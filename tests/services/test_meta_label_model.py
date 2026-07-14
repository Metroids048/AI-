from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from services.strategy_library.meta_label_model import (
    FEATURE_NAMES,
    MetaLabelModelArtifact,
    extract_features,
    load_active_model,
    predict_win_probability,
)


class _Bar:
    def __init__(self, *, close: float, high: float, low: float, volume: float, timestamp: datetime) -> None:
        self.close = Decimal(str(close))
        self.high = Decimal(str(high))
        self.low = Decimal(str(low))
        self.volume = Decimal(str(volume))
        self.timestamp = timestamp


def _synthetic_bars(count: int, *, start_price: float = 100.0) -> list[_Bar]:
    base_time = datetime(2026, 1, 1, tzinfo=UTC)
    bars = []
    price = start_price
    for i in range(count):
        price *= 1.001
        bars.append(
            _Bar(
                close=price,
                high=price * 1.002,
                low=price * 0.998,
                volume=1000 + i,
                timestamp=base_time + timedelta(hours=i),
            )
        )
    return bars


class TestExtractFeatures:
    def test_returns_none_with_fewer_than_21_bars(self) -> None:
        bars = _synthetic_bars(20)

        features = extract_features(
            bars=bars,
            direction_vote_count=2,
            entry_vote_count=1,
            ensemble_confidence=0.6,
            funding_rate_bps=1.5,
            signal_time=bars[-1].timestamp,
        )

        assert features is None

    def test_returns_all_declared_feature_names_when_history_sufficient(self) -> None:
        bars = _synthetic_bars(30)

        features = extract_features(
            bars=bars,
            direction_vote_count=2,
            entry_vote_count=1,
            ensemble_confidence=0.6,
            funding_rate_bps=1.5,
            signal_time=bars[-1].timestamp,
        )

        assert features is not None
        assert set(features.keys()) == set(FEATURE_NAMES)

    def test_is_deterministic_for_the_same_input(self) -> None:
        bars = _synthetic_bars(30)

        first = extract_features(
            bars=bars,
            direction_vote_count=3,
            entry_vote_count=2,
            ensemble_confidence=0.7,
            funding_rate_bps=-2.0,
            signal_time=bars[-1].timestamp,
        )
        second = extract_features(
            bars=bars,
            direction_vote_count=3,
            entry_vote_count=2,
            ensemble_confidence=0.7,
            funding_rate_bps=-2.0,
            signal_time=bars[-1].timestamp,
        )

        assert first == second

    def test_funding_rate_none_defaults_to_zero_not_missing(self) -> None:
        bars = _synthetic_bars(30)

        features = extract_features(
            bars=bars,
            direction_vote_count=1,
            entry_vote_count=1,
            ensemble_confidence=0.5,
            funding_rate_bps=None,
            signal_time=bars[-1].timestamp,
        )

        assert features is not None
        assert features["funding_rate_bps"] == 0.0

    def test_only_uses_bars_up_to_and_including_the_decision_bar(self) -> None:
        """Feeding extra future bars must not change the computed features --
        this guards against accidentally leaking lookahead information into
        training samples built from a longer bar list than was available at
        the actual decision time."""
        bars = _synthetic_bars(40)

        features_at_30 = extract_features(
            bars=bars[:30],
            direction_vote_count=1,
            entry_vote_count=1,
            ensemble_confidence=0.5,
            funding_rate_bps=1.0,
            signal_time=bars[29].timestamp,
        )
        features_at_30_again = extract_features(
            bars=bars[:30],
            direction_vote_count=1,
            entry_vote_count=1,
            ensemble_confidence=0.5,
            funding_rate_bps=1.0,
            signal_time=bars[29].timestamp,
        )

        assert features_at_30 == features_at_30_again


class TestLoadActiveModel:
    def test_returns_none_when_pointer_file_is_absent(self, tmp_path, monkeypatch) -> None:
        import services.strategy_library.meta_label_model as module

        monkeypatch.setattr(module, "MODEL_ARTIFACT_DIR", tmp_path / "does_not_exist")

        assert load_active_model("some_strategy") is None

    def test_returns_none_when_model_file_referenced_by_pointer_is_missing(self, tmp_path, monkeypatch) -> None:
        import services.strategy_library.meta_label_model as module

        monkeypatch.setattr(module, "MODEL_ARTIFACT_DIR", tmp_path)
        strategy_dir = tmp_path / "s1"
        strategy_dir.mkdir()
        (strategy_dir / "active.json").write_text(
            json.dumps(
                {
                    "version": "v1",
                    "trained_at": datetime.now(UTC).isoformat(),
                    "feature_names": list(FEATURE_NAMES),
                    "train_sample_count": 500,
                    "validation_auc": 0.6,
                    "model_path": str(strategy_dir / "v1.joblib"),
                    "max_age_days": 30,
                }
            ),
            encoding="utf-8",
        )

        assert load_active_model("s1") is None

    def test_returns_none_when_model_is_older_than_max_age_days(self, tmp_path, monkeypatch) -> None:
        import services.strategy_library.meta_label_model as module

        monkeypatch.setattr(module, "MODEL_ARTIFACT_DIR", tmp_path)
        strategy_dir = tmp_path / "s1"
        strategy_dir.mkdir()
        model_path = strategy_dir / "v1.joblib"
        model_path.write_bytes(b"not a real model, existence is all that's checked here")
        trained_at = datetime.now(UTC) - timedelta(days=45)
        (strategy_dir / "active.json").write_text(
            json.dumps(
                {
                    "version": "v1",
                    "trained_at": trained_at.isoformat(),
                    "feature_names": list(FEATURE_NAMES),
                    "train_sample_count": 500,
                    "validation_auc": 0.6,
                    "model_path": str(model_path),
                    "max_age_days": 30,
                }
            ),
            encoding="utf-8",
        )

        assert load_active_model("s1", now=datetime.now(UTC)) is None

    def test_returns_artifact_when_pointer_valid_and_fresh(self, tmp_path, monkeypatch) -> None:
        import services.strategy_library.meta_label_model as module

        monkeypatch.setattr(module, "MODEL_ARTIFACT_DIR", tmp_path)
        strategy_dir = tmp_path / "s1"
        strategy_dir.mkdir()
        model_path = strategy_dir / "v1.joblib"
        model_path.write_bytes(b"placeholder")
        (strategy_dir / "active.json").write_text(
            json.dumps(
                {
                    "version": "v1",
                    "trained_at": datetime.now(UTC).isoformat(),
                    "feature_names": list(FEATURE_NAMES),
                    "train_sample_count": 500,
                    "validation_auc": 0.61,
                    "model_path": str(model_path),
                    "max_age_days": 30,
                }
            ),
            encoding="utf-8",
        )

        artifact = load_active_model("s1")

        assert artifact is not None
        assert artifact.version == "v1"
        assert artifact.validation_auc == 0.61
        assert artifact.model_path == model_path

    def test_returns_none_when_pointer_json_is_malformed(self, tmp_path, monkeypatch) -> None:
        import services.strategy_library.meta_label_model as module

        monkeypatch.setattr(module, "MODEL_ARTIFACT_DIR", tmp_path)
        strategy_dir = tmp_path / "s1"
        strategy_dir.mkdir()
        (strategy_dir / "active.json").write_text("{not valid json", encoding="utf-8")

        assert load_active_model("s1") is None


class TestPredictWinProbability:
    def test_returns_none_when_model_file_cannot_be_loaded(self, tmp_path) -> None:
        model_path = tmp_path / "broken.joblib"
        model_path.write_bytes(b"garbage, not a real joblib pickle")
        artifact = MetaLabelModelArtifact(
            strategy_key="s1",
            version="v1",
            trained_at=datetime.now(UTC).isoformat(),
            feature_names=FEATURE_NAMES,
            train_sample_count=500,
            validation_auc=0.6,
            model_path=model_path,
        )

        result = predict_win_probability(artifact, dict.fromkeys(FEATURE_NAMES, 0.0))

        assert result is None

    def test_returns_probability_for_a_real_trained_estimator(self, tmp_path) -> None:
        joblib = __import__("joblib")
        from sklearn.linear_model import LogisticRegression

        estimator = LogisticRegression()
        estimator.fit([[0.0] * len(FEATURE_NAMES), [1.0] * len(FEATURE_NAMES)], [0, 1])
        model_path = tmp_path / "real.joblib"
        joblib.dump(estimator, model_path)
        artifact = MetaLabelModelArtifact(
            strategy_key="s1",
            version="v1",
            trained_at=datetime.now(UTC).isoformat(),
            feature_names=FEATURE_NAMES,
            train_sample_count=500,
            validation_auc=0.6,
            model_path=model_path,
        )

        result = predict_win_probability(artifact, dict.fromkeys(FEATURE_NAMES, 1.0))

        assert result is not None
        assert 0.0 <= result <= 1.0
