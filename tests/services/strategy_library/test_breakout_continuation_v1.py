from __future__ import annotations

from decimal import Decimal

from services.strategy_library.candidates.breakout_continuation_v1 import BreakoutContinuationConfig


def test_breakout_config_has_frozen_initial_geometry() -> None:
    config = BreakoutContinuationConfig()
    assert config.structure_lookback == 20
    assert config.minimum_volume_ratio == Decimal("1.25")
    assert config.minimum_body_range_ratio == Decimal("0.55")
    assert config.minimum_compression_score == 0.30
    assert config.minimum_expansion_score == 0.35
