"""Contract guard tests — enforce the data-contract-first principle."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from shared.models import (
    BacktestEngine,
    BacktestReport,
    MetaLabel,
    OHLCVBar,
    RiskEvent,
    RiskEventType,
    RiskProfile,
    RiskSeverity,
    SignalEnsemble,
    TradeSide,
)


def test_ohlcv_bar_roundtrip(sample_ohlcv_bar: dict) -> None:
    bar = OHLCVBar(**sample_ohlcv_bar)
    restored = OHLCVBar.model_validate_json(bar.model_dump_json())
    assert restored.symbol == "BTC/USDT"
    assert restored.timestamp == bar.timestamp


def test_ohlcv_bar_rejects_high_below_low(sample_ohlcv_bar: dict) -> None:
    bad = {**sample_ohlcv_bar, "high": Decimal("100"), "low": Decimal("200")}
    with pytest.raises(ValidationError):
        OHLCVBar(**bad)


def test_ohlcv_bar_rejects_negative(sample_ohlcv_bar: dict) -> None:
    with pytest.raises(ValidationError):
        OHLCVBar(**{**sample_ohlcv_bar, "volume": Decimal("-1")})


def test_ohlcv_bar_forbids_extra_fields(sample_ohlcv_bar: dict) -> None:
    with pytest.raises(ValidationError):
        OHLCVBar(**{**sample_ohlcv_bar, "unexpected": 1})


def test_backtest_report_minimal() -> None:
    report = BacktestReport(
        strategy_id="s1",
        engine=BacktestEngine.FREQTRADE,
        sharpe=1.5,
        profit_factor=1.4,
        max_drawdown=0.18,
        win_rate=0.55,
        expectancy=0.2,
    )
    assert report.engine == "freqtrade"


def test_risk_event_defaults() -> None:
    event = RiskEvent(
        event_type=RiskEventType.NEWS_RISK,
        severity=RiskSeverity.HIGH,
        source="jinshi",
        description="test",
    )
    assert event.resolution_status == "detected"


def test_risk_profile_defaults() -> None:
    profile = RiskProfile()
    assert profile.max_leverage == 3.0
    assert profile.hard_stop_drawdown_limit == 0.20


def test_signal_ensemble_and_meta_label_minimal() -> None:
    ensemble = SignalEnsemble(ensemble_id="e1", strategy_refs=["s1"], fused_direction=TradeSide.LONG)
    meta = MetaLabel(meta_label_id="m1", ensemble_id=ensemble.ensemble_id)
    assert ensemble.ensemble_status == "formed"
    assert meta.bet_decision == "pending"
