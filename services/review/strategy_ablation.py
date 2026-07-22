"""Pure Review-layer evaluation of A-E strategy-filter shadow variants.

The evaluator consumes a persisted decision trace and returns structured
counterfactual *candidates*. It has no repository or execution dependency and
cannot create a TradeIntent, reserve risk, or call an exchange gateway.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

A_CURRENT_PRODUCTION = "A_CURRENT_PRODUCTION"
B_NO_LLM_HARD_VETO = "B_NO_LLM_HARD_VETO"
C_WEIGHTED_ENSEMBLE = "C_WEIGHTED_ENSEMBLE"
D_HIERARCHICAL_MTF = "D_HIERARCHICAL_MTF"
E_COMBINED_BCD = "E_COMBINED_BCD"


@dataclass(frozen=True, slots=True)
class ShadowVariantResult:
    variant: str
    candidate: bool | None
    side: str | None
    reason: str
    long_weight: float
    short_weight: float
    llm_advisory: bool
    evidence_gaps: tuple[str, ...] = ()


def _signals(trace: dict[str, Any]) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    raw = trace.get("signals")
    if isinstance(raw, list):
        collected.extend(item for item in raw if isinstance(item, dict))
    volatility = trace.get("volatility")
    mtf = volatility.get("multi_timeframe") if isinstance(volatility, dict) else None
    direction_signals = mtf.get("direction_signals") if isinstance(mtf, dict) else None
    if isinstance(direction_signals, list):
        collected.extend(item for item in direction_signals if isinstance(item, dict))
    return collected


def _weights(trace: dict[str, Any]) -> tuple[float, float, str | None]:
    long_weight = 0.0
    short_weight = 0.0
    for signal in _signals(trace):
        side = str(signal.get("side") or "").lower()
        try:
            confidence = max(float(signal.get("confidence", 0.0)), 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        if side == "long":
            long_weight += confidence
        elif side == "short":
            short_weight += confidence
    weighted_side: str | None
    if long_weight > short_weight:
        weighted_side = "long"
    elif short_weight > long_weight:
        weighted_side = "short"
    else:
        weighted_side = None
    return round(long_weight, 12), round(short_weight, 12), weighted_side


def _ensemble_side(trace: dict[str, Any]) -> str | None:
    ensemble = trace.get("ensemble")
    if not isinstance(ensemble, dict):
        return None
    side = str(ensemble.get("fused_direction") or "").lower()
    return side if side in {"long", "short"} else None


def _llm_veto(trace: dict[str, Any]) -> bool:
    veto = trace.get("veto_result")
    return isinstance(veto, dict) and veto.get("veto") is True


def _current(trace: dict[str, Any], *, long_weight: float, short_weight: float) -> ShadowVariantResult:
    status = str(trace.get("pipeline_status") or "unknown")
    side = _ensemble_side(trace)
    candidate = status == "bet_taken" and side is not None and not _llm_veto(trace)
    return ShadowVariantResult(
        variant=A_CURRENT_PRODUCTION,
        candidate=candidate,
        side=side if candidate else None,
        reason="current pipeline passed" if candidate else f"current pipeline blocked at {status}",
        long_weight=long_weight,
        short_weight=short_weight,
        llm_advisory=False,
    )


def _no_llm(
    trace: dict[str, Any],
    current: ShadowVariantResult,
    *,
    long_weight: float,
    short_weight: float,
) -> ShadowVariantResult:
    side = _ensemble_side(trace)
    bypassed = str(trace.get("pipeline_status") or "") == "vetoed" and side is not None
    return ShadowVariantResult(
        variant=B_NO_LLM_HARD_VETO,
        candidate=True if bypassed else current.candidate,
        side=side if bypassed else current.side,
        reason="LLM veto retained as advisory" if bypassed else current.reason,
        long_weight=long_weight,
        short_weight=short_weight,
        llm_advisory=_llm_veto(trace),
    )


def _weighted_ensemble(
    trace: dict[str, Any],
    current: ShadowVariantResult,
    *,
    long_weight: float,
    short_weight: float,
    weighted_side: str | None,
) -> ShadowVariantResult:
    status = str(trace.get("pipeline_status") or "unknown")
    if status != "ensemble_discarded":
        return ShadowVariantResult(
            variant=C_WEIGHTED_ENSEMBLE,
            candidate=current.candidate,
            side=current.side,
            reason=current.reason,
            long_weight=long_weight,
            short_weight=short_weight,
            llm_advisory=False,
        )
    if weighted_side is None:
        return ShadowVariantResult(
            variant=C_WEIGHTED_ENSEMBLE,
            candidate=None,
            side=None,
            reason="weighted ensemble is tied or has no usable confidence",
            long_weight=long_weight,
            short_weight=short_weight,
            llm_advisory=False,
            evidence_gaps=("usable signal confidence",),
        )
    return ShadowVariantResult(
        variant=C_WEIGHTED_ENSEMBLE,
        candidate=True,
        side=weighted_side,
        reason="confidence-weighted signals produce a directional shadow candidate",
        long_weight=long_weight,
        short_weight=short_weight,
        llm_advisory=False,
    )


def _hierarchical_mtf(
    trace: dict[str, Any],
    current: ShadowVariantResult,
    *,
    long_weight: float,
    short_weight: float,
) -> ShadowVariantResult:
    status = str(trace.get("pipeline_status") or "unknown")
    if status not in {"multi_timeframe_disagreement", "confirmation_unavailable_fail_closed"}:
        return ShadowVariantResult(
            variant=D_HIERARCHICAL_MTF,
            candidate=current.candidate,
            side=current.side,
            reason=current.reason,
            long_weight=long_weight,
            short_weight=short_weight,
            llm_advisory=False,
        )
    volatility = trace.get("volatility")
    mtf = volatility.get("multi_timeframe") if isinstance(volatility, dict) else None
    shadow_context = mtf.get("shadow_context") if isinstance(mtf, dict) else None
    if not isinstance(shadow_context, dict) or not shadow_context.get("regime_4h_direction"):
        return ShadowVariantResult(
            variant=D_HIERARCHICAL_MTF,
            candidate=None,
            side=None,
            reason="historical early-return trace lacks the 4h regime needed for hierarchical MTF",
            long_weight=long_weight,
            short_weight=short_weight,
            llm_advisory=False,
            evidence_gaps=("4h", "downstream ensemble result"),
        )
    return ShadowVariantResult(
        variant=D_HIERARCHICAL_MTF,
        candidate=None,
        side=None,
        reason="4h context exists but downstream production ensemble was not evaluated",
        long_weight=long_weight,
        short_weight=short_weight,
        llm_advisory=False,
        evidence_gaps=("downstream ensemble result",),
    )


def evaluate_shadow_variants(trace: dict[str, Any]) -> tuple[ShadowVariantResult, ...]:
    """Return A-E results without mutating the trace or any external state."""
    long_weight, short_weight, weighted_side = _weights(trace)
    current = _current(trace, long_weight=long_weight, short_weight=short_weight)
    no_llm = _no_llm(
        trace,
        current,
        long_weight=long_weight,
        short_weight=short_weight,
    )
    weighted = _weighted_ensemble(
        trace,
        current,
        long_weight=long_weight,
        short_weight=short_weight,
        weighted_side=weighted_side,
    )
    hierarchical = _hierarchical_mtf(
        trace,
        current,
        long_weight=long_weight,
        short_weight=short_weight,
    )
    status = str(trace.get("pipeline_status") or "unknown")
    if status in {"multi_timeframe_disagreement", "confirmation_unavailable_fail_closed"}:
        combined = ShadowVariantResult(
            variant=E_COMBINED_BCD,
            candidate=hierarchical.candidate,
            side=hierarchical.side,
            reason=hierarchical.reason,
            long_weight=long_weight,
            short_weight=short_weight,
            llm_advisory=_llm_veto(trace),
            evidence_gaps=hierarchical.evidence_gaps,
        )
    elif status == "ensemble_discarded":
        combined = ShadowVariantResult(
            variant=E_COMBINED_BCD,
            candidate=weighted.candidate,
            side=weighted.side,
            reason=weighted.reason,
            long_weight=long_weight,
            short_weight=short_weight,
            llm_advisory=_llm_veto(trace),
            evidence_gaps=weighted.evidence_gaps,
        )
    elif status == "vetoed":
        combined = ShadowVariantResult(
            variant=E_COMBINED_BCD,
            candidate=no_llm.candidate,
            side=no_llm.side,
            reason=no_llm.reason,
            long_weight=long_weight,
            short_weight=short_weight,
            llm_advisory=True,
            evidence_gaps=no_llm.evidence_gaps,
        )
    else:
        combined = ShadowVariantResult(
            variant=E_COMBINED_BCD,
            candidate=current.candidate,
            side=current.side,
            reason=current.reason,
            long_weight=long_weight,
            short_weight=short_weight,
            llm_advisory=_llm_veto(trace),
        )
    return current, no_llm, weighted, hierarchical, combined
