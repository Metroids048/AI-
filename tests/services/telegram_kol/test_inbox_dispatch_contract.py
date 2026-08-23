from datetime import UTC, datetime
from decimal import Decimal

from services.agents.telegram_kol.integration.inbox import CandidateInbox, InboxItem
from services.agents.telegram_kol.integration.v2_dispatcher import TelegramV2Dispatcher


def test_candidate_inbox_is_idempotent_by_candidate_key() -> None:
    inbox = CandidateInbox()
    item = InboxItem(
        candidate_key="telegram:source:thread:1:0",
        source_id="source",
        thread_id="thread",
        symbol="BTC/USDT",
        payload={"side": "LONG"},
        created_at=datetime(2026, 8, 23, tzinfo=UTC),
    )

    assert inbox.put(item) is True
    assert inbox.put(item) is False
    assert len(inbox.pending()) == 1


def test_dispatcher_requires_writer_authority_and_marks_dispatched() -> None:
    inbox = CandidateInbox()
    item = InboxItem(
        candidate_key="telegram:source:thread:2:0",
        source_id="source",
        thread_id="thread",
        symbol="BTC/USDT",
        payload={"side": "LONG", "stop_distance": str(Decimal("100"))},
        created_at=datetime(2026, 8, 23, tzinfo=UTC),
    )
    inbox.put(item)
    calls: list[str] = []
    dispatcher = TelegramV2Dispatcher(
        inbox=inbox,
        writer_authority=lambda: True,
        submit=lambda payload: calls.append(payload["side"]),
    )

    result = dispatcher.dispatch_once()

    assert result == {"dispatched": 1, "blocked": 0}
    assert calls == ["LONG"]
    assert inbox.pending() == []
