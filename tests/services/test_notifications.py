from __future__ import annotations

from datetime import UTC, datetime, timedelta

from services.notifications import NotificationDispatcherService, NotificationDispatchResult
from services.strategy_library import NotificationRepository
from shared.models import NotificationOutboxItem


class StubAdapter:
    def __init__(self, *, should_fail: bool = False, failure_kind: str = "transient") -> None:
        self.should_fail = should_fail
        self.failure_kind = failure_kind
        self.calls: list[str] = []

    def send(self, item: NotificationOutboxItem) -> NotificationDispatchResult:
        self.calls.append(item.notification_id)
        if self.should_fail:
            return NotificationDispatchResult(
                channel="stub",
                success=False,
                external_ref=None,
                error_message="adapter failure",
                failure_kind=self.failure_kind,
            )
        return NotificationDispatchResult(
            channel="stub",
            success=True,
            external_ref=f"sent:{item.notification_id}",
            error_message=None,
            failure_kind=None,
        )


def _item(notification_id: str, *, status: str = "pending_adapter") -> NotificationOutboxItem:
    return NotificationOutboxItem(
        notification_id=notification_id,
        event_type="risk_event",
        severity="high",
        channel_group="ops",
        delivery_channels=["stub"],
        subject="ops alert",
        body="investigate risk event",
        source_ref="risk-1",
        delivery_status=status,
    )


def test_dispatch_marks_notification_sent_and_is_idempotent(db_session) -> None:
    repo = NotificationRepository(db_session)
    repo.create_notification(_item("risk:telegram"))
    adapter = StubAdapter()
    dispatcher = NotificationDispatcherService(
        repository=repo,
        adapters={"stub": adapter},
        now_factory=lambda: datetime(2026, 7, 4, tzinfo=UTC),
    )

    result = dispatcher.dispatch_due_notifications(limit=10)

    assert result.dispatched == 1
    sent = repo.get_notification("risk:telegram")
    assert sent is not None
    assert sent.delivery_status == "sent"
    assert sent.delivery_attempts == 1
    assert sent.delivered_at is not None
    assert sent.last_attempt_at is not None
    assert len(sent.attempt_history) == 1
    assert sent.attempt_history[0]["channels"][0]["status"] == "sent"
    assert adapter.calls == ["risk:telegram"]

    rerun = dispatcher.dispatch_due_notifications(limit=10)
    assert rerun.dispatched == 0
    assert adapter.calls == ["risk:telegram"]


def test_dispatch_transient_failure_schedules_retry(db_session) -> None:
    repo = NotificationRepository(db_session)
    repo.create_notification(_item("risk:retry"))
    adapter = StubAdapter(should_fail=True, failure_kind="transient")
    now = datetime(2026, 7, 4, tzinfo=UTC)
    dispatcher = NotificationDispatcherService(
        repository=repo,
        adapters={"stub": adapter},
        now_factory=lambda: now,
    )

    result = dispatcher.dispatch_due_notifications(limit=10)

    assert result.dispatched == 1
    failed = repo.get_notification("risk:retry")
    assert failed is not None
    assert failed.delivery_status == "pending_retry"
    assert failed.delivery_attempts == 1
    assert failed.next_attempt_at == now + timedelta(minutes=5)
    assert failed.delivered_at is None
    assert failed.attempt_history[0]["channels"][0]["status"] == "failed"
    assert failed.attempt_history[0]["channels"][0]["failure_kind"] == "transient"


def test_dispatch_skips_notifications_not_due_yet(db_session) -> None:
    repo = NotificationRepository(db_session)
    repo.create_notification(
        _item("risk:not-due").model_copy(
            update={
                "delivery_status": "pending_retry",
                "next_attempt_at": datetime(2026, 7, 4, 0, 10, tzinfo=UTC),
            }
        )
    )
    adapter = StubAdapter()
    dispatcher = NotificationDispatcherService(
        repository=repo,
        adapters={"stub": adapter},
        now_factory=lambda: datetime(2026, 7, 4, 0, 0, tzinfo=UTC),
    )

    result = dispatcher.dispatch_due_notifications(limit=10)

    assert result.dispatched == 0
    assert adapter.calls == []
