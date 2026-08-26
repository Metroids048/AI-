from decimal import Decimal

from services.automated_trading.application.operator_profile import (
    apply_testnet_canary_runtime_contract,
    resolve_v2_execution_settings,
)
from services.automated_trading.domain.portfolio_risk import (
    RiskExposure,
    evaluate_portfolio_risk,
)


def _base_settings():
    return resolve_v2_execution_settings(
        "BTC/USDT",
        {
            "risk_per_trade": 0.10,
            "max_leverage": 50,
            "max_margin_fraction": 0.05,
            "max_symbol_exposure": 2.5,
        },
    )


def test_canary_contract_allows_two_independent_positions_without_raising_symbol_cap():
    settings = apply_testnet_canary_runtime_contract(
        _base_settings(),
        symbol="BTC/USDT",
        execution_mode="BINANCE_TESTNET",
        entry_authority="TESTNET_CANARY",
        candidate_lane="TESTNET_SAMPLING",
    )

    assert settings.max_open_positions == 2
    assert settings.max_total_exposure == Decimal("0.02")
    assert settings.max_position_fraction == Decimal("0.01")
    assert settings.order_notional_usdt == Decimal("50.0")


def test_canary_contract_does_not_change_production_or_mainnet_settings():
    base = _base_settings()
    production = apply_testnet_canary_runtime_contract(
        base,
        symbol="BTC/USDT",
        execution_mode="BINANCE_TESTNET",
        entry_authority="PRODUCTION",
        candidate_lane="PRODUCTION",
    )
    mainnet = apply_testnet_canary_runtime_contract(
        base,
        symbol="BTC/USDT",
        execution_mode="BINANCE_MAINNET",
        entry_authority="TESTNET_CANARY",
        candidate_lane="TESTNET_SAMPLING",
    )

    assert production == base
    assert mainnet == base


def test_two_open_canary_positions_block_third_but_one_open_allows_other_symbol():
    one_open = [RiskExposure("ETH/USDT", "long", Decimal("1"), source="position")]
    two_open = one_open + [RiskExposure("BTC/USDT", "long", Decimal("1"), source="position")]

    allowed = evaluate_portfolio_risk(
        equity=Decimal("10000"),
        candidate_symbol="BTC/USDT",
        candidate_direction="long",
        candidate_initial_risk_usdt=Decimal("1"),
        committed=one_open,
        max_open_positions=2,
        max_total_fraction=Decimal("0.02"),
        max_cluster_fraction=Decimal("0.02"),
    )
    blocked = evaluate_portfolio_risk(
        equity=Decimal("10000"),
        candidate_symbol="SOL/USDT",
        candidate_direction="long",
        candidate_initial_risk_usdt=Decimal("1"),
        committed=two_open,
        max_open_positions=2,
        max_total_fraction=Decimal("0.02"),
        max_cluster_fraction=Decimal("0.02"),
    )

    assert allowed.allowed
    assert blocked.reason_code == "MAX_OPEN_EXPOSURES"
