"""Offline-trained meta-label classifier, loaded read-only at decision time.

The existing `SignalEnsembleService.create_meta_label` (services/strategy_library/
ensemble/service.py) is a rule-based win-rate/average-return heuristic over the
last N same-direction training samples. This module adds an optional,
separately-trained statistical classifier that can replace that heuristic's
probability estimate when a model artifact exists and is fresh -- the rule path
remains the permanent fallback (fail-closed to the existing, already-audited
behavior) whenever no model is available, expired, or fails to load.

Model parameters always come from the deterministic offline training script
(scripts/train_meta_label_model.py), never from an LLM call -- this keeps the
decision auditable and reproducible per AGENTS.md's "AI is a researcher, not a
discretionary trader" boundary. An LLM may be consulted offline to *propose*
candidate feature names (a design decision, reviewed by a human), but it never
supplies feature values or model weights.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

MODEL_ARTIFACT_DIR = Path("artifacts/meta_label_models")

# Feature extraction must be deterministic and reconstructible from data that is
# actually available at real decision time (closed OHLCV bars, market extras,
# ensemble vote counts) -- never from information only known in hindsight.
FEATURE_NAMES: tuple[str, ...] = (
    "atr_percent",
    "trailing_return_5",
    "trailing_return_20",
    "volume_zscore_20",
    "ensemble_confidence",
    "direction_vote_count",
    "entry_vote_count",
    "funding_rate_bps",
    "hour_of_day_sin",
    "hour_of_day_cos",
)


@dataclass(frozen=True)
class MetaLabelModelArtifact:
    strategy_key: str
    version: str
    trained_at: str
    feature_names: tuple[str, ...]
    train_sample_count: int
    validation_auc: float
    model_path: Path
    max_age_days: int = 30


def _active_pointer_path(strategy_key: str) -> Path:
    return MODEL_ARTIFACT_DIR / strategy_key / "active.json"


def load_active_model(strategy_key: str, *, now: datetime | None = None) -> MetaLabelModelArtifact | None:
    """Read the active model pointer for `strategy_key`.

    Returns None (fail-closed to the rule-based path) whenever the pointer file
    is absent, unreadable, past `max_age_days`, or its model file is missing --
    this function must never raise, since callers always have a working
    rule-based fallback and a broken model artifact must not break live cycles.
    """
    pointer_path = _active_pointer_path(strategy_key)
    if not pointer_path.exists():
        return None
    try:
        meta = json.loads(pointer_path.read_text(encoding="utf-8"))
        model_path = Path(meta["model_path"])
        trained_at = datetime.fromisoformat(meta["trained_at"])
        max_age_days = int(meta.get("max_age_days", 30))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not model_path.exists():
        return None
    reference_time = now if now is not None else datetime.now(trained_at.tzinfo)
    if (reference_time - trained_at).days > max_age_days:
        return None
    return MetaLabelModelArtifact(
        strategy_key=strategy_key,
        version=str(meta.get("version", "unknown")),
        trained_at=meta["trained_at"],
        feature_names=tuple(meta.get("feature_names", FEATURE_NAMES)),
        train_sample_count=int(meta.get("train_sample_count", 0)),
        validation_auc=float(meta.get("validation_auc", 0.0)),
        model_path=model_path,
        max_age_days=max_age_days,
    )


def extract_features(
    *,
    bars: list[Any],
    direction_vote_count: int,
    entry_vote_count: int,
    ensemble_confidence: float,
    funding_rate_bps: float | None,
    signal_time: datetime,
) -> dict[str, float] | None:
    """Deterministic feature extraction shared by training and inference.

    `bars` must be closed OHLCV bars ending at (and including) the decision bar,
    oldest first. Returns None if there is not enough history to compute the
    rolling features -- callers must treat that the same as "no model
    available" and fall back to the rule-based path, not substitute zeros.
    """
    if len(bars) < 21:
        return None
    frame = pd.DataFrame(
        {
            "close": [float(bar.close) for bar in bars],
            "high": [float(bar.high) for bar in bars],
            "low": [float(bar.low) for bar in bars],
            "volume": [float(bar.volume) for bar in bars],
        }
    )
    close = frame["close"]
    high = frame["high"]
    low = frame["low"]
    previous_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - previous_close).abs(), (low - previous_close).abs()],
        axis=1,
    ).max(axis=1)
    atr = true_range.rolling(14).mean().iloc[-1]
    if pd.isna(atr) or close.iloc[-1] <= 0:
        return None
    atr_percent = float(atr) / float(close.iloc[-1])

    trailing_return_5 = float(close.iloc[-1] / close.iloc[-6] - 1.0) if close.iloc[-6] > 0 else 0.0
    trailing_return_20 = float(close.iloc[-1] / close.iloc[-21] - 1.0) if close.iloc[-21] > 0 else 0.0

    volume_window = frame["volume"].iloc[-20:]
    volume_mean = float(volume_window.mean())
    volume_std = float(volume_window.std(ddof=0))
    volume_zscore_20 = (float(frame["volume"].iloc[-1]) - volume_mean) / volume_std if volume_std > 0 else 0.0

    hour = signal_time.hour + signal_time.minute / 60.0
    hour_angle = 2.0 * math.pi * hour / 24.0

    return {
        "atr_percent": atr_percent,
        "trailing_return_5": trailing_return_5,
        "trailing_return_20": trailing_return_20,
        "volume_zscore_20": volume_zscore_20,
        "ensemble_confidence": float(ensemble_confidence),
        "direction_vote_count": float(direction_vote_count),
        "entry_vote_count": float(entry_vote_count),
        "funding_rate_bps": float(funding_rate_bps) if funding_rate_bps is not None else 0.0,
        "hour_of_day_sin": math.sin(hour_angle),
        "hour_of_day_cos": math.cos(hour_angle),
    }


def predict_win_probability(model: MetaLabelModelArtifact, features: dict[str, float]) -> float | None:
    """Load the joblib artifact and score `features`. Returns None (fail-closed
    to the rule-based path) on any load/inference error instead of raising."""
    try:
        import joblib
    except ImportError:
        return None
    try:
        estimator = joblib.load(model.model_path)
        ordered = [[features.get(name, 0.0) for name in model.feature_names]]
        return float(estimator.predict_proba(ordered)[0][1])
    except Exception:  # noqa: BLE001 - any model-file/runtime failure must fail closed
        return None
