"""Symbol-level leverage and notional caps for simulation-first execution."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from services.data.universe import exchange_to_platform_symbol
from shared.models import AssetRiskTierSettings, VolatilityRiskTierSettings

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
        scaled_leverage = cap_directional_leverage(round(max_leverage * leverage_ratio, 2))
        scaled_fraction = cap_directional_position_fraction(round(max_symbol_exposure * fraction_ratio, 4))
        # A per-symbol tier ceiling must survive an operator slider move: rescaling
        # is allowed to tighten but never to raise a tier back to the slider value.
        # Without this clamp, saving settings silently reverted the E-003 caps.
        tier_leverage_ceiling = payload.get("max_leverage")
        if tier_leverage_ceiling is not None:
            scaled_leverage = min(scaled_leverage, float(tier_leverage_ceiling))
        existing_fraction = payload.get("max_position_fraction")
        if existing_fraction is not None and payload.get("max_margin_fraction") is not None:
            scaled_fraction = min(scaled_fraction, float(existing_fraction))
        payload["leverage"] = scaled_leverage
        payload["max_position_fraction"] = scaled_fraction
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
        # A per-symbol tier may declare only the E-003 ceilings. Derive the legacy
        # fields from them rather than skipping the tier, which would silently fall
        # back to the permissive profile-wide values.
        if payload.get("leverage") is None and payload.get("max_leverage") is not None:
            payload["leverage"] = payload["max_leverage"]
        if payload.get("max_position_fraction") is None and payload.get("max_margin_fraction") is not None:
            payload["max_position_fraction"] = float(payload["max_margin_fraction"]) * float(payload["leverage"])
        if payload.get("leverage") is None or payload.get("max_position_fraction") is None:
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


# E-003 validation-phase per-symbol hard upper bounds for the Testnet directional
# lane. These are ceilings, not targets: stop-risk sizing still decides the actual
# quantity and routinely resolves smaller. Raising any value here is forbidden
# without an explicit operator decision.
SYMBOL_RISK_BASE_CEILINGS: dict[str, dict[str, float]] = {
    "BTC/USDT": {"risk_per_trade": 0.005, "max_leverage": 20.0, "max_margin_fraction": 0.020},
    "ETH/USDT": {"risk_per_trade": 0.004, "max_leverage": 15.0, "max_margin_fraction": 0.015},
    "BNB/USDT": {"risk_per_trade": 0.0035, "max_leverage": 12.0, "max_margin_fraction": 0.0125},
    "SOL/USDT": {"risk_per_trade": 0.0025, "max_leverage": 10.0, "max_margin_fraction": 0.010},
    "XRP/USDT": {"risk_per_trade": 0.0025, "max_leverage": 10.0, "max_margin_fraction": 0.010},
}

# Notional exposure ceiling implied by margin budget x leverage, kept explicit so
# the resolved exposure clamp cannot silently exceed the margin envelope.
SYMBOL_POSITION_FRACTION_CEILINGS: dict[str, float] = {
    symbol: round(values["max_margin_fraction"] * values["max_leverage"], 4)
    for symbol, values in SYMBOL_RISK_BASE_CEILINGS.items()
}

VOLATILITY_MULTIPLIERS: dict[str, float] = {
    "low": 1.00,
    "mid": 0.75,
    "high": 0.50,
    "shock": 0.25,
}


def symbol_risk_base_tiers() -> dict[str, dict[str, Any]]:
    """Per-symbol base ceilings as asset tiers consumable by the V2 resolver."""

    tiers: dict[str, dict[str, Any]] = {}
    for symbol, values in SYMBOL_RISK_BASE_CEILINGS.items():
        key = f"symbol_{symbol.split('/')[0].lower()}"
        tiers[key] = AssetRiskTierSettings(
            tier=key,
            symbols=[symbol],
            leverage=values["max_leverage"],
            max_position_fraction=SYMBOL_POSITION_FRACTION_CEILINGS[symbol],
            risk_per_trade=values["risk_per_trade"],
            max_leverage=values["max_leverage"],
            max_margin_fraction=values["max_margin_fraction"],
        ).model_dump(mode="json")
    return tiers


def resolve_volatility_adjustment(
    symbol: str,
    tiers: Mapping[str, Any] | None = None,
) -> tuple[Decimal, bool]:
    """Resolve the volatility multiplier for ``symbol``.

    Returns ``(multiplier, no_new_entry)``. The multiplier can only reduce risk, so
    an unmatched symbol or an absent configuration resolves to ``1`` and leaves the
    symbol ceilings untouched. A SHOCK tier can additionally block new entries; it
    never affects reduce-only exits, protection, or reconciliation.
    """

    if not isinstance(tiers, Mapping) or not tiers:
        return Decimal("1"), False
    normalized = _normalize_symbol(symbol)
    for name, raw in tiers.items():
        if str(name).startswith("_") or not isinstance(raw, (dict, VolatilityRiskTierSettings)):
            continue
        payload = raw.model_dump(mode="json") if isinstance(raw, VolatilityRiskTierSettings) else dict(raw)
        payload.setdefault("tier", name)
        tier = VolatilityRiskTierSettings.model_validate(payload)
        if normalized not in {_normalize_symbol(item) for item in tier.symbols}:
            continue
        multiplier = min(Decimal(str(tier.multiplier)), Decimal("1"))
        return multiplier, bool(tier.no_new_entry)
    return Decimal("1"), False


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


def build_volatility_risk_tiers(
    symbol_atr_pct: Mapping[str, float],
    *,
    required_symbols: Sequence[str] = (),
) -> dict[str, dict[str, Any]]:
    """Build dynamic LOW/MID/HIGH tiers that only multiply existing symbol ceilings.

    An execution symbol without enough daily ATR data is placed in a SHOCK
    no-entry tier. Unknown volatility must never resolve to full risk.
    """

    buckets = classify_symbols_by_atr_pct(symbol_atr_pct)
    tiers = {
        name.removeprefix("vol_"): VolatilityRiskTierSettings(
            tier=name.removeprefix("vol_"),
            symbols=buckets[name],
            multiplier=VOLATILITY_MULTIPLIERS[name.removeprefix("vol_")],
            no_new_entry=False,
        ).model_dump(mode="json")
        for name in VOLATILITY_TIER_NAMES
    }
    scored_symbols = {_normalize_symbol(symbol) for symbol in symbol_atr_pct}
    missing_symbols = [
        _normalize_symbol(symbol) for symbol in required_symbols if _normalize_symbol(symbol) not in scored_symbols
    ]
    tiers["shock"] = VolatilityRiskTierSettings(
        tier="shock",
        symbols=missing_symbols,
        multiplier=VOLATILITY_MULTIPLIERS["shock"],
        no_new_entry=True,
    ).model_dump(mode="json")
    return tiers


def volatility_tier_meta(symbol_atr_pct: Mapping[str, float], *, lookback_days: int = 30) -> dict[str, Any]:
    return {
        "source": "atr_pct_terciles",
        "lookback_days": lookback_days,
        "computed_at": datetime.now(UTC).isoformat(),
        "symbol_atr_pct": {_normalize_symbol(k): float(v) for k, v in symbol_atr_pct.items()},
        "defaults": VOLATILITY_TIER_DEFAULTS,
    }
