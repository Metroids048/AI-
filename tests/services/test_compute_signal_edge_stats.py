from __future__ import annotations

import json
import sys
from datetime import UTC, datetime

import pytest

from services.execution.bootstrap import AUTO_PAPER_TECHNICAL_KEY
from services.validation.technical_replay import ReplayMetrics


def _fake_metrics(*, total_trades: int, evaluation_start=None, evaluation_end=None) -> ReplayMetrics:
    return ReplayMetrics(
        strategy_key=AUTO_PAPER_TECHNICAL_KEY,
        entry_timeframe="15m",
        total_trades=total_trades,
        signal_count=total_trades,
        win_rate=0.6,
        average_win=0.02,
        average_loss=0.01,
        average_r=1.5,
        average_hold_hours=4.0,
        ladder_level_hits={},
        gross_return=0.1,
        net_return=0.08,
        net_expectancy=0.005,
        total_fee_bps=10.0,
        total_slippage_bps=5.0,
        cost_share_of_gross_profit=0.2,
        sharpe=1.2,
        profit_factor=1.4,
        max_drawdown=0.1,
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
        data_issues=[],
        trades=(),
    )


class _StubReplayService:
    def __init__(self, metrics: ReplayMetrics) -> None:
        self._metrics = metrics

    def replay(self, *, strategy, market_data, start_at=None, end_at=None) -> ReplayMetrics:  # noqa: ANN001
        return self._metrics


def _patch_replay_pipeline(monkeypatch, *, metrics: ReplayMetrics) -> None:
    import scripts.run_top20_technical_validation as replay_module

    monkeypatch.setattr(replay_module, "_load_or_backfill", lambda *, days, end_at: {})
    monkeypatch.setattr(replay_module, "_load_stored", lambda *, days, end_at: {})

    import services.validation.technical_replay as technical_replay_module

    monkeypatch.setattr(
        technical_replay_module,
        "TechnicalStrategyValidationService",
        lambda *args, **kwargs: _StubReplayService(metrics),
    )


class TestComputeAndWriteEdgeStats:
    def test_rejects_when_below_min_trade_samples(self, tmp_path, monkeypatch) -> None:
        import services.execution.signal_edge_stats as edge_stats_module
        from scripts.compute_signal_edge_stats import compute_and_write_edge_stats

        monkeypatch.setattr(edge_stats_module, "EDGE_STATS_ARTIFACT_DIR", tmp_path)
        _patch_replay_pipeline(monkeypatch, metrics=_fake_metrics(total_trades=5))

        result = compute_and_write_edge_stats(
            strategy_key=AUTO_PAPER_TECHNICAL_KEY,
            days=60,
            min_trade_samples=30,
            reuse_stored_data=True,
        )

        assert result.accepted is False
        assert result.total_trades == 25
        assert result.artifact_path is None
        assert result.report_path is not None

    def test_writes_artifact_when_min_trade_samples_met(self, tmp_path, monkeypatch) -> None:
        import services.execution.signal_edge_stats as edge_stats_module

        monkeypatch.setattr(edge_stats_module, "EDGE_STATS_ARTIFACT_DIR", tmp_path)

        evaluation_start = datetime(2026, 5, 1, tzinfo=UTC)
        evaluation_end = datetime(2026, 7, 1, tzinfo=UTC)
        _patch_replay_pipeline(
            monkeypatch,
            metrics=_fake_metrics(total_trades=42, evaluation_start=evaluation_start, evaluation_end=evaluation_end),
        )

        from scripts.compute_signal_edge_stats import compute_and_write_edge_stats

        result = compute_and_write_edge_stats(
            strategy_key=AUTO_PAPER_TECHNICAL_KEY,
            days=60,
            min_trade_samples=30,
            max_age_days=30,
            reuse_stored_data=True,
        )

        assert result.accepted is True
        assert result.total_trades == 210
        assert result.artifact_path is not None
        assert result.selected_candidate_id == "trend_momentum_v1"
        written = json.loads(
            (tmp_path / AUTO_PAPER_TECHNICAL_KEY / "trend_momentum_v1" / "BTCUSDT" / "active.json").read_text(
                encoding="utf-8"
            )
        )
        assert written["sample_count"] == 42
        assert written["win_rate"] == 0.6
        assert written["evaluation_start"] == evaluation_start.isoformat()
        assert written["max_age_days"] == 30
        assert written["average_net_loss_magnitude"] == 0.01

    def test_rejects_unsupported_strategy_key(self) -> None:
        import pytest

        from scripts.compute_signal_edge_stats import compute_and_write_edge_stats

        with pytest.raises(ValueError, match="wired for evidence replay"):
            compute_and_write_edge_stats(strategy_key="not_wired_up")


class TestEvidenceCliDatabaseBoundary:
    def test_compute_cli_requires_database_url_before_running(self, monkeypatch) -> None:
        import scripts.compute_signal_edge_stats as compute_module

        monkeypatch.setattr(
            sys,
            "argv",
            ["compute_signal_edge_stats", "--strategy-key", AUTO_PAPER_TECHNICAL_KEY],
        )

        with pytest.raises(SystemExit) as exc_info:
            compute_module.main()

        assert exc_info.value.code == 2

    def test_validation_cli_requires_database_url_before_running(self, monkeypatch) -> None:
        import scripts.run_top20_technical_validation as validation_module

        monkeypatch.setattr(sys, "argv", ["run_top20_technical_validation", "--days", "60"])

        with pytest.raises(SystemExit) as exc_info:
            validation_module.main()

        assert exc_info.value.code == 2


class TestRefreshSignalEdgeStatsTask:
    def test_notifies_outbox_on_rejection(self, db_session, monkeypatch) -> None:
        import services.execution.tasks as tasks_module
        from services.strategy_library import NotificationRepository

        monkeypatch.setattr(tasks_module, "get_session_factory", lambda: lambda: db_session)

        def _fake_compute(**kwargs):  # noqa: ANN003
            from scripts.compute_signal_edge_stats import EdgeStatsComputationResult

            return EdgeStatsComputationResult(
                accepted=False,
                strategy_key=kwargs["strategy_key"],
                total_trades=3,
                win_rate=0.0,
                average_win=0.0,
                average_loss=0.0,
                min_trade_samples=kwargs["min_trade_samples"],
            )

        import scripts.compute_signal_edge_stats as compute_module

        monkeypatch.setattr(compute_module, "compute_and_write_edge_stats", _fake_compute)

        result = tasks_module.refresh_signal_edge_stats.run(min_trade_samples=30)

        assert result["accepted"] is False
        notifications = NotificationRepository(db_session).list_notifications(event_type="signal_edge_stats_rejected")
        assert len(notifications) == 1
        assert AUTO_PAPER_TECHNICAL_KEY in notifications[0].source_ref

    def test_notifies_outbox_on_acceptance(self, db_session, monkeypatch) -> None:
        import services.execution.tasks as tasks_module
        from services.strategy_library import NotificationRepository

        monkeypatch.setattr(tasks_module, "get_session_factory", lambda: lambda: db_session)

        def _fake_compute(**kwargs):  # noqa: ANN003
            from scripts.compute_signal_edge_stats import EdgeStatsComputationResult

            return EdgeStatsComputationResult(
                accepted=True,
                strategy_key=kwargs["strategy_key"],
                total_trades=50,
                win_rate=0.58,
                average_win=0.02,
                average_loss=0.01,
                min_trade_samples=kwargs["min_trade_samples"],
                artifact_path="artifacts/signal_edge_stats/x/active.json",
            )

        import scripts.compute_signal_edge_stats as compute_module

        monkeypatch.setattr(compute_module, "compute_and_write_edge_stats", _fake_compute)

        result = tasks_module.refresh_signal_edge_stats.run()

        assert result["accepted"] is True
        notifications = NotificationRepository(db_session).list_notifications(event_type="signal_edge_stats_refreshed")
        assert len(notifications) == 1
