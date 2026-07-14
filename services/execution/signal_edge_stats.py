"""Offline-computed real signal-conditioned edge stats, loaded read-only at
decision time.

`decision_pipeline.py`'s `net_edge_after_cost` gate previously estimated
win_rate/average_win/average_loss from `_meta_label_samples()` -- the raw
close-to-close return of the last ~47 bars in the ensemble's fused direction,
regardless of whether this signal combination ever actually fired historically.
That is a noisy proxy for "will this specific signal make money", not a real
measurement of it; it is disconnected from the entry/exit rules the strategy
actually uses (stop distance, take profit, costs).

This module loads a per-strategy artifact computed by
`scripts/compute_signal_edge_stats.py`, which runs the exact same
`TechnicalStrategyValidationService` historical replay engine already used for
the ExitLadder-vs-fixed-2R comparison (docs/audits/2026-07-12-exitladder-replay-
comparison.md) -- i.e. real historical trades this signal+stop+take
configuration would actually have produced, not a raw-return proxy.

Fails closed to the existing raw-bar-return proxy (services/execution/net_edge.py
::meta_label_edge_stats over `_meta_label_samples()`) whenever no artifact
exists, it is stale, or it fails to load -- the same fail-closed pattern as
`services/strategy_library/meta_label_model.py::load_active_model`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

EDGE_STATS_ARTIFACT_DIR = Path("artifacts/signal_edge_stats")


@dataclass(frozen=True)
class SignalEdgeStatsArtifact:
    strategy_key: str
    computed_at: str
    sample_count: int
    win_rate: float
    average_win: float
    average_loss: float
    evaluation_start: str | None
    evaluation_end: str | None
    max_age_days: int = 30


def _active_pointer_path(strategy_key: str) -> Path:
    return EDGE_STATS_ARTIFACT_DIR / strategy_key / "active.json"


def load_active_edge_stats(strategy_key: str, *, now: datetime | None = None) -> SignalEdgeStatsArtifact | None:
    """Read the active real-edge-stats pointer for `strategy_key`.

    Returns None (fail-closed to the raw-bar-return proxy) whenever the
    pointer file is absent, unreadable, past `max_age_days`, or malformed --
    this function must never raise, since callers always have a working
    fallback and a broken artifact must not break live cycles.
    """
    pointer_path = _active_pointer_path(strategy_key)
    if not pointer_path.exists():
        return None
    try:
        meta = json.loads(pointer_path.read_text(encoding="utf-8"))
        computed_at = datetime.fromisoformat(meta["computed_at"])
        max_age_days = int(meta.get("max_age_days", 30))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    reference_time = now if now is not None else datetime.now(computed_at.tzinfo)
    if (reference_time - computed_at).days > max_age_days:
        return None
    try:
        return SignalEdgeStatsArtifact(
            strategy_key=strategy_key,
            computed_at=meta["computed_at"],
            sample_count=int(meta["sample_count"]),
            win_rate=float(meta["win_rate"]),
            average_win=float(meta["average_win"]),
            average_loss=float(meta["average_loss"]),
            evaluation_start=meta.get("evaluation_start"),
            evaluation_end=meta.get("evaluation_end"),
            max_age_days=max_age_days,
        )
    except (KeyError, TypeError, ValueError):
        return None
