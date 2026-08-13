"""Research-only projection from a production RegimeScore onto a regime label.

Read-only. No execution authority, no database write, no production behaviour change.
See ADR-004 and `.p2a-execution-manifest.yaml`.

Why a projection and not a classifier
-------------------------------------
The platform already owns regime scoring: `MarketContextBuilder` produces the
point-in-time context and `RegimeScorerV2` produces the score vector. P2-A2 adds only
the missing last step — turning that continuous vector into a discrete
TREND / RANGE / EXPANSION / UNKNOWN label that a per-regime exit comparison can slice
on. Nothing here re-derives direction or volatility; a second scoring algorithm would
make research labels and runtime labels mean different things.

Frozen rules
------------
The confidence threshold and the comparison rules below are fixed in advance and must
not be searched against the sample's realised PnL: with a sample this small, tuning the
label boundary until a preferred exit policy wins is indistinguishable from fitting
noise. The full score vector is persisted alongside the label so that if the projection
rules are ever revised, history can be recomputed instead of being overwritten.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from services.research.exit_policy_shadow.contracts import Regime
from services.strategy_library.context import MarketContext
from services.strategy_library.proposal_pipeline import PROPOSAL_CONTEXT_WINDOW_LENGTHS
from services.strategy_library.regime.scorer_v2 import SCORER_VERSION, RegimeScore

CLASSIFIER_VERSION = "p2a-regime-label-v1"

REGIME_LABEL_CONFIDENCE_THRESHOLD = 0.60
"""Minimum score for a label to be asserted at all.

A research reporting threshold, not a production trading parameter. Frozen: P2-A2
forbids moving it to change which exit policy wins.
"""

CRITICAL_TIMEFRAMES: tuple[str, ...] = ("15m", "1h", "4h")
"""Timeframes the score vector actually depends on.

`RegimeScorerV2` derives direction from 15m/1h/4h and volatility from 15m. 1m and 5m
enter only through the instability penalty, so their absence must not veto a label —
sample entries that predate 5m collection still carry complete critical evidence, and
discarding them would shrink an already small sample for no methodological gain.
"""

MIN_BARS_FOR_LABEL: dict[str, int] = {
    timeframe: PROPOSAL_CONTEXT_WINDOW_LENGTHS[timeframe] for timeframe in CRITICAL_TIMEFRAMES
}
"""Required bar count per critical timeframe, taken from the production window.

Deliberately not a looser research-specific number. `RegimeScorerV2._direction`
measures first-close vs last-close over whatever window it is handed, so a short
window silently changes what "trend" means while carrying the same weight, and
`_volatility_scores` returns zeros below ten true ranges — which suppresses
`expansion` and inflates `range`, manufacturing a confident-looking RANGE label out of
thin history. Requiring the production window makes a label mean exactly what a
runtime label means.
"""


class TrendDirection(StrEnum):
    UP = "UP"
    DOWN = "DOWN"
    NONE = "NONE"


class RegimeLabelResult(BaseModel):
    """A regime label plus the complete evidence it was derived from.

    Persisting the whole score vector (not just the label) is what keeps the projection
    revisable: a future rule change can be recomputed from these fields instead of
    contaminating historical rows.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    regime: Regime
    trend_direction: TrendDirection
    direction_score: float
    trend_up: float
    trend_down: float
    range: float
    compression: float
    expansion: float
    unstable: float
    evidence: dict[str, float]
    decision_time: datetime
    scorer_version: str
    classifier_version: str
    feature_snapshot_hash: str
    data_quality_reason: str = ""
    """Non-empty only when UNKNOWN was forced by data quality.

    An empty string on an UNKNOWN label therefore means honest low confidence, which is
    a different finding from missing data and must stay distinguishable in the report.
    """


def project_regime_label(*, context: MarketContext, score: RegimeScore) -> RegimeLabelResult:
    """Project ``score`` onto a discrete regime label.

    Deterministic: identical (context, score) inputs always produce an identical
    result, including the feature snapshot hash.

    Data quality is checked first. When a critical timeframe is missing, too short,
    stale, or internally gapped, the label is UNKNOWN regardless of how confident the
    score looks — a score computed over incomplete evidence is not weak evidence, it is
    evidence about a different window than it claims.
    """
    direction_score = max(score.trend_up, score.trend_down)
    quality_reason = _data_quality_reason(context)

    if quality_reason:
        regime = Regime.UNKNOWN
    else:
        regime = _label_from_scores(
            direction_score=direction_score,
            range_score=score.range,
            expansion=score.expansion,
        )

    return RegimeLabelResult(
        regime=regime,
        trend_direction=_trend_direction(regime=regime, score=score),
        direction_score=direction_score,
        trend_up=score.trend_up,
        trend_down=score.trend_down,
        range=score.range,
        compression=score.compression,
        expansion=score.expansion,
        unstable=score.unstable,
        evidence=dict(score.evidence),
        decision_time=context.decision_time,
        scorer_version=SCORER_VERSION,
        classifier_version=CLASSIFIER_VERSION,
        feature_snapshot_hash=compute_feature_snapshot_hash(context),
        data_quality_reason=quality_reason,
    )


def _label_from_scores(*, direction_score: float, range_score: float, expansion: float) -> Regime:
    """Apply the frozen, mutually exclusive label rules.

    Each branch requires its own score to both clear the threshold and dominate the
    others, so no input can satisfy two branches. Anything else abstains rather than
    taking whichever score happens to be largest.
    """
    threshold = REGIME_LABEL_CONFIDENCE_THRESHOLD

    if expansion >= threshold and expansion > direction_score and expansion > range_score:
        return Regime.EXPANSION
    if direction_score >= threshold and direction_score > range_score and direction_score >= expansion:
        return Regime.TREND
    if range_score >= threshold and range_score > direction_score and range_score >= expansion:
        return Regime.RANGE
    return Regime.UNKNOWN


def _trend_direction(*, regime: Regime, score: RegimeScore) -> TrendDirection:
    """Directional sub-label, recorded only for TREND."""
    if regime != Regime.TREND:
        return TrendDirection.NONE
    if score.trend_up > score.trend_down:
        return TrendDirection.UP
    if score.trend_down > score.trend_up:
        return TrendDirection.DOWN
    return TrendDirection.NONE


def _data_quality_reason(context: MarketContext) -> str:
    """Return why the critical evidence is unusable, or an empty string when it is fine.

    Reasons are sorted and joined so the string is stable across runs, which keeps the
    resulting report row comparable between evaluations.
    """
    windows = {
        "15m": context.bars_15m,
        "1h": context.bars_1h,
        "4h": context.bars_4h,
    }
    reasons: list[str] = []

    for timeframe in CRITICAL_TIMEFRAMES:
        window = windows[timeframe]
        required = MIN_BARS_FOR_LABEL[timeframe]

        if not window.bars:
            reasons.append(f"{timeframe}:missing")
            continue
        if len(window.bars) < required:
            reasons.append(f"{timeframe}:insufficient_bars({len(window.bars)}<{required})")
        if timeframe in context.freshness.stale_timeframes:
            reasons.append(f"{timeframe}:stale")
        if window.gap_count > 0:
            reasons.append(f"{timeframe}:gap({window.gap_count})")

    return ",".join(sorted(reasons))


def compute_feature_snapshot_hash(context: MarketContext) -> str:
    """Stable digest of the point-in-time evidence behind a label.

    Covers the symbol, the decision time, and every closed critical bar. Two labels
    with the same hash were computed from byte-identical evidence, which is what makes
    the point-in-time guarantee auditable: a later backfill that would have changed the
    inputs necessarily changes this digest.
    """
    windows = {
        "15m": context.bars_15m,
        "1h": context.bars_1h,
        "4h": context.bars_4h,
    }
    payload = {
        "symbol": context.symbol,
        "decision_time": context.decision_time.isoformat(),
        "timeframes": {
            timeframe: [
                [
                    bar.timestamp.isoformat(),
                    str(bar.open),
                    str(bar.high),
                    str(bar.low),
                    str(bar.close),
                    str(bar.volume),
                ]
                for bar in windows[timeframe].bars
            ]
            for timeframe in CRITICAL_TIMEFRAMES
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
