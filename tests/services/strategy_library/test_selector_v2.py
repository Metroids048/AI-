from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from services.strategy_library.ensemble.selector_v2 import CandidateSelectorV2
from services.strategy_library.proposals import EntryTrigger, InvalidationRule, StrategyProposal, TargetRule

NOW = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)


def _proposal(
    proposal_id: str,
    *,
    side: str,
    setup_quality: float = 0.8,
    expires_at: datetime | None = None,
) -> StrategyProposal:
    entry = Decimal("100")
    stop = Decimal("95") if side == "long" else Decimal("105")
    target = Decimal("110") if side == "long" else Decimal("90")
    return StrategyProposal(
        proposal_id=proposal_id,
        strategy_id="test",
        strategy_version="1",
        symbol="BTC/USDT",
        side=side,  # type: ignore[arg-type]
        setup_type="test",
        signal_bar_time=NOW - timedelta(minutes=15),
        expires_at=expires_at or NOW + timedelta(minutes=15),
        entry_trigger=EntryTrigger(
            entry_type="market_after_confirmation", reference_price=entry, max_price_drift_bps=Decimal("20")
        ),
        invalidation=InvalidationRule(stop_price=stop, extreme_price=stop, reason="test"),
        targets=(TargetRule(label="tp", price=target, quantity_fraction=Decimal("1")),),
        regime_fit=0.8,
        setup_quality=setup_quality,
        cost_adjusted_rr=Decimal("1.5"),
        confidence_components={"volume": 0.8},
        feature_snapshot_hash="a" * 64,
        reasons=("test",),
    )


def test_selector_never_averages_opposing_directions() -> None:
    result = CandidateSelectorV2().select([_proposal("long", side="long"), _proposal("short", side="short")], now=NOW)

    assert result.selected is None
    assert result.status == "CONFLICT"
    assert set(result.rejected_reasons.values()) == {"opposing_direction_conflict"}


def test_selector_chooses_materially_stronger_direction_without_mutating_it() -> None:
    long = _proposal("long", side="long", setup_quality=0.95)
    short = _proposal("short", side="short", setup_quality=0.40)

    result = CandidateSelectorV2().select([long, short], now=NOW)

    assert result.status == "SELECTED"
    assert result.selected == long
    assert result.selected.entry_trigger.reference_price == Decimal("100")
    assert result.supporting_proposal_ids == ("long",)
    assert result.rejected_reasons == {"short": "opposing_direction_lost"}


def test_selector_ignores_expired_proposals_and_keeps_same_side_attribution() -> None:
    winner = _proposal("winner", side="long", setup_quality=0.9)
    support = _proposal("support", side="long", setup_quality=0.7)
    expired = _proposal("expired", side="short", expires_at=NOW)

    result = CandidateSelectorV2().select([winner, support, expired], now=NOW)

    assert result.selected == winner
    assert result.supporting_proposal_ids == ("winner", "support")
    assert result.rejected_reasons["expired"] == "expired"
