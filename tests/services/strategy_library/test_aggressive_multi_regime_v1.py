from __future__ import annotations

from decimal import Decimal

from services.strategy_library.candidates.range_sweep_reversion_v1 import RangeSweepConfig
from services.strategy_library.candidates.trend_pullback_v2 import TrendPullbackConfig
from services.strategy_library.ensemble.selector_v2 import CandidateSelectorV2
from services.strategy_library.v2_projection import project_single_target, risk_per_trade_for_score


def test_selector_rejects_score_below_production_floor() -> None:
    from tests.services.strategy_library.test_selector_v2 import _proposal

    proposal = _proposal("low", side="long", setup_quality=0.1).model_copy(
        update={
            "regime_fit": 0.1,
            "cost_adjusted_rr": Decimal("0.2"),
            "confidence_components": {"volume": 0.1},
        }
    )
    result = CandidateSelectorV2().select([proposal], now=proposal.signal_bar_time)
    assert result.status == "NO_TRADE"
    assert result.selected is None


def test_score_risk_tiers_are_frozen() -> None:
    assert risk_per_trade_for_score(Decimal("0.58")) == Decimal("0.02")
    assert risk_per_trade_for_score(Decimal("0.66")) == Decimal("0.04")
    assert risk_per_trade_for_score(Decimal("0.74")) == Decimal("0.07")
    assert risk_per_trade_for_score(Decimal("0.82")) == Decimal("0.10")


def test_projection_chooses_largest_fraction_then_farthest_target() -> None:
    from tests.services.strategy_library.test_selector_v2 import _proposal

    proposal = _proposal("projection", side="long")
    projected = project_single_target(proposal)
    assert projected.primary_target_price == Decimal("110")
    assert projected.stop_distance == Decimal("5")


def test_projection_rejects_directionally_invalid_geometry() -> None:
    from tests.services.strategy_library.test_selector_v2 import _proposal

    proposal = _proposal("invalid", side="long").model_copy(
        update={"invalidation": _proposal("invalid2", side="short").invalidation}
    )
    with __import__("pytest").raises(ValueError, match="directional"):
        project_single_target(proposal)


def test_selector_conflict_margin_is_point_zero_eight() -> None:
    assert CandidateSelectorV2().conflict_margin == 0.08


def test_strategy_family_initial_parameters_match_frozen_contract() -> None:
    trend = TrendPullbackConfig()
    range_config = RangeSweepConfig()
    assert trend.minimum_trend_score == 0.55
    assert trend.maximum_entry_distance_atr == Decimal("2.5")
    assert range_config.minimum_range_score == 0.55
    assert range_config.maximum_expansion_score == 0.65
