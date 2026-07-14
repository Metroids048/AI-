from __future__ import annotations

import json
from datetime import UTC, datetime

from services.execution.decision_pipeline import _edge_stats_for_gate
from shared.models import MetaLabelSample


def _proxy_samples() -> list[MetaLabelSample]:
    return [
        MetaLabelSample(sample_time=datetime(2026, 1, 1, tzinfo=UTC), net_return=0.01),
        MetaLabelSample(sample_time=datetime(2026, 1, 2, tzinfo=UTC), net_return=-0.02),
        MetaLabelSample(sample_time=datetime(2026, 1, 3, tzinfo=UTC), net_return=0.03),
    ]


class TestEdgeStatsForGate:
    def test_falls_back_to_raw_bar_proxy_when_no_artifact_exists(self, tmp_path, monkeypatch) -> None:
        import services.execution.signal_edge_stats as edge_module

        monkeypatch.setattr(edge_module, "EDGE_STATS_ARTIFACT_DIR", tmp_path / "does_not_exist")

        stats = _edge_stats_for_gate(strategy_key="auto_paper_mature_templates", training_samples=_proxy_samples())

        assert stats["sample_count"] == 3.0
        assert stats["win_rate"] == 2 / 3

    def test_prefers_real_replay_artifact_when_fresh(self, tmp_path, monkeypatch) -> None:
        import services.execution.signal_edge_stats as edge_module

        monkeypatch.setattr(edge_module, "EDGE_STATS_ARTIFACT_DIR", tmp_path)
        strategy_dir = tmp_path / "auto_paper_mature_templates"
        strategy_dir.mkdir()
        (strategy_dir / "active.json").write_text(
            json.dumps(
                {
                    "computed_at": datetime.now(UTC).isoformat(),
                    "sample_count": 84,
                    "win_rate": 0.41,
                    "average_win": 0.021,
                    "average_loss": 0.017,
                    "max_age_days": 30,
                }
            ),
            encoding="utf-8",
        )

        stats = _edge_stats_for_gate(strategy_key="auto_paper_mature_templates", training_samples=_proxy_samples())

        assert stats["sample_count"] == 84.0
        assert stats["win_rate"] == 0.41
        assert stats["average_win"] == 0.021
        assert stats["average_loss"] == 0.017

    def test_falls_back_to_proxy_when_strategy_key_is_none(self) -> None:
        stats = _edge_stats_for_gate(strategy_key=None, training_samples=_proxy_samples())

        assert stats["sample_count"] == 3.0
