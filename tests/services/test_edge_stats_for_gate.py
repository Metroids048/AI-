from __future__ import annotations

import json
from datetime import UTC, datetime

from services.execution.decision_pipeline import _edge_stats_for_gate
from services.execution.signal_edge_stats import strategy_rules_hash
from shared.models import MetaLabelSample, StrategyContract, StrategyRules


def _proxy_samples() -> list[MetaLabelSample]:
    return [
        MetaLabelSample(sample_time=datetime(2026, 1, 1, tzinfo=UTC), net_return=0.01),
        MetaLabelSample(sample_time=datetime(2026, 1, 2, tzinfo=UTC), net_return=-0.02),
        MetaLabelSample(sample_time=datetime(2026, 1, 3, tzinfo=UTC), net_return=0.03),
    ]


def _strategy(key: str = "generic") -> StrategyContract:
    return StrategyContract(
        strategy_id=key,
        strategy_key=key,
        source="test",
        core_thesis="edge gate contract",
        rules=StrategyRules(entry_rules={"candidate_id": "operator_heuristic_v1"}),
    )


class TestEdgeStatsForGate:
    def test_main_lane_requires_validated_artifact_when_missing(self, tmp_path, monkeypatch) -> None:
        import services.execution.signal_edge_stats as edge_module

        monkeypatch.setattr(edge_module, "EDGE_STATS_ARTIFACT_DIR", tmp_path / "does_not_exist")
        strategy = _strategy("auto_paper_mature_templates")

        stats = _edge_stats_for_gate(strategy=strategy, symbol="BTC/USDT", training_samples=_proxy_samples())

        assert stats["validated_edge_required"] is True
        assert stats["source"] == "validated_edge_stats_missing_or_stale"
        assert stats["net_expectancy"] is None

    def test_main_lane_prefers_matching_v2_artifact(self, tmp_path, monkeypatch) -> None:
        import services.execution.signal_edge_stats as edge_module

        monkeypatch.setattr(edge_module, "EDGE_STATS_ARTIFACT_DIR", tmp_path)
        strategy = _strategy("auto_paper_mature_templates")
        target = tmp_path / "auto_paper_mature_templates" / "operator_heuristic_v1" / "BTCUSDT"
        target.mkdir(parents=True)
        (target / "active.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "strategy_key": "auto_paper_mature_templates",
                    "candidate_id": "operator_heuristic_v1",
                    "rules_hash": strategy_rules_hash(strategy.rules),
                    "symbol": "BTC/USDT",
                    "computed_at": datetime.now(UTC).isoformat(),
                    "sample_count": 84,
                    "oos_sample_count": 40,
                    "win_rate": 0.41,
                    "average_net_win": 0.021,
                    "average_net_loss_magnitude": 0.017,
                    "net_expectancy": 0.001,
                    "sharpe": 1.2,
                    "profit_factor": 1.4,
                    "max_drawdown": 0.2,
                    "eligible": True,
                    "failed_reasons": [],
                    "max_age_days": 30,
                }
            ),
            encoding="utf-8",
        )

        stats = _edge_stats_for_gate(strategy=strategy, symbol="BTC/USDT", training_samples=_proxy_samples())

        assert stats["sample_count"] == 84.0
        assert stats["net_expectancy"] == 0.001
        assert stats["source"] == "validated_oos_artifact_v2"

    def test_observation_lane_keeps_non_authoritative_proxy(self) -> None:
        stats = _edge_stats_for_gate(
            strategy=_strategy("signal_observation_technical"),
            symbol="BTC/USDT",
            training_samples=_proxy_samples(),
        )
        assert stats["sample_count"] == 3.0
        assert stats["source"] == "raw_bar_proxy"
        assert stats["validated_edge_required"] is False
