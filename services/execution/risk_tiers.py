"""Symbol-level leverage and notional caps for simulation-first execution."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from services.data.universe import exchange_to_platform_symbol
from shared.models import AssetRiskTierSettings

CORE_SYMBOLS = ("BTC/USDT", "ETH/USDT", "SOL/USDT")
VOLATILITY_TIER_NAMES = ("vol_low", "vol_mid", "vol_high")

# Proposed defaults (operator can override via execution_profile): higher vol → tighter caps.
VOLATILITY_TIER_DEFAULTS: dict[str, dict[str, float]] = {
    "vol_low": {"leverage": 15.0, "max_position_fraction": 0.12},
    "vol_mid": {"leverage": 8.0, "max_position_fraction": 0.06},
    "vol_high": {"leverage": 4.0, "max_position_fraction": 0.03},
}


def default_asset_risk_tiers() -> dict[str, dict[str, Any]]:
    return {
        "core": AssetRiskTierSettings(
            tier="core",
            symbols=list(CORE_SYMBOLS),
            leverage=20,
            max_position_fraction=0.15,
        ).model_dump(mode="json"),
        "standard": AssetRiskTierSettings(
            tier="standard",
            symbols=[],
            leverage=10,
            max_position_fraction=0.06,
        ).model_dump(mode="json"),
    }


def _normalize_symbol(symbol: str) -> str:
    return exchange_to_platform_symbol(symbol).replace(":USDT", "")


def _has_dynamic_volatility_tiers(tiers: Mapping[str, Any]) -> bool:
    for name in VOLATILITY_TIER_NAMES:
        raw = tiers.get(name)
        if raw is None:
            continue
        payload = raw.model_dump(mode="json") if isinstance(raw, AssetRiskTierSettings) else dict(raw)
        symbols = payload.get("symbols") or []
        if symbols:
            return True
    return False


def resolve_asset_risk_tier(
    symbol: str,
    tiers: Mapping[str, Any] | None = None,
) -> AssetRiskTierSettings:
    source = tiers or default_asset_risk_tiers()
    configured = {key: value for key, value in source.items() if not str(key).startswith("_")}
    normalized = _normalize_symbol(symbol)

    # Prefer ATR%-driven tiers when present; otherwise keep legacy core/standard.
    lookup_names = (
        list(VOLATILITY_TIER_NAMES)
        if _has_dynamic_volatility_tiers(configured)
        else [name for name in configured if name not in VOLATILITY_TIER_NAMES]
    )
    if not lookup_names:
        lookup_names = list(configured.keys())

    fallback: AssetRiskTierSettings | None = None
    for tier_name in lookup_names:
        raw = configured.get(tier_name)
        if raw is None or not isinstance(raw, (dict, AssetRiskTierSettings)):
            continue
        payload = raw.model_dump(mode="json") if isinstance(raw, AssetRiskTierSettings) else dict(raw)
        if "leverage" not in payload or "max_position_fraction" not in payload:
            continue
        payload.setdefault("tier", tier_name)
        tier = AssetRiskTierSettings.model_validate(payload)
        if tier.tier in {"standard", "vol_mid"} or not tier.symbols:
            fallback = tier
        if normalized in {_normalize_symbol(item) for item in tier.symbols}:
            return tier

    if fallback is not None:
        return fallback
    # Ultimate fallback when only vol tiers exist but symbol unmatched.
    if _has_dynamic_volatility_tiers(configured):
        mid = configured.get("vol_mid") or VOLATILITY_TIER_DEFAULTS["vol_mid"]
        payload = mid.model_dump(mode="json") if isinstance(mid, AssetRiskTierSettings) else dict(mid)
        payload.setdefault("tier", "vol_mid")
        payload.setdefault("leverage", VOLATILITY_TIER_DEFAULTS["vol_mid"]["leverage"])
        payload.setdefault(
            "max_position_fraction", VOLATILITY_TIER_DEFAULTS["vol_mid"]["max_position_fraction"]
        )
        return AssetRiskTierSettings.model_validate(payload)
    return AssetRiskTierSettings(
        tier="standard",
        leverage=10,
        max_position_fraction=0.06,
    )


def atr_pct_from_daily_bars(bars: Sequence[Any], *, period: int = 14) -> float | None:
    """Average ATR% over recent daily bars. bars need high/low/close attributes or mapping keys."""

    if len(bars) < period + 1:
        return None
    closes: list[float] = []
    true_ranges: list[float] = []
    prev_close: float | None = None
    for bar in bars:
        high = float(bar.high if hasattr(bar, "high") else bar["high"])
        low = float(bar.low if hasattr(bar, "low") else bar["low"])
        close = float(bar.close if hasattr(bar, "close") else bar["close"])
        ranges = [high - low]
        if prev_close is not None:
            ranges.append(abs(high - prev_close))
            ranges.append(abs(low - prev_close))
        true_ranges.append(max(ranges))
        closes.append(close)
        prev_close = close
    if len(true_ranges) < period:
        return None
    atr = sum(true_ranges[-period:]) / period
    close = closes[-1]
    if close <= 0:
        return None
    return atr / close


def classify_symbols_by_atr_pct(
    symbol_atr_pct: Mapping[str, float],
) -> dict[str, list[str]]:
    """Split symbols into vol_low / vol_mid / vol_high by ATR% terciles."""

    ordered = sorted(
        ((_normalize_symbol(sym), float(value)) for sym, value in symbol_atr_pct.items()),
        key=lambda item: item[1],
    )
    if not ordered:
        return {"vol_low": [], "vol_mid": [], "vol_high": []}
    n = len(ordered)
    low_end = max(1, n // 3)
    high_start = n - max(1, n // 3)
    if n < 3:
        # Tiny universes: put all in mid except extremes if 2.
        if n == 1:
            return {"vol_low": [], "vol_mid": [ordered[0][0]], "vol_high": []}
        return {"vol_low": [ordered[0][0]], "vol_mid": [], "vol_high": [ordered[1][0]]}
    return {
        "vol_low": [sym for sym, _ in ordered[:low_end]],
        "vol_mid": [sym for sym, _ in ordered[low_end:high_start]],
        "vol_high": [sym for sym, _ in ordered[high_start:]],
    }


def build_volatility_asset_risk_tiers(
    symbol_atr_pct: Mapping[str, float],
    *,
    keep_legacy_fallback: bool = True,
) -> dict[str, dict[str, Any]]:
    """Build execution_profile.asset_risk_tiers from ATR% scores."""

    buckets = classify_symbols_by_atr_pct(symbol_atr_pct)
    tiers: dict[str, dict[str, Any]] = {}
    for name in VOLATILITY_TIER_NAMES:
        defaults = VOLATILITY_TIER_DEFAULTS[name]
        tiers[name] = AssetRiskTierSettings(
            tier=name,
            symbols=buckets[name],
            leverage=defaults["leverage"],
            max_position_fraction=defaults["max_position_fraction"],
        ).model_dump(mode="json")
    if keep_legacy_fallback:
        legacy = default_asset_risk_tiers()
        tiers["core"] = legacy["core"]
        tiers["standard"] = legacy["standard"]
    return tiers


def volatility_tier_meta(symbol_atr_pct: Mapping[str, float], *, lookback_days: int = 30) -> dict[str, Any]:
    return {
        "source": "atr_pct_terciles",
        "lookback_days": lookback_days,
        "computed_at": datetime.now(UTC).isoformat(),
        "symbol_atr_pct": {_normalize_symbol(k): float(v) for k, v in symbol_atr_pct.items()},
        "defaults": VOLATILITY_TIER_DEFAULTS,
    }
