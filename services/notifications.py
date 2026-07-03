"""Notification outbox intents for operational visibility."""

from __future__ import annotations

from datetime import UTC, datetime

from shared.models import NotificationOutboxItem, RiskEvent


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
                subject=f"{severity.upper()} risk event: {event.event_type}",
                body=event.description,
                source_ref=event_id,
                created_at=event.occurred_at or datetime.now(UTC),
            )
        )
    return items
