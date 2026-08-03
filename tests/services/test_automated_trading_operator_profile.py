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
