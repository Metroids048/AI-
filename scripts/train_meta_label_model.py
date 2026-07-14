"""Offline training for the MetaLabel win-probability classifier.

Reconstructs walk-forward-safe training samples directly from persisted OHLCV
bars (never from live decision traces, which are too sparse in early Paper
history to train from) using the exact same `extract_features()` used at real
decision time (services/strategy_library/meta_label_model.py), so training and
inference features are computed identically.

This script never talks to an LLM and never writes to any table that Execution/
Gatekeeper reads. It only ever produces a local joblib artifact plus an
`active.json` pointer; `SignalEnsembleService.create_meta_label` reads that
pointer and falls back to the existing rule-based heuristic whenever it is
missing, stale, or fails to load (see meta_label_model.py::load_active_model).

Usage:
    python scripts/train_meta_label_model.py --strategy-key auto_paper_mature_templates \
        --database-url sqlite:///.local_paper_console.db
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime

# Label horizon: how many closed bars ahead we look to decide win/loss for a
# training sample. Matches the directional lane's `time_exit_hours=24` on 15m
# bars (24h / 15m = 96 bars) collapsed to a coarser default so this works
# across arbitrary timeframes without per-timeframe tuning.
DEFAULT_LABEL_HORIZON_BARS = 8
MIN_OOS_AUC = 0.55
MIN_TRAIN_SAMPLES = 200


@dataclass(frozen=True)
class TrainingSample:
    features: dict[str, float]
    label: int  # 1 if the forward return over the horizon was positive, else 0
    sample_time: datetime


def _build_samples(
    *,
    bars: list,
    label_horizon_bars: int,
) -> list[TrainingSample]:
    from services.strategy_library.meta_label_model import extract_features

    samples: list[TrainingSample] = []
    # Need at least 21 bars of history behind index i (extract_features'
    # trailing_return_20 requirement) and label_horizon_bars ahead of it.
    for i in range(21, len(bars) - label_horizon_bars):
        window = bars[: i + 1]
        features = extract_features(
            bars=window,
            direction_vote_count=1,
            entry_vote_count=1,
            ensemble_confidence=0.6,
            funding_rate_bps=None,
            signal_time=window[-1].timestamp,
        )
        if features is None:
            continue
        entry_close = float(bars[i].close)
        exit_close = float(bars[i + label_horizon_bars].close)
        if entry_close <= 0:
            continue
        forward_return = exit_close / entry_close - 1.0
        samples.append(
            TrainingSample(features=features, label=1 if forward_return > 0 else 0, sample_time=bars[i].timestamp)
        )
    return samples


def _walk_forward_split(samples: list[TrainingSample], *, oos_fraction: float = 0.25) -> tuple[list, list]:
    """Strict time-ordered split -- OOS is always the most recent slice, never
    shuffled, so the model is never evaluated on data that precedes training
    data in time (the exact lookahead-bias failure mode AGENTS.md forbids)."""
    ordered = sorted(samples, key=lambda sample: sample.sample_time)
    split_index = int(len(ordered) * (1.0 - oos_fraction))
    return ordered[:split_index], ordered[split_index:]


def train_and_evaluate(samples: list[TrainingSample]) -> tuple[object, list[str], float, int]:
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score

    from services.strategy_library.meta_label_model import FEATURE_NAMES

    train_samples, oos_samples = _walk_forward_split(samples)
    feature_names = list(FEATURE_NAMES)
    x_train = [[sample.features.get(name, 0.0) for name in feature_names] for sample in train_samples]
    y_train = [sample.label for sample in train_samples]
    x_oos = [[sample.features.get(name, 0.0) for name in feature_names] for sample in oos_samples]
    y_oos = [sample.label for sample in oos_samples]

    estimator = LogisticRegression(max_iter=1000)
    estimator.fit(x_train, y_train)

    if len(set(y_oos)) < 2:
        # AUC is undefined with only one class in the OOS window; treat as a
        # failing gate rather than raising, so the caller can report cleanly.
        return estimator, feature_names, 0.0, len(train_samples)
    oos_probabilities = estimator.predict_proba(x_oos)[:, 1]
    auc = float(roc_auc_score(y_oos, oos_probabilities))
    return estimator, feature_names, auc, len(train_samples)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy-key", required=True)
    parser.add_argument("--symbols", nargs="+", default=None, help="Defaults to the fixed Binance Top20")
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--label-horizon-bars", type=int, default=DEFAULT_LABEL_HORIZON_BARS)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--min-oos-auc", type=float, default=MIN_OOS_AUC)
    parser.add_argument("--min-train-samples", type=int, default=MIN_TRAIN_SAMPLES)
    args = parser.parse_args()

    if args.database_url:
        os.environ["POSTGRES_URL"] = args.database_url

    from services.data import DataRepository
    from services.data.service import DEFAULT_BINANCE_TOP20
    from services.database import get_session_factory
    from services.strategy_library.meta_label_model import FEATURE_NAMES, MODEL_ARTIFACT_DIR

    symbols = args.symbols or list(DEFAULT_BINANCE_TOP20)
    all_samples: list[TrainingSample] = []
    with get_session_factory()() as session:
        repo = DataRepository(session)
        for symbol in symbols:
            bars = repo.list_ohlcv_bars(symbol=symbol, timeframe=args.timeframe)
            if len(bars) < 21 + args.label_horizon_bars + 1:
                continue
            all_samples.extend(_build_samples(bars=bars, label_horizon_bars=args.label_horizon_bars))

    print(f"reconstructed {len(all_samples)} samples across {len(symbols)} symbols ({args.timeframe})")

    if len(all_samples) < args.min_train_samples:
        print(
            f"REJECTED: {len(all_samples)} samples < required minimum {args.min_train_samples} -- "
            "not enough history to train a model that isn't just fitting noise. "
            "create_meta_label() will keep using the rule-based heuristic."
        )
        return 1

    estimator, feature_names, oos_auc, train_count = train_and_evaluate(all_samples)
    print(f"train_samples={train_count} oos_auc={oos_auc:.4f} (gate: >= {args.min_oos_auc})")

    if oos_auc < args.min_oos_auc:
        print(
            f"REJECTED: OOS AUC {oos_auc:.4f} is below the {args.min_oos_auc} gate -- "
            "model not saved. create_meta_label() will keep using the rule-based heuristic."
        )
        return 1

    import joblib

    model_dir = MODEL_ARTIFACT_DIR / args.strategy_key
    model_dir.mkdir(parents=True, exist_ok=True)
    version = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    model_path = model_dir / f"{version}.joblib"
    joblib.dump(estimator, model_path)

    pointer = {
        "version": version,
        "trained_at": datetime.now(UTC).isoformat(),
        "feature_names": feature_names or list(FEATURE_NAMES),
        "train_sample_count": train_count,
        "validation_auc": oos_auc,
        "model_path": str(model_path),
        "max_age_days": 30,
    }
    (model_dir / "active.json").write_text(json.dumps(pointer, indent=2), encoding="utf-8")
    print(f"ACCEPTED: wrote {model_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
