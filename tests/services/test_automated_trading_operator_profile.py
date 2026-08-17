"""V2 operator execution-profile precedence contracts (S-201/S-202)."""

from __future__ import annotations

from decimal import Decimal

from services.automated_trading.application.operator_profile import resolve_v2_execution_settings


def test_operator_profile_overrides_v2_runtime_defaults_without_asset_tiers() -> None:
    """S-202: saved profile values beat stale static V2 runtime limits."""
    settings = resolve_v2_execution_settings(
        "BTC/USDT",
        {
            "max_leverage": 7,
            "risk_per_trade": 0.012,
            "order_notional_usdt": 123,
            "max_symbol_exposure": 0.11,
            "simulation_sampling_fallback_enabled": False,
        },
    )

    assert settings.max_leverage == 7
    assert settings.risk_per_trade == Decimal("0.012")
    assert settings.order_notional_usdt == Decimal("123")
    assert settings.max_position_fraction == Decimal("0.11")
    assert settings.max_margin_fraction == Decimal("0.05")
    assert settings.sampling_fallback_enabled is False


def test_operator_asset_tier_has_precedence_over_profile_max_leverage() -> None:
    """S-202: a symbol-specific tier overrides the profile-wide leverage cap."""
    settings = resolve_v2_execution_settings(
        "BTC/USDT",
        {
            "max_leverage": 20,
            "max_symbol_exposure": 0.35,
            "asset_risk_tiers": {
                "core": {
                    "tier": "core",
                    "symbols": ["BTC/USDT"],
                    "leverage": 6,
                    "max_position_fraction": 0.10,
                }
            },
        },
    )

    assert settings.max_leverage == 6
    assert settings.max_position_fraction == Decimal("0.10")
    assert settings.max_margin_fraction == Decimal("0.05")


def test_symbol_tier_resolves_per_symbol_risk_and_margin_ceiling() -> None:
    settings = resolve_v2_execution_settings(
        "ETH/USDT",
        {
            "risk_per_trade": 0.01,
            "max_leverage": 50,
            "max_margin_fraction": 0.05,
            "max_symbol_exposure": 2.5,
            "asset_risk_tiers": {
                "symbol_eth": {
                    "tier": "symbol_eth",
                    "symbols": ["ETH/USDT"],
                    "risk_per_trade": 0.004,
                    "max_leverage": 15,
                    "max_margin_fraction": 0.015,
                    "max_position_fraction": 0.60,
                }
            },
        },
    )

    assert settings.risk_per_trade == Decimal("0.004")
    assert settings.max_leverage == 15
    assert settings.max_margin_fraction == Decimal("0.015")
    assert settings.max_position_fraction == Decimal("0.60")


def test_high_volatility_multiplier_reduces_resolved_sizing_inputs() -> None:
    profile = {
        "risk_per_trade": 0.005,
        "max_leverage": 20,
        "max_margin_fraction": 0.02,
        "max_symbol_exposure": 0.60,
        "asset_risk_tiers": {
            "symbol_btc": {
                "tier": "symbol_btc",
                "symbols": ["BTC/USDT"],
                "risk_per_trade": 0.005,
                "max_leverage": 20,
                "max_margin_fraction": 0.02,
                "max_position_fraction": 0.60,
            }
        },
        "volatility_risk_tiers": {
            "high": {"tier": "high", "symbols": ["BTC/USDT"], "multiplier": 0.50},
        },
    }
    low = resolve_v2_execution_settings("BTC/USDT", {**profile, "volatility_risk_tiers": {}})
    high = resolve_v2_execution_settings("BTC/USDT", profile)

    assert high.risk_per_trade == Decimal("0.0025")
    assert high.max_leverage == 10
    assert high.max_margin_fraction == Decimal("0.01")
    assert high.max_position_fraction == Decimal("0.30")
    assert high.risk_per_trade < low.risk_per_trade
    assert high.max_leverage < low.max_leverage

    settings = resolve_v2_execution_settings(
        "XRP/USDT",
        {
            "max_leverage": 50,
            "max_symbol_exposure": 2.5,
            "max_margin_fraction": 0.25,
            "risk_per_trade": 0.01,
        },
    )

    assert settings.max_leverage == 50
    assert settings.max_position_fraction == Decimal("2.5")
    assert settings.max_margin_fraction == Decimal("0.05")
