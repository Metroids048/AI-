from datetime import UTC, datetime
from decimal import Decimal

from services.agents.telegram_kol.domain.messages import MessageEnvelope
from services.agents.telegram_kol.integration.candidate_adapter import KolCandidateAdapter
from services.agents.telegram_kol.parsing.parser import UniversalKolParser
from services.agents.telegram_kol.shadow.market_sanity import MarketSnapshot, check_market_sanity
from services.automated_trading.domain.enums import V2CandidateType


def _event(text: str):
    now = datetime(2026, 8, 23, tzinfo=UTC)
    return UniversalKolParser().parse(
        MessageEnvelope(
            source_id="fei-yang",
            chat_id="-1",
            message_id=100,
            revision=0,
            posted_at=now,
            received_at=now,
            text=text,
        )
    )


def test_market_sanity_rejects_missed_range_and_non_execution_symbol() -> None:
    event = _event("ZEC 多 820-825 SL795 TP860")
    snapshot = MarketSnapshot(
        symbol="ZEC/USDT",
        price=Decimal("900"),
        spread_bps=Decimal("3"),
        observed_at=datetime(2026, 8, 23, tzinfo=UTC),
    )

    result = check_market_sanity(event, snapshot=snapshot, now=snapshot.observed_at)

    assert result.accepted is False
    assert result.reason_code == "SYMBOL_NOT_EXECUTION_ELIGIBLE"


def test_candidate_adapter_uses_relative_geometry_and_sampling_lane() -> None:
    event = _event("BTC 多 77000 SL75000 TP79000")
    adapter = KolCandidateAdapter()
    now = datetime(2026, 8, 23, tzinfo=UTC)

    candidate = adapter.to_candidate(event, cycle_id="cycle-1", now=now, thread_id="thread-1")

    assert candidate is not None
    assert candidate.candidate_type is V2CandidateType.SAMPLING
    assert candidate.non_promotable is True
    assert candidate.stop_distance == Decimal("2000")
    assert candidate.take_profit_distance == Decimal("2000")
    context = dict(candidate.signal_context)
    assert context["signal_source"] == "telegram_kol"
    assert context["thread_id"] == "thread-1"
    assert candidate.candidate_id == "telegram:fei-yang:thread-1:100:0"


def test_candidate_adapter_rejects_multi_tp_without_collapsing_targets() -> None:
    event = _event("BTC 多 77000 SL75000 TP79000/81500")

    assert KolCandidateAdapter().to_candidate(event, cycle_id="cycle-1", now=datetime(2026, 8, 23, tzinfo=UTC)) is None
