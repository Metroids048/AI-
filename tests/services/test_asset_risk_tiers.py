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
            position_rules={"risk_per_trade": 0.01, "max_leverage": 5, "max_position_fraction": 0.05},
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
    assert core.leverage == 25
    assert core.max_position_fraction == 0.20
    assert standard.tier == "standard"
    assert standard.leverage == 15
    assert standard.max_position_fraction == 0.09


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
    assert btc.leverage == 20
    assert pepe.tier == "vol_high"
    assert pepe.leverage == 6
    assert pepe.max_position_fraction == 0.05


def test_resolve_falls_back_to_core_standard_without_dynamic_tiers() -> None:
    tiers = default_asset_risk_tiers()
    assert resolve_asset_risk_tier("BTC/USDT", tiers).tier == "core"
    assert resolve_asset_risk_tier("PEPE/USDT", tiers).tier == "standard"


def test_atr_pct_from_daily_bars_needs_enough_history() -> None:
    bars = [
        {"high": 110 + i, "low": 100 + i, "close": 105 + i}
        for i in range(20)
    ]
    assert atr_pct_from_daily_bars(bars[:5]) is None
    value = atr_pct_from_daily_bars(bars)
    assert value is not None and value > 0


def test_tier_position_fraction_caps_notional_without_multiplying_leverage(db_session) -> None:
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

    # Tier defaults were bumped moderately more aggressive per operator request
    # (core 20x/0.15 -> 25x/0.20, standard 10x/0.06 -> 15x/0.09); expected notional
    # scales with the new max_position_fraction caps.
    assert core_leverage == 25
    assert core_notional == 2_000
    assert standard_notional == 900


def test_scale_asset_risk_tiers_tracks_operator_sliders() -> None:
    scaled = scale_asset_risk_tiers(
        default_asset_risk_tiers(),
        max_leverage=5,
        max_symbol_exposure=0.10,
    )

    assert scaled["core"]["leverage"] == 5
    assert scaled["core"]["max_position_fraction"] == 0.10
    assert scaled["standard"]["leverage"] == 2.5
    assert scaled["standard"]["max_position_fraction"] == 0.04
    # Symbol assignments from the source tiers must survive the rescale.
    assert scaled["core"]["symbols"] == list(default_asset_risk_tiers()["core"]["symbols"])


def test_scale_asset_risk_tiers_preserves_dynamic_volatility_buckets() -> None:
    dynamic = build_volatility_asset_risk_tiers({"BTC/USDT": 0.01, "PEPE/USDT": 0.09})

    scaled = scale_asset_risk_tiers(dynamic, max_leverage=10, max_symbol_exposure=0.20)

    assert scaled["vol_low"]["leverage"] == 7.5
    assert scaled["vol_high"]["leverage"] == 2.0
    assert scaled["vol_low"]["symbols"] == dynamic["vol_low"]["symbols"]


def test_scale_asset_risk_tiers_falls_back_to_defaults_when_missing() -> None:
    scaled = scale_asset_risk_tiers(None, max_leverage=8, max_symbol_exposure=0.12)

    assert scaled["core"]["leverage"] == 8
    assert scaled["standard"]["leverage"] == 4


def test_medium_risk_profile_allows_core_tier_but_keeps_hard_limits() -> None:
    profile = medium_risk_profile()

    assert profile.max_leverage == 25
    assert profile.max_symbol_exposure == 0.20
    assert profile.max_total_exposure == 0.60
    assert profile.max_open_positions == 6
    assert profile.daily_loss_limit == 0.06
    assert profile.hard_stop_drawdown_limit == 0.22
