"""V2 adapter for the existing operator-owned execution profile.

The profile is saved by the existing PaperRun API.  V2 consumes that contract
directly rather than inventing a parallel configuration store or falling back
to static limits when an operator value is present.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from services.execution.risk_tiers import resolve_asset_risk_tier
from shared.models.risk import PAPER_RUNTIME_LIMITS


@dataclass(frozen=True)
class V2ExecutionSettings:
    """Resolved, per-symbol V2 settings from one operator execution profile."""

    risk_per_trade: Decimal
    max_leverage: int
    order_notional_usdt: Decimal | None
    max_position_fraction: Decimal
    sampling_fallback_enabled: bool


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def resolve_v2_execution_settings(symbol: str, execution_profile: Mapping[str, Any] | None) -> V2ExecutionSettings:
    """Apply the frozen operator-profile precedence for one V2 symbol.

    Asset tiers override the profile-wide leverage and impose their own
    position-fraction ceiling.  A profile-wide exposure cap remains a further
    ceiling.  Static ``PAPER_RUNTIME_LIMITS`` are defaults only when the
    corresponding operator field is absent.
    """
    profile: Mapping[str, Any] = execution_profile or {}
    tiers = profile.get("asset_risk_tiers")
    has_tiers = isinstance(tiers, Mapping) and bool(tiers)

    fallback_leverage = _decimal(PAPER_RUNTIME_LIMITS["max_leverage"])
    fallback_exposure = _decimal(PAPER_RUNTIME_LIMITS["max_symbol_exposure"])
    profile_exposure = (
        _decimal(profile["max_symbol_exposure"]) if "max_symbol_exposure" in profile else fallback_exposure
    )

    if has_tiers:
        tier = resolve_asset_risk_tier(symbol, tiers)
        leverage = _decimal(tier.leverage)
        max_position_fraction = min(_decimal(tier.max_position_fraction), profile_exposure)
    else:
        leverage = _decimal(profile["max_leverage"]) if "max_leverage" in profile else fallback_leverage
        max_position_fraction = profile_exposure

    order_notional = _decimal(profile["order_notional_usdt"]) if "order_notional_usdt" in profile else None
    risk_per_trade = (
        _decimal(profile["risk_per_trade"])
        if "risk_per_trade" in profile
        else _decimal(PAPER_RUNTIME_LIMITS["risk_per_trade"])
    )

    return V2ExecutionSettings(
        risk_per_trade=risk_per_trade,
        max_leverage=int(leverage),
        order_notional_usdt=order_notional,
        max_position_fraction=max_position_fraction,
        sampling_fallback_enabled=bool(profile.get("simulation_sampling_fallback_enabled", False)),
    )
