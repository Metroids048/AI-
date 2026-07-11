"""Symbol-level leverage and notional caps for simulation-first execution."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from services.data.universe import exchange_to_platform_symbol
from shared.models import AssetRiskTierSettings

CORE_SYMBOLS = ("BTC/USDT", "ETH/USDT", "SOL/USDT")


def default_asset_risk_tiers() -> dict[str, dict[str, Any]]:
    return {
        "core": AssetRiskTierSettings(
            tier="core",
            symbols=list(CORE_SYMBOLS),
            leverage=10,
            max_position_fraction=0.15,
        ).model_dump(mode="json"),
        "standard": AssetRiskTierSettings(
            tier="standard",
            symbols=[],
            leverage=5,
            max_position_fraction=0.06,
        ).model_dump(mode="json"),
    }


def resolve_asset_risk_tier(
    symbol: str,
    tiers: Mapping[str, Any] | None = None,
) -> AssetRiskTierSettings:
    configured = tiers or default_asset_risk_tiers()
    normalized = exchange_to_platform_symbol(symbol).replace(":USDT", "")
    fallback: AssetRiskTierSettings | None = None
    for tier_name, raw in configured.items():
        payload = raw.model_dump(mode="json") if isinstance(raw, AssetRiskTierSettings) else dict(raw)
        payload.setdefault("tier", tier_name)
        tier = AssetRiskTierSettings.model_validate(payload)
        if tier.tier == "standard" or not tier.symbols:
            fallback = tier
        if normalized in {exchange_to_platform_symbol(item).replace(":USDT", "") for item in tier.symbols}:
            return tier
    return fallback or AssetRiskTierSettings(
        tier="standard",
        leverage=5,
        max_position_fraction=0.06,
    )
