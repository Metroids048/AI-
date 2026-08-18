"""Contract guard tests — enforce the data-contract-first principle."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from shared.models import (
    AutoTradingSettings,
    BacktestEngine,
    BacktestReport,
    ExecutionRiskState,
    FailureRecord,
    MarketEvent,
    MarketIntelligenceFeatureSnapshot,
    MarketIntelligenceSignal,
    MetaLabel,
    OHLCVBar,
    RiskEvent,
    RiskEventType,
    RiskProfile,
    RiskSeverity,
    SignalEnsemble,
    TradeSide,
)
from shared.models.trading import ConfigSnapshot, canonical_config_hash


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
    assert profile.max_symbol_exposure == 0.10
    assert profile.max_total_exposure == 0.50
    assert profile.max_leverage == 3.0
    assert profile.hard_stop_drawdown_limit == 0.20
    assert profile.consecutive_loss_limit == 4
    assert profile.api_failure_limit == 3
    assert profile.api_failure_window_minutes == 10


def test_auto_trading_settings_defaults_accept_canary_runtime_contract() -> None:
    settings = AutoTradingSettings()

    assert settings.max_leverage == 30.0
    assert settings.risk_per_trade == 0.10
    assert settings.max_margin_fraction == 0.05
    assert settings.max_symbol_exposure == 1.50
    assert settings.max_total_exposure == 7.50
    assert settings.max_open_positions == 5
    assert settings.max_symbols == 5
    assert set(settings.asset_risk_tiers["core"].symbols) == {
        "BTC/USDT",
        "ETH/USDT",
        "SOL/USDT",
        "XRP/USDT",
        "BNB/USDT",
    }

    explicit = AutoTradingSettings(max_total_exposure=7.5)
    assert explicit.max_total_exposure == 7.5


def test_execution_risk_state_defaults() -> None:
    state = ExecutionRiskState(account_equity=10_000, equity_peak=10_500)
    assert state.daily_realized_pnl == 0.0
    assert state.open_positions == 0
    assert state.requested_leverage == 1.0


def test_failure_record_can_attach_to_strategy_or_idea_but_not_neither() -> None:
    strategy_failure = FailureRecord(
        strategy_id="strategy-1",
        origin_run_type="paper",
        origin_run_id="paper-1",
        failure_type="risk_gate_reject",
        failure_summary="risk rejected",
    )
    idea_failure = FailureRecord(
        idea_id="idea-1",
        origin_run_type="research_intake",
        origin_run_id="idea-1",
        failure_type="alpha_evaluator_reject",
        failure_summary="unsupported stock field",
    )
    assert strategy_failure.strategy_id == "strategy-1"
    assert idea_failure.idea_id == "idea-1"
    with pytest.raises(ValidationError):
        FailureRecord(
            origin_run_type="research_intake",
            origin_run_id="unknown",
            failure_type="alpha_evaluator_reject",
            failure_summary="missing subject",
        )


def test_risk_profile_orm_defaults_match_shared_contract() -> None:
    from services.strategy_library.models import RiskProfile as RiskProfileRow

    profile = RiskProfile()
    assert RiskProfileRow.max_symbol_exposure.default.arg == profile.max_symbol_exposure
    assert RiskProfileRow.max_total_exposure.default.arg == profile.max_total_exposure
    assert RiskProfileRow.consecutive_loss_limit.default.arg == profile.consecutive_loss_limit
    assert RiskProfileRow.api_failure_limit.default.arg == profile.api_failure_limit
    assert RiskProfileRow.api_failure_window_minutes.default.arg == profile.api_failure_window_minutes


def test_signal_ensemble_and_meta_label_minimal() -> None:
    ensemble = SignalEnsemble(ensemble_id="e1", strategy_refs=["s1"], fused_direction=TradeSide.LONG)
    meta = MetaLabel(meta_label_id="m1", ensemble_id=ensemble.ensemble_id)
    assert ensemble.ensemble_status == "formed"
    assert meta.bet_decision == "pending"


def test_market_intelligence_contracts_cap_vote_weight() -> None:
    event = MarketEvent(event_id="event-1", source="jinshi", event_type="news", title="ETF approved")
    snapshot = MarketIntelligenceFeatureSnapshot(symbol="BTC/USDT", generated_at=event.occurred_at or datetime.now())
    signal = MarketIntelligenceSignal(
        symbol=snapshot.symbol,
        generated_at=snapshot.generated_at,
        long_probability=0.72,
        short_probability=0.28,
        confidence=0.8,
        direction=TradeSide.LONG,
        vote_weight=0.30,
    )

    assert event.event_type == "news"
    assert snapshot.symbol == "BTC/USDT"
    assert signal.vote_weight == 0.30
    with pytest.raises(ValidationError):
        MarketIntelligenceSignal(
            symbol="BTC/USDT",
            generated_at=snapshot.generated_at,
            long_probability=0.7,
            short_probability=0.3,
            confidence=0.8,
            vote_weight=0.31,
        )


# ---------------------------------------------------------------------------
# ConfigSnapshot — hash stability, secret rejection, unit disambiguation
# ---------------------------------------------------------------------------


def test_config_snapshot_hash_is_deterministic() -> None:
    """Same config dict must always produce the same hash, regardless of key order."""
    cfg_a = {"risk_fraction": 0.0025, "max_leverage": 3, "timeframe": "15m"}
    cfg_b = {"timeframe": "15m", "max_leverage": 3, "risk_fraction": 0.0025}
    assert canonical_config_hash(cfg_a) == canonical_config_hash(cfg_b)


def test_config_snapshot_hash_changes_on_value_change() -> None:
    cfg1 = {"risk_fraction": 0.0025}
    cfg2 = {"risk_fraction": 0.005}
    assert canonical_config_hash(cfg1) != canonical_config_hash(cfg2)


def test_config_snapshot_create_sets_hash_automatically() -> None:
    cfg = {"risk_fraction": 0.0025, "max_leverage": 3}
    snapshot = ConfigSnapshot.create(
        paper_run_id="run-1",
        config=cfg,
        created_by="test",
        effective_cycle_id="cycle-1",
    )
    assert snapshot.config_hash == canonical_config_hash(cfg)
    assert snapshot.config_hash.startswith("sha256:")


def test_config_snapshot_rejects_mismatched_hash() -> None:
    cfg = {"risk_fraction": 0.0025}
    with pytest.raises(ValidationError, match="config_hash does not match"):
        ConfigSnapshot(
            paper_run_id="run-1",
            config=cfg,
            config_hash="sha256:wrong",
            created_by="test",
            effective_cycle_id="cycle-1",
        )


def test_config_snapshot_rejects_secrets() -> None:
    """A config dict containing API credentials must never be persisted."""
    for secret_key in ("api_key", "secret", "password", "token"):
        with pytest.raises(ValidationError, match="secret"):
            ConfigSnapshot.create(
                paper_run_id="run-1",
                config={secret_key: "s3cr3t", "risk_fraction": 0.0025},
                created_by="test",
                effective_cycle_id="cycle-1",
            )


def test_config_snapshot_unit_disambiguation() -> None:
    """0.5%, 0.005 and 0.5 stored as separate configs must produce distinct hashes."""
    half_percent_fraction = canonical_config_hash({"risk_fraction": 0.005})
    point_five = canonical_config_hash({"risk_fraction": 0.5})
    half_pct_string = canonical_config_hash({"risk_fraction": "0.5%"})
    assert len({half_percent_fraction, point_five, half_pct_string}) == 3
