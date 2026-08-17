"""Symbol-level leverage and notional caps for simulation-first execution."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from services.data.universe import exchange_to_platform_symbol
from shared.models import AssetRiskTierSettings

CORE_SYMBOLS = ("BTC/USDT", "ETH/USDT", "SOL/USDT")
VOLATILITY_TIER_NAMES = ("vol_low", "vol_mid", "vol_high")

# Directional execution is intentionally capped independently of any operator
# slider or legacy tier payload.  Persisted profiles can outlive code defaults,
# so every tier construction and resolution path applies these same ceilings.
MAX_DIRECTIONAL_LEVERAGE = 50.0
MAX_DIRECTIONAL_POSITION_FRACTION = 2.50

# Directional execution uses the same 50x / 5%-margin budget for every authorized
# symbol.  ``max_position_fraction`` is the resulting notional fraction (0.05 x
# 50 = 2.50), not the margin fraction itself.
VOLATILITY_TIER_DEFAULTS: dict[str, dict[str, float]] = {
    "vol_low": {"leverage": 50.0, "max_position_fraction": 2.50},
    "vol_mid": {"leverage": 50.0, "max_position_fraction": 2.50},
    "vol_high": {"leverage": 50.0, "max_position_fraction": 2.50},
}


def default_asset_risk_tiers() -> dict[str, dict[str, Any]]:
    return {
        "core": AssetRiskTierSettings(
            tier="core",
            symbols=list(CORE_SYMBOLS),
            leverage=MAX_DIRECTIONAL_LEVERAGE,
            max_position_fraction=MAX_DIRECTIONAL_POSITION_FRACTION,
        ).model_dump(mode="json"),
        "standard": AssetRiskTierSettings(
            tier="standard",
            symbols=[],
            leverage=MAX_DIRECTIONAL_LEVERAGE,
            max_position_fraction=MAX_DIRECTIONAL_POSITION_FRACTION,
        ).model_dump(mode="json"),
    }


# Relative risk ordering used to rescale tiers when an operator moves the
# max_leverage / max_symbol_exposure sliders. Each tuple is (leverage_ratio,
# max_position_fraction_ratio) applied against the slider value, preserving the
# core > vol_low > standard > vol_mid > vol_high risk ordering while anchoring
# the highest-privilege tier to the operator's chosen ceiling.
TIER_SCALE_RATIOS: dict[str, tuple[float, float]] = {
    "core": (1.0, 1.0),
    "vol_low": (1.0, 1.0),
    "standard": (1.0, 1.0),
    "vol_mid": (1.0, 1.0),
    "vol_high": (1.0, 1.0),
}


def cap_directional_leverage(value: float) -> float:
    """Return a valid leverage value bounded by the directional ceiling."""

    return max(1.0, min(MAX_DIRECTIONAL_LEVERAGE, float(value)))


def cap_directional_position_fraction(value: float) -> float:
    """Return a valid exposure fraction bounded by the directional ceiling."""

    return max(0.01, min(MAX_DIRECTIONAL_POSITION_FRACTION, float(value)))


def scale_asset_risk_tiers(
    tiers: Mapping[str, Any] | None,
    *,
    max_leverage: float,
    max_symbol_exposure: float,
) -> dict[str, dict[str, Any]]:
    """Rescale existing tier leverage/max_position_fraction to track operator sliders.

    Symbol assignments (e.g. from the weekly ATR% volatility sweep) are preserved
    as-is; only the numeric leverage/max_position_fraction of each known tier is
    rescaled against TIER_SCALE_RATIOS, so "core" always tracks the slider value
    directly and lower tiers stay proportionally tighter.
    """

    source: Mapping[str, Any] = tiers if tiers else default_asset_risk_tiers()
    scaled: dict[str, dict[str, Any]] = {}
    for name, raw in source.items():
        if str(name).startswith("_") or not isinstance(raw, (dict, AssetRiskTierSettings)):
            continue
        payload = raw.model_dump(mode="json") if isinstance(raw, AssetRiskTierSettings) else dict(raw)
        leverage_ratio, fraction_ratio = TIER_SCALE_RATIOS.get(name, (1.0, 1.0))
        payload["leverage"] = cap_directional_leverage(round(max_leverage * leverage_ratio, 2))
        payload["max_position_fraction"] = cap_directional_position_fraction(
            round(max_symbol_exposure * fraction_ratio, 4)
        )
        payload.setdefault("tier", name)
        scaled[name] = AssetRiskTierSettings.model_validate(payload).model_dump(mode="json")
    return scaled


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
        tier = AssetRiskTierSettings.model_validate(
            {
                **payload,
                "leverage": cap_directional_leverage(payload["leverage"]),
                "max_position_fraction": cap_directional_position_fraction(payload["max_position_fraction"]),
            }
        )
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
        payload.setdefault("max_position_fraction", VOLATILITY_TIER_DEFAULTS["vol_mid"]["max_position_fraction"])
        return AssetRiskTierSettings.model_validate(
            {
                **payload,
                "leverage": cap_directional_leverage(payload["leverage"]),
                "max_position_fraction": cap_directional_position_fraction(payload["max_position_fraction"]),
            }
        )
    return AssetRiskTierSettings(
        tier="standard",
        leverage=MAX_DIRECTIONAL_LEVERAGE,
        max_position_fraction=MAX_DIRECTIONAL_POSITION_FRACTION,
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
