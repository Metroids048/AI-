from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from services.strategy_library.exit.adaptive_exit import build_adaptive_exit
from services.strategy_library.proposals import EntryTrigger, InvalidationRule, StrategyProposal, TargetRule


def _proposal(side: str = "long") -> StrategyProposal:
    entry = Decimal("100")
    stop = Decimal("95") if side == "long" else Decimal("105")
    targets = (
        TargetRule(
            label="tp1", price=Decimal("105") if side == "long" else Decimal("95"), quantity_fraction=Decimal("0.35")
        ),
        TargetRule(
            label="tp2", price=Decimal("109") if side == "long" else Decimal("91"), quantity_fraction=Decimal("0.40")
        ),
        TargetRule(
            label="runner",
            price=Decimal("112.5") if side == "long" else Decimal("87.5"),
            quantity_fraction=Decimal("0.25"),
        ),
    )
    signal_time = datetime(2026, 1, 4, tzinfo=UTC)
    return StrategyProposal(
        proposal_id=f"p:{side}",
        strategy_id="trend_pullback_v2",
        strategy_version="2.0.0-research",
        symbol="BTC/USDT",
        side=side,  # type: ignore[arg-type]
        setup_type="test",
        signal_bar_time=signal_time,
        expires_at=signal_time + timedelta(minutes=30),
        entry_trigger=EntryTrigger(
            entry_type="market_after_confirmation", reference_price=entry, max_price_drift_bps=Decimal("20")
        ),
        invalidation=InvalidationRule(stop_price=stop, extreme_price=stop, reason="test"),
        targets=targets,
        regime_fit=0.8,
        setup_quality=0.8,
        cost_adjusted_rr=Decimal("1.5"),
        confidence_components={},
        feature_snapshot_hash="a" * 64,
        reasons=("test",),
    )


def test_adaptive_exit_creates_partial_targets_from_actual_fill() -> None:
    plan = build_adaptive_exit(_proposal(), filled_price=Decimal("101"), atr=Decimal("2"))

    assert plan.initial_stop == Decimal("95")
    assert [target.quantity_fraction for target in plan.targets] == [Decimal("0.35"), Decimal("0.40"), Decimal("0.25")]
    assert plan.targets[0].price == Decimal("107")
    assert plan.targets[1].price == Decimal("111.8")
    assert plan.trailing_activation_r == Decimal("1.8")
    assert plan.time_exit_bars == 8


def test_adaptive_exit_is_directionally_symmetric() -> None:
    plan = build_adaptive_exit(_proposal("short"), filled_price=Decimal("99"), atr=Decimal("2"))

    assert plan.initial_stop == Decimal("105")
    assert plan.targets[0].price == Decimal("93")
    assert plan.targets[1].price == Decimal("88.2")


def test_adaptive_exit_rejects_fill_that_invalidates_stop_geometry() -> None:
    import pytest

    with pytest.raises(ValueError, match="must stay above the long stop"):
        build_adaptive_exit(_proposal(), filled_price=Decimal("94"), atr=Decimal("2"))
