from datetime import UTC, datetime
from decimal import Decimal

from services.agents.telegram_kol.domain.events import (
    Completeness,
    EntrySemantics,
    KolEventType,
)
from services.agents.telegram_kol.domain.messages import MessageEnvelope
from services.agents.telegram_kol.lifecycle.thread_engine import TradeThreadEngine
from services.agents.telegram_kol.parsing.parser import UniversalKolParser
from services.agents.telegram_kol.parsing.validator import validate_event


def envelope(text: str, *, message_id: int = 1, reply_to: int | None = None) -> MessageEnvelope:
    now = datetime(2026, 8, 23, tzinfo=UTC)
    return MessageEnvelope(
        source_id="fei-yang",
        chat_id="-1001",
        message_id=message_id,
        revision=0,
        posted_at=now,
        received_at=now,
        text=text,
        reply_to_message_id=reply_to,
    )


def test_parser_preserves_range_entry_and_multiple_targets() -> None:
    event = UniversalKolParser().parse(envelope("BTC 多 77000-77300 SL75000 TP79000/81500"))

    assert event.event_type is KolEventType.OPEN
    assert event.entry_semantics is EntrySemantics.RANGE
    assert event.entry_low == Decimal("77000")
    assert event.entry_high == Decimal("77300")
    assert event.stop_loss == Decimal("75000")
    assert event.take_profits == (Decimal("79000"), Decimal("81500"))
    assert event.completeness is Completeness.COMPLETE


def test_parser_classifies_management_events_and_commentary() -> None:
    parser = UniversalKolParser()

    partial = parser.parse(envelope("BTC 止盈一半", message_id=2))
    add = parser.parse(envelope("TRB 加一次仓", message_id=3))
    commentary = parser.parse(envelope("BTC 还有机会回踩", message_id=4))

    assert partial.event_type is KolEventType.PARTIAL_CLOSE
    assert partial.close_fraction == Decimal("0.5")
    assert add.event_type is KolEventType.ADD_POSITION
    assert commentary.event_type is KolEventType.COMMENTARY
    assert commentary.completeness is Completeness.NON_SIGNAL


def test_parser_emits_ambiguous_for_direction_conflict() -> None:
    event = UniversalKolParser().parse(envelope("BTC 多空都有机会，现价77000"))

    assert event.event_type is KolEventType.AMBIGUOUS
    assert "DIRECTION_CONFLICT" in event.tags


def test_parser_covers_management_event_taxonomy() -> None:
    assert UniversalKolParser().parse(envelope("BTC 取消进场")).event_type is KolEventType.CANCEL_ENTRY
    assert UniversalKolParser().parse(envelope("BTC 设置止盈 TP79000/81500")).event_type is KolEventType.SET_TAKE_PROFIT
    assert UniversalKolParser().parse(envelope("BTC 结果 盈利 2R")).event_type is KolEventType.RESULT_REPORT


def test_missing_stop_is_fail_closed() -> None:
    event = UniversalKolParser().parse(envelope("ETH 现价多 3500", message_id=5))

    result = validate_event(event, now=datetime(2026, 8, 23, tzinfo=UTC))

    assert result.accepted is False
    assert result.reason_code == "MISSING_STOP"
    assert event.completeness is Completeness.INCOMPLETE


def test_thread_engine_rejects_ambiguous_active_threads() -> None:
    engine = TradeThreadEngine()
    first = UniversalKolParser().parse(envelope("BTC 多 77000 SL75000 TP79000", message_id=10))
    second = UniversalKolParser().parse(envelope("BTC 空 78000 SL79000 TP76000", message_id=11))
    update = UniversalKolParser().parse(envelope("止盈一半", message_id=12))

    engine.apply(first, envelope("BTC 多 77000 SL75000 TP79000", message_id=10))
    engine.apply(second, envelope("BTC 空 78000 SL79000 TP76000", message_id=11))
    linked = engine.link(update, envelope("止盈一半", message_id=12))

    assert linked.reason_code == "AMBIGUOUS_THREAD"
    assert linked.thread is None
