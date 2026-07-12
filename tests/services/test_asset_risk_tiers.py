from decimal import Decimal

from services.execution.paper_signal import PaperSignalGenerator
from services.execution.risk_tiers import default_asset_risk_tiers, resolve_asset_risk_tier
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
    assert core.leverage == 20
    assert core.max_position_fraction == 0.15
    assert standard.tier == "standard"
    assert standard.leverage == 10
    assert standard.max_position_fraction == 0.06


def test_tier_position_fraction_caps_notional_without_multiplying_leverage() -> None:
    paper_run = _paper_run()
    strategy = _strategy()

    core_leverage = PaperSignalGenerator._requested_leverage(
        strategy=strategy,
        paper_run=paper_run,
        symbol="BTC/USDT",
    )
    core_notional = PaperSignalGenerator._requested_notional(
        strategy=strategy,
        paper_run=paper_run,
        symbol="BTC/USDT",
        requested_leverage=core_leverage,
        reference_price=Decimal("100"),
        stoploss_price=Decimal("97.5"),
    )
    standard_notional = PaperSignalGenerator._requested_notional(
        strategy=strategy,
        paper_run=paper_run,
        symbol="XRP/USDT",
        requested_leverage=10,
        reference_price=Decimal("1"),
        stoploss_price=Decimal("0.975"),
    )

    assert core_leverage == 20
    assert core_notional == 1_500
    assert standard_notional == 600


def test_medium_risk_profile_allows_core_tier_but_keeps_hard_limits() -> None:
    profile = medium_risk_profile()

    assert profile.max_leverage == 20
    assert profile.max_symbol_exposure == 0.15
    assert profile.max_total_exposure == 0.50
    assert profile.max_open_positions == 5
    assert profile.daily_loss_limit == 0.05
    assert profile.hard_stop_drawdown_limit == 0.20
