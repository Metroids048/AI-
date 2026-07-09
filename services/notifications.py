"""Notification outbox intent creation, adapter dispatch, and retry state."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

import httpx
from sqlalchemy.orm import Session

from services.strategy_library import NotificationRepository
from shared.config import settings
from shared.models import NotificationOutboxItem, RiskEvent


@dataclass(slots=True)
class NotificationDispatchResult:
    channel: str
    success: bool
    external_ref: str | None = None
    error_message: str | None = None
    failure_kind: str | None = None


@dataclass(slots=True)
class NotificationDispatchSummary:
    dispatched: int = 0


class NotificationAdapter(Protocol):
    def send(self, item: NotificationOutboxItem) -> NotificationDispatchResult: ...


class TelegramNotificationAdapter:
    def __init__(self, *, bot_token: str, channel_ids: list[str], client: httpx.Client | None = None) -> None:
        self.bot_token = bot_token
        self.channel_ids = [channel_id.strip() for channel_id in channel_ids if channel_id.strip()]
        self.client = client or httpx.Client(timeout=10.0)

    def send(self, item: NotificationOutboxItem) -> NotificationDispatchResult:
        if not self.bot_token or not self.channel_ids:
            return NotificationDispatchResult(
                channel="telegram",
                success=False,
                error_message="telegram adapter not configured",
                failure_kind="permanent",
            )
        external_refs: list[str] = []
        for channel_id in self.channel_ids:
            response = self.client.post(
                f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
                json={"chat_id": channel_id, "text": f"{item.subject}\n\n{item.body}"},
            )
            response.raise_for_status()
            payload = response.json()
            external_refs.append(str(payload.get("result", {}).get("message_id", channel_id)))
        return NotificationDispatchResult(
            channel="telegram",
            success=True,
            external_ref=",".join(external_refs),
        )


class WebhookNotificationAdapter:
    def __init__(self, *, webhook_url: str, client: httpx.Client | None = None) -> None:
        self.webhook_url = webhook_url.strip()
        self.client = client or httpx.Client(timeout=10.0)

    def send(self, item: NotificationOutboxItem) -> NotificationDispatchResult:
        if not self.webhook_url:
            return NotificationDispatchResult(
                channel="webhook",
                success=False,
                error_message="webhook adapter not configured",
                failure_kind="permanent",
            )
        response = self.client.post(
            self.webhook_url,
            json={
                "notification_id": item.notification_id,
                "event_type": item.event_type,
                "severity": item.severity,
                "subject": item.subject,
                "body": item.body,
                "source_ref": item.source_ref,
            },
        )
        response.raise_for_status()
        return NotificationDispatchResult(channel="webhook", success=True, external_ref=self.webhook_url)


def risk_event_notifications(events: list[RiskEvent]) -> list[NotificationOutboxItem]:
    """Convert high-severity risk events into pending notification intents."""

    items: list[NotificationOutboxItem] = []
    for event in events:
        severity = str(event.severity)
        if severity not in {"high", "critical"}:
            continue
        event_id = event.risk_event_id or f"risk-{len(items) + 1}"
        items.append(
            NotificationOutboxItem(
                notification_id=f"risk:{event_id}",
                event_type="risk_event",
                severity=severity,
                channel_group="ops",
                delivery_channels=["telegram", "webhook"],
                subject=f"{severity.upper()} risk event: {event.event_type}",
                body=event.description,
                source_ref=event_id,
                created_at=event.occurred_at or datetime.now(UTC),
            )
        )
    return items


class NotificationOutboxService:
    """Persist notification intents without invoking external adapters."""

    def __init__(self, session: Session):
        self.repository = NotificationRepository(session)

    def enqueue_risk_event_notification(self, event: RiskEvent) -> NotificationOutboxItem | None:
        items = risk_event_notifications([event])
        if not items:
            return None
        return self.repository.create_notification(items[0])


class NotificationDispatcherService:
    """Deliver due notification intents through configured adapters."""

    def __init__(
        self,
        *,
        repository: NotificationRepository,
        adapters: dict[str, NotificationAdapter] | None = None,
        now_factory: Callable[[], datetime] | None = None,
        max_attempts: int | None = None,
        base_delay_seconds: int | None = None,
    ) -> None:
        self.repository = repository
        self.adapters = adapters or self._default_adapters()
        self.now_factory = now_factory or (lambda: datetime.now(UTC))
        self.max_attempts = max_attempts or settings.notification_dispatch_max_attempts
        self.base_delay_seconds = base_delay_seconds or settings.notification_dispatch_base_delay_seconds

    def dispatch_due_notifications(self, *, limit: int = 20) -> NotificationDispatchSummary:
        summary = NotificationDispatchSummary()
        items = self.repository.list_notifications(only_due=True, limit=limit)
        for item in items:
            if self.dispatch_notification(item.notification_id):
                summary.dispatched += 1
        return summary

    def dispatch_notification(self, notification_id: str) -> bool:
        item = self.repository.get_notification(notification_id)
        if item is None or item.delivery_status == "sent":
            return False
        now = self.now_factory()
        if item.next_attempt_at is not None and item.next_attempt_at > now:
            return False

        attempt_number = item.delivery_attempts + 1
        channel_results: list[dict[str, str | bool | None]] = []
        success = True
        transient_failure = False
        errors: list[str] = []

        for channel in item.delivery_channels:
            adapter = self.adapters.get(channel)
            if adapter is None:
                result = NotificationDispatchResult(
                    channel=channel,
                    success=False,
                    error_message=f"{channel} adapter not configured",
                    failure_kind="permanent",
                )
            else:
                try:
                    result = adapter.send(item)
                except Exception as exc:  # pragma: no cover - safety net around third-party IO
                    result = NotificationDispatchResult(
                        channel=channel,
                        success=False,
                        error_message=str(exc),
                        failure_kind="transient",
                    )

            success = success and result.success
            if not result.success and result.failure_kind == "transient":
                transient_failure = True
            if result.error_message:
                errors.append(f"{result.channel}: {result.error_message}")
            channel_results.append(
                {
                    "channel": result.channel,
                    "status": "sent" if result.success else "failed",
                    "external_ref": result.external_ref,
                    "failure_kind": result.failure_kind,
                    "error_message": result.error_message,
                }
            )

        history_entry = {
            "attempt": attempt_number,
            "attempted_at": now.isoformat(),
            "channels": channel_results,
        }
        history = [*item.attempt_history, history_entry]
        if success:
            self.repository.update_notification(
                notification_id,
                delivery_status="sent",
                delivery_attempts=attempt_number,
                next_attempt_at=None,
                last_attempt_at=now,
                delivered_at=now,
                last_error=None,
                attempt_history=history,
            )
            return True

        next_attempt_at = None
        delivery_status = "failed"
        if transient_failure and attempt_number < self.max_attempts:
            delay_seconds = self.base_delay_seconds * (2 ** (attempt_number - 1))
            next_attempt_at = now + timedelta(seconds=delay_seconds)
            delivery_status = "pending_retry"

        self.repository.update_notification(
            notification_id,
            delivery_status=delivery_status,
            delivery_attempts=attempt_number,
            next_attempt_at=next_attempt_at,
            last_attempt_at=now,
            last_error="; ".join(errors) if errors else None,
            attempt_history=history,
        )
        return True

    @staticmethod
    def _default_adapters() -> dict[str, NotificationAdapter]:
        channel_ids = [item.strip() for item in settings.telegram_channel_ids.split(",") if item.strip()]
        return {
            "telegram": TelegramNotificationAdapter(
                bot_token=settings.telegram_bot_token,
                channel_ids=channel_ids,
            ),
            "webhook": WebhookNotificationAdapter(webhook_url=settings.notification_webhook_url),
        }
