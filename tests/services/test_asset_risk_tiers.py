from decimal import Decimal

from services.data import DataRepository
from services.execution.paper_signal import PaperSignalGenerator
from services.execution.risk_tiers import (
    atr_pct_from_daily_bars,
    build_volatility_asset_risk_tiers,
    default_asset_risk_tiers,
    resolve_asset_risk_tier,
    scale_asset_risk_tiers,
)
from shared.models import PaperRun, StrategyContract, StrategyRules
from shared.models.risk import medium_risk_profile


def _strategy() -> StrategyContract:
    return StrategyContract(
        strategy_id="tiered-risk-strategy",
        strategy_key="tiered-risk-strategy",
        source="test",
        core_thesis="risk tier sizing",
        market="crypto_perp",
        timeframe="1h",
        rules=StrategyRules(
            stoploss_rules={"fixed_bps": 250},
            takeprofit_rules={"risk_reward": 2.5},
            position_rules={"risk_per_trade": 0.01, "max_leverage": 50, "max_position_fraction": 2.50},
        ),
    )


def _paper_run() -> PaperRun:
    return PaperRun(
        strategy_id="tiered-risk-strategy",
        execution_profile={
            "account_equity": 10_000,
            "asset_risk_tiers": default_asset_risk_tiers(),
        },
    )


def test_default_asset_risk_tiers_separate_core_and_standard_symbols() -> None:
    tiers = default_asset_risk_tiers()

    core = resolve_asset_risk_tier("BTC/USDT", tiers)
    standard = resolve_asset_risk_tier("XRP/USDT", tiers)

    assert core.tier == "core"
    assert core.leverage == 50
    assert core.max_position_fraction == 2.50
    assert standard.tier == "standard"
    assert standard.leverage == 50
    assert standard.max_position_fraction == 2.50


def test_dynamic_volatility_tiers_preferred_when_present() -> None:
    tiers = build_volatility_asset_risk_tiers(
        {
            "BTC/USDT": 0.01,
            "ETH/USDT": 0.015,
            "SOL/USDT": 0.02,
            "LINK/USDT": 0.03,
            "AVAX/USDT": 0.035,
            "DOGE/USDT": 0.06,
            "PEPE/USDT": 0.09,
            "ENA/USDT": 0.08,
            "ONDO/USDT": 0.04,
        }
    )

    btc = resolve_asset_risk_tier("BTC/USDT", tiers)
    pepe = resolve_asset_risk_tier("PEPE/USDT", tiers)

    assert btc.tier == "vol_low"
    assert btc.leverage == 50
    assert pepe.tier == "vol_high"
    assert pepe.leverage == 50
    assert pepe.max_position_fraction == 2.50


def test_resolve_falls_back_to_core_standard_without_dynamic_tiers() -> None:
    tiers = default_asset_risk_tiers()
    assert resolve_asset_risk_tier("BTC/USDT", tiers).tier == "core"
    assert resolve_asset_risk_tier("PEPE/USDT", tiers).tier == "standard"


def test_atr_pct_from_daily_bars_needs_enough_history() -> None:
    bars = [{"high": 110 + i, "low": 100 + i, "close": 105 + i} for i in range(20)]
    assert atr_pct_from_daily_bars(bars[:5]) is None
    value = atr_pct_from_daily_bars(bars)
    assert value is not None and value > 0


def test_legacy_paper_signal_path_keeps_its_own_notional_cap(db_session) -> None:
    """The frozen legacy path is not the V2 Binance entry-sizing authority."""
    paper_run = _paper_run()
    strategy = _strategy()
    generator = PaperSignalGenerator(data_repo=DataRepository(db_session))

    core_leverage = PaperSignalGenerator._requested_leverage(
        strategy=strategy,
        paper_run=paper_run,
        symbol="BTC/USDT",
    )
    core_notional = generator._requested_notional(
        strategy=strategy,
        paper_run=paper_run,
        symbol="BTC/USDT",
        requested_leverage=core_leverage,
        reference_price=Decimal("100"),
        stoploss_price=Decimal("97.5"),
    )
    standard_notional = generator._requested_notional(
        strategy=strategy,
        paper_run=paper_run,
        symbol="XRP/USDT",
        requested_leverage=10,
        reference_price=Decimal("1"),
        stoploss_price=Decimal("0.975"),
    )

    assert core_leverage == 50
    assert core_notional == 4_000
    assert standard_notional == 4_000


def test_scale_asset_risk_tiers_tracks_operator_sliders() -> None:
    scaled = scale_asset_risk_tiers(
        default_asset_risk_tiers(),
        max_leverage=5,
        max_symbol_exposure=0.10,
    )

    assert scaled["core"]["leverage"] == 5
    assert scaled["core"]["max_position_fraction"] == 0.10
    assert scaled["standard"]["leverage"] == 5
    assert scaled["standard"]["max_position_fraction"] == 0.10
    # Symbol assignments from the source tiers must survive the rescale.
    assert scaled["core"]["symbols"] == list(default_asset_risk_tiers()["core"]["symbols"])


def test_scale_asset_risk_tiers_preserves_dynamic_volatility_buckets() -> None:
    dynamic = build_volatility_asset_risk_tiers({"BTC/USDT": 0.01, "PEPE/USDT": 0.09})

    scaled = scale_asset_risk_tiers(dynamic, max_leverage=10, max_symbol_exposure=0.20)

    assert scaled["vol_low"]["leverage"] == 10
    assert scaled["vol_high"]["leverage"] == 10
    assert scaled["vol_low"]["max_position_fraction"] == 0.20
    assert scaled["vol_high"]["max_position_fraction"] == 0.20
    assert scaled["vol_low"]["symbols"] == dynamic["vol_low"]["symbols"]


def test_scale_asset_risk_tiers_falls_back_to_defaults_when_missing() -> None:
    scaled = scale_asset_risk_tiers(None, max_leverage=8, max_symbol_exposure=0.12)

    assert scaled["core"]["leverage"] == 8
    assert scaled["standard"]["leverage"] == 8


def test_medium_risk_profile_allows_core_tier_but_keeps_hard_limits() -> None:
    profile = medium_risk_profile()

    assert profile.max_leverage == 30.0
    assert profile.max_symbol_exposure == 1.50
    assert profile.max_total_exposure == 7.50
    assert profile.max_open_positions == 5
    assert profile.daily_loss_limit == 0.20
    assert profile.hard_stop_drawdown_limit == 0.40


def test_all_execution_symbols_resolve_within_directional_ceiling() -> None:
    tiers = default_asset_risk_tiers()
    for symbol in ("BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "BNB/USDT"):
        resolved = resolve_asset_risk_tier(symbol, tiers)
        assert resolved.leverage <= 50
        assert resolved.max_position_fraction <= 2.50


def test_oversized_slider_inputs_are_capped_for_every_tier() -> None:
    dynamic = build_volatility_asset_risk_tiers(
        {"BTC/USDT": 0.01, "ETH/USDT": 0.02, "SOL/USDT": 0.03, "XRP/USDT": 0.04, "BNB/USDT": 0.05}
    )
    scaled = scale_asset_risk_tiers(dynamic, max_leverage=50, max_symbol_exposure=2.5)
    for tier in scaled.values():
        assert tier["leverage"] <= 50
        assert tier["max_position_fraction"] <= 2.50
    for symbol in ("BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "BNB/USDT"):
        resolved = resolve_asset_risk_tier(symbol, scaled)
        assert resolved.leverage <= 50
        assert resolved.max_position_fraction <= 2.50
