"""V2 adapter for the existing operator-owned execution profile.

The profile is saved by the existing PaperRun API.  V2 consumes that contract
directly rather than inventing a parallel configuration store or falling back
to static limits when an operator value is present.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import ROUND_DOWN, Decimal
from typing import Any

from services.execution.risk_tiers import (
    cap_directional_leverage,
    cap_directional_position_fraction,
    resolve_asset_risk_tier,
    resolve_volatility_adjustment,
)
from shared.models.risk import PAPER_RUNTIME_LIMITS, TESTNET_CANARY_RUNTIME_CONTRACT, TESTNET_CANARY_SYMBOLS

_ONE = Decimal("1")


@dataclass(frozen=True)
class V2ExecutionSettings:
    """Resolved, per-symbol V2 settings from one operator execution profile."""

    risk_per_trade: Decimal
    max_leverage: int
    max_margin_fraction: Decimal
    order_notional_usdt: Decimal | None
    max_position_fraction: Decimal
    max_open_positions: int
    max_total_exposure: Decimal
    sampling_fallback_enabled: bool
    active_snapshot_config: dict[str, Any] | None = None
    active_snapshot_hash: str | None = None
    volatility_multiplier: Decimal = Decimal("1")
    volatility_no_new_entry: bool = False
    # E-003 / volatility recommendations remain observable when the Canary
    # contract overrides their enforcement for sampling.
    tier_recommended_leverage: int | None = None
    tier_would_reduce: bool = False
    tier_enforced: bool = True
    volatility_enforced: bool = True
    canary_contract_applied: bool = False


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
    fallback_margin_fraction = _decimal(PAPER_RUNTIME_LIMITS["max_margin_fraction"])
    fallback_exposure = _decimal(PAPER_RUNTIME_LIMITS["max_symbol_exposure"])
    fallback_open_positions = int(PAPER_RUNTIME_LIMITS["max_open_positions"])
    fallback_total_exposure = _decimal(PAPER_RUNTIME_LIMITS["max_total_exposure"])
    profile_exposure = _decimal(
        cap_directional_position_fraction(
            float(profile["max_symbol_exposure"]) if "max_symbol_exposure" in profile else float(fallback_exposure)
        )
    )

    if has_tiers:
        tier = resolve_asset_risk_tier(symbol, tiers)
        leverage = _decimal(tier.leverage)
        # E-003: an explicit operator leverage must still be able to tighten a tier.
        # Previously the tier won outright, so lowering the profile-wide slider left
        # the higher tier leverage in force.
        if "max_leverage" in profile:
            leverage = min(
                leverage,
                _decimal(cap_directional_leverage(float(profile["max_leverage"]))),
            )
        max_position_fraction = min(_decimal(tier.max_position_fraction), profile_exposure)
    else:
        leverage = _decimal(
            cap_directional_leverage(
                float(profile["max_leverage"]) if "max_leverage" in profile else float(fallback_leverage)
            )
        )
        max_position_fraction = profile_exposure

    order_notional = _decimal(profile["order_notional_usdt"]) if "order_notional_usdt" in profile else None
    risk_per_trade = (
        _decimal(profile["risk_per_trade"])
        if "risk_per_trade" in profile
        else _decimal(PAPER_RUNTIME_LIMITS["risk_per_trade"])
    )
    max_margin_fraction = min(
        _decimal(profile["max_margin_fraction"]) if "max_margin_fraction" in profile else fallback_margin_fraction,
        _decimal("0.05"),
    )
    max_open_positions = int(profile.get("max_open_positions", fallback_open_positions))
    max_total_exposure = _decimal(profile.get("max_total_exposure", fallback_total_exposure))

    # E-003: a symbol tier may only tighten the resolved envelope. Each optional
    # per-symbol ceiling is intersected with the profile-wide value, so adding a
    # tier can never raise risk above what the operator profile already allows.
    if has_tiers:
        tier_ceilings = resolve_asset_risk_tier(symbol, tiers)
        if tier_ceilings.risk_per_trade is not None:
            risk_per_trade = min(risk_per_trade, _decimal(tier_ceilings.risk_per_trade))
        if tier_ceilings.max_leverage is not None:
            leverage = min(leverage, _decimal(cap_directional_leverage(float(tier_ceilings.max_leverage))))
        if tier_ceilings.max_margin_fraction is not None:
            max_margin_fraction = min(max_margin_fraction, _decimal(tier_ceilings.max_margin_fraction))

    # Preserve the tier recommendation before volatility applies its separate
    # multiplier; Canary reports this recommendation without enforcing it.
    tier_recommended_leverage = int(leverage)

    # E-003: volatility adjustment scales the resolved ceilings downward only.
    multiplier, no_new_entry = resolve_volatility_adjustment(symbol, profile.get("volatility_risk_tiers"))
    if multiplier < _ONE:
        risk_per_trade *= multiplier
        max_margin_fraction *= multiplier
        max_position_fraction *= multiplier
        leverage = max(_ONE, (leverage * multiplier).to_integral_value(rounding=ROUND_DOWN))

    return V2ExecutionSettings(
        risk_per_trade=risk_per_trade,
        max_leverage=int(leverage),
        max_margin_fraction=max_margin_fraction,
        order_notional_usdt=order_notional,
        max_position_fraction=max_position_fraction,
        max_open_positions=max_open_positions,
        max_total_exposure=max_total_exposure,
        sampling_fallback_enabled=bool(profile.get("simulation_sampling_fallback_enabled", False)),
        volatility_multiplier=multiplier,
        volatility_no_new_entry=no_new_entry,
        tier_recommended_leverage=tier_recommended_leverage,
    )


def apply_testnet_canary_runtime_contract(
    settings: V2ExecutionSettings,
    *,
    symbol: str,
    execution_mode: str,
    entry_authority: str,
    candidate_lane: str | None = None,
) -> V2ExecutionSettings:
    """Apply the one authoritative sizing contract after authority resolution.

    E-003 tiers and volatility remain fully calculated on the base settings.  In
    the exact Testnet Canary sampling scope they become diagnostic recommendations
    while the fixed five-symbol contract owns the effective values.  Production
    callers receive the original settings unchanged.
    """
    mode_value = getattr(execution_mode, "value", execution_mode)
    authority_value = getattr(entry_authority, "value", entry_authority)
    if (
        str(mode_value) != str(TESTNET_CANARY_RUNTIME_CONTRACT["execution_mode"])
        or str(authority_value) != str(TESTNET_CANARY_RUNTIME_CONTRACT["entry_authority"])
        or symbol not in TESTNET_CANARY_SYMBOLS
        or (
            candidate_lane is not None
            and str(getattr(candidate_lane, "value", candidate_lane))
            != str(TESTNET_CANARY_RUNTIME_CONTRACT["candidate_lane"])
        )
    ):
        return settings

    contract_leverage = int(TESTNET_CANARY_RUNTIME_CONTRACT["max_leverage"])
    contract_margin = _decimal(TESTNET_CANARY_RUNTIME_CONTRACT["max_margin_fraction"])
    contract_exposure = _decimal(TESTNET_CANARY_RUNTIME_CONTRACT["max_symbol_exposure"])
    contract_risk = _decimal(TESTNET_CANARY_RUNTIME_CONTRACT["risk_per_trade"])
    return replace(
        settings,
        risk_per_trade=contract_risk,
        max_leverage=contract_leverage,
        max_margin_fraction=contract_margin,
        max_position_fraction=contract_exposure,
        max_open_positions=int(TESTNET_CANARY_RUNTIME_CONTRACT["max_open_positions"]),
        max_total_exposure=_decimal(TESTNET_CANARY_RUNTIME_CONTRACT["max_total_exposure"]),
        tier_would_reduce=(
            settings.tier_recommended_leverage is not None and settings.tier_recommended_leverage < contract_leverage
        ),
        tier_enforced=False,
        volatility_enforced=False,
        canary_contract_applied=True,
    )
