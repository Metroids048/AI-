from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from services.execution.signal_edge_stats import load_active_edge_stats


class TestLoadActiveEdgeStats:
    def test_returns_none_when_pointer_file_is_absent(self, tmp_path, monkeypatch) -> None:
        import services.execution.signal_edge_stats as module

        monkeypatch.setattr(module, "EDGE_STATS_ARTIFACT_DIR", tmp_path / "does_not_exist")

        assert load_active_edge_stats("some_strategy") is None

    def test_returns_none_when_artifact_older_than_max_age_days(self, tmp_path, monkeypatch) -> None:
        import services.execution.signal_edge_stats as module

        monkeypatch.setattr(module, "EDGE_STATS_ARTIFACT_DIR", tmp_path)
        strategy_dir = tmp_path / "s1"
        strategy_dir.mkdir()
        computed_at = datetime.now(UTC) - timedelta(days=45)
        (strategy_dir / "active.json").write_text(
            json.dumps(
                {
                    "computed_at": computed_at.isoformat(),
                    "sample_count": 100,
                    "win_rate": 0.55,
                    "average_win": 0.01,
                    "average_loss": 0.008,
                    "max_age_days": 30,
                }
            ),
            encoding="utf-8",
        )

        assert load_active_edge_stats("s1", now=datetime.now(UTC)) is None

    def test_returns_artifact_when_pointer_valid_and_fresh(self, tmp_path, monkeypatch) -> None:
        import services.execution.signal_edge_stats as module

        monkeypatch.setattr(module, "EDGE_STATS_ARTIFACT_DIR", tmp_path)
        strategy_dir = tmp_path / "s1"
        strategy_dir.mkdir()
        (strategy_dir / "active.json").write_text(
            json.dumps(
                {
                    "computed_at": datetime.now(UTC).isoformat(),
                    "sample_count": 120,
                    "win_rate": 0.42,
                    "average_win": 0.015,
                    "average_loss": 0.011,
                    "evaluation_start": "2026-05-01T00:00:00+00:00",
                    "evaluation_end": "2026-07-01T00:00:00+00:00",
                    "max_age_days": 30,
                }
            ),
            encoding="utf-8",
        )

        artifact = load_active_edge_stats("s1")

        assert artifact is not None
        assert artifact.sample_count == 120
        assert artifact.win_rate == 0.42
        assert artifact.average_win == 0.015
        assert artifact.average_loss == 0.011

    def test_returns_none_when_pointer_json_is_malformed(self, tmp_path, monkeypatch) -> None:
        import services.execution.signal_edge_stats as module

        monkeypatch.setattr(module, "EDGE_STATS_ARTIFACT_DIR", tmp_path)
        strategy_dir = tmp_path / "s1"
        strategy_dir.mkdir()
        (strategy_dir / "active.json").write_text("{not valid json", encoding="utf-8")

        assert load_active_edge_stats("s1") is None

    def test_returns_none_when_required_field_missing(self, tmp_path, monkeypatch) -> None:
        import services.execution.signal_edge_stats as module

        monkeypatch.setattr(module, "EDGE_STATS_ARTIFACT_DIR", tmp_path)
        strategy_dir = tmp_path / "s1"
        strategy_dir.mkdir()
        (strategy_dir / "active.json").write_text(
            json.dumps({"computed_at": datetime.now(UTC).isoformat(), "sample_count": 10}),
            encoding="utf-8",
        )

        assert load_active_edge_stats("s1") is None
