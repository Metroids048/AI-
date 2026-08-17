"""E-003 volatility-aware per-symbol risk contracts.

The assertions below are deliberately end-to-end through the real resolver and the
real single sizing authority (`_calculate_quantity`). Proving that a config value
differs is not enough: a volatility tier only counts as effective when the final
notional actually shrinks under identical equity and stop geometry.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from services.automated_trading.application.cycle_service import CycleRequest, _calculate_quantity
from services.automated_trading.application.decision_service import TimeframeView
from services.automated_trading.application.operator_profile import resolve_v2_execution_settings
from services.automated_trading.domain.enums import V2ExecutionMode
from services.automated_trading.infrastructure.market_snapshot_provider import AuthoritativeAccountSnapshot
from services.execution.risk_tiers import (
    SYMBOL_RISK_BASE_CEILINGS,
    symbol_risk_base_tiers,
)

EQUITY = Decimal("7000")
REFERENCE_PRICE = Decimal("63700")
STOP_DISTANCE = Decimal("223")  # ~0.35%, the geometry observed on 2026-08-16

# The permissive profile that was actually active on 2026-08-16.
LEGACY_ACTIVE_PROFILE = {
    "risk_per_trade": 0.01,
    "max_leverage": 50,
    "max_margin_fraction": 0.05,
    "max_symbol_exposure": 2.50,
}


def _profile(**overrides: object) -> dict[str, object]:
    return {**LEGACY_ACTIVE_PROFILE, "asset_risk_tiers": symbol_risk_base_tiers(), **overrides}


def _snapshot() -> AuthoritativeAccountSnapshot:
    return AuthoritativeAccountSnapshot(
        balance=EQUITY,
        equity=EQUITY,
        positions=[],
        pending_orders=[],
        snapshot_timestamp=datetime.now(UTC),
    )


def _notional(symbol: str, profile: dict[str, object]) -> Decimal:
    settings = resolve_v2_execution_settings(symbol, profile)
    request = CycleRequest(
        cycle_id="e003-test-cycle",
        symbol=symbol,
        timeframe="15m",
        entry_timeframe=TimeframeView(timeframe="15m", bars=()),
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        engine_activation="ACTIVE",
        fencing_token="e003-token",
        now=datetime.now(UTC),
        risk_per_trade=settings.risk_per_trade,
        max_leverage=settings.max_leverage,
        max_margin_fraction=settings.max_margin_fraction,
        max_position_fraction=settings.max_position_fraction,
    )
    return _calculate_quantity(
        request,
        _snapshot(),
        stop_distance=STOP_DISTANCE,
        reference_price=REFERENCE_PRICE,
    )


@pytest.mark.parametrize(
    ("symbol", "risk", "leverage", "margin"),
    [
        ("BTC/USDT", Decimal("0.005"), 20, Decimal("0.020")),
        ("ETH/USDT", Decimal("0.004"), 15, Decimal("0.015")),
        ("BNB/USDT", Decimal("0.0035"), 12, Decimal("0.0125")),
        ("SOL/USDT", Decimal("0.0025"), 10, Decimal("0.010")),
        ("XRP/USDT", Decimal("0.0025"), 10, Decimal("0.010")),
    ],
)
def test_symbol_base_ceilings_resolve_from_persisted_tiers(
    symbol: str, risk: Decimal, leverage: int, margin: Decimal
) -> None:
    settings = resolve_v2_execution_settings(symbol, _profile())

    assert settings.risk_per_trade <= risk
    assert settings.max_leverage <= leverage
    assert settings.max_margin_fraction <= margin


def test_symbol_ceilings_tighten_the_legacy_permissive_profile() -> None:
    legacy = resolve_v2_execution_settings("BTC/USDT", LEGACY_ACTIVE_PROFILE)
    tightened = resolve_v2_execution_settings("BTC/USDT", _profile())

    assert legacy.risk_per_trade == Decimal("0.01")
    assert legacy.max_leverage == 50
    assert tightened.risk_per_trade < legacy.risk_per_trade
    assert tightened.max_leverage < legacy.max_leverage
    assert tightened.max_position_fraction < legacy.max_position_fraction


def test_volatility_multiplier_orders_resolved_risk_low_mid_high() -> None:
    def resolved(tier: str, multiplier: float) -> Decimal:
        profile = _profile(
            volatility_risk_tiers={tier: {"tier": tier, "symbols": ["BTC/USDT"], "multiplier": multiplier}}
        )
        return resolve_v2_execution_settings("BTC/USDT", profile).risk_per_trade

    low = resolved("low", 1.00)
    mid = resolved("mid", 0.75)
    high = resolved("high", 0.50)

    assert high < mid < low


def test_high_volatility_reduces_final_notional_not_only_config() -> None:
    """The multiplier must move the real sizing output, not just a config field."""
    low = _notional("BTC/USDT", _profile())
    mid = _notional(
        "BTC/USDT",
        _profile(volatility_risk_tiers={"mid": {"tier": "mid", "symbols": ["BTC/USDT"], "multiplier": 0.75}}),
    )
    high = _notional(
        "BTC/USDT",
        _profile(volatility_risk_tiers={"high": {"tier": "high", "symbols": ["BTC/USDT"], "multiplier": 0.50}}),
    )

    assert high < mid < low


def test_shock_tier_flags_no_new_entry_without_touching_ceilings_upward() -> None:
    settings = resolve_v2_execution_settings(
        "SOL/USDT",
        _profile(
            volatility_risk_tiers={
                "shock": {"tier": "shock", "symbols": ["SOL/USDT"], "multiplier": 0.25, "no_new_entry": True}
            }
        ),
    )

    assert settings.volatility_no_new_entry is True
    assert settings.risk_per_trade <= Decimal("0.0025") * Decimal("0.25")


def test_missing_volatility_data_becomes_shock_no_entry() -> None:
    from services.execution.risk_tiers import build_volatility_risk_tiers

    tiers = build_volatility_risk_tiers(
        {"BTC/USDT": 0.01, "ETH/USDT": 0.02, "SOL/USDT": 0.03},
        required_symbols=("BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT"),
    )

    assert tiers["shock"]["symbols"] == ["BNB/USDT", "XRP/USDT"]
    assert tiers["shock"]["multiplier"] == 0.25
    assert tiers["shock"]["no_new_entry"] is True

    settings = resolve_v2_execution_settings(
        "BTC/USDT",
        _profile(risk_per_trade=0.001, max_leverage=5, max_margin_fraction=0.005),
    )

    assert settings.risk_per_trade == Decimal("0.001")
    assert settings.max_leverage == 5
    assert settings.max_margin_fraction == Decimal("0.005")


def test_symbol_tier_can_never_raise_risk_above_operator_ceiling() -> None:
    permissive_tier = {
        "symbol_btc": {
            "tier": "symbol_btc",
            "symbols": ["BTC/USDT"],
            "risk_per_trade": 0.05,
            "max_leverage": 125,
            "max_margin_fraction": 0.9,
            "max_position_fraction": 5,
        }
    }
    settings = resolve_v2_execution_settings(
        "BTC/USDT",
        {**LEGACY_ACTIVE_PROFILE, "asset_risk_tiers": permissive_tier},
    )

    assert settings.risk_per_trade <= Decimal("0.01")
    assert settings.max_leverage <= 50
    assert settings.max_margin_fraction <= Decimal("0.05")


def test_ceilings_never_exceed_the_authorized_validation_bounds() -> None:
    for symbol, bounds in SYMBOL_RISK_BASE_CEILINGS.items():
        settings = resolve_v2_execution_settings(symbol, _profile())
        assert settings.risk_per_trade <= Decimal(str(bounds["risk_per_trade"]))
        assert settings.max_leverage <= bounds["max_leverage"]
        assert settings.max_margin_fraction <= Decimal(str(bounds["max_margin_fraction"]))
