from __future__ import annotations

from datetime import UTC, datetime

from scripts.compute_signal_edge_stats import (
    build_artifact_payload,
    evidence_failure_reasons,
    select_best_candidate,
)
from services.validation.technical_replay import ReplayMetrics


def _metrics(**overrides) -> ReplayMetrics:
    payload = {
        "strategy_key": "candidate",
        "entry_timeframe": "15m",
        "total_trades": 40,
        "signal_count": 50,
        "win_rate": 0.55,
        "average_win": 0.02,
        "average_loss": -0.01,
        "average_r": 0.4,
        "average_hold_hours": 3.0,
        "ladder_level_hits": {},
        "gross_return": 0.2,
        "net_return": 0.1,
        "net_expectancy": 0.0025,
        "total_fee_bps": 400.0,
        "total_slippage_bps": 80.0,
        "cost_share_of_gross_profit": 0.1,
        "sharpe": 1.4,
        "profit_factor": 1.5,
        "max_drawdown": 0.20,
        "evaluation_start": datetime(2025, 1, 1, tzinfo=UTC),
        "evaluation_end": datetime(2026, 1, 1, tzinfo=UTC),
        "data_issues": [],
        "trades": (),
    }
    payload.update(overrides)
    return ReplayMetrics(**payload)


def test_artifact_payload_normalizes_loss_magnitude_and_records_post_cost_expectancy() -> None:
    payload = build_artifact_payload(
        strategy_key="auto_paper_mature_templates",
        candidate_id="trend_momentum_v1",
        symbol="BTC/USDT",
        rules={"entry_rules": {"candidate_id": "trend_momentum_v1"}},
        full_metrics=_metrics(total_trades=100),
        oos_metrics=_metrics(total_trades=40),
        min_oos_trades=30,
        max_age_days=30,
        computed_at=datetime(2026, 1, 2, tzinfo=UTC),
    )

    assert payload["schema_version"] == 2
    assert payload["average_net_loss_magnitude"] == 0.01
    assert payload["net_expectancy"] == 0.0025
    assert payload["oos_sample_count"] == 40
    assert payload["eligible"] is True


def test_evidence_requires_oos_samples_and_all_canonical_thresholds() -> None:
    failed = evidence_failure_reasons(
        _metrics(total_trades=10, sharpe=1.0, profit_factor=1.3, max_drawdown=0.25, net_expectancy=0.0),
        min_oos_trades=30,
    )
    assert set(failed) == {
        "insufficient_oos_trades",
        "sharpe_not_above_1",
        "profit_factor_not_above_1_3",
        "max_drawdown_not_below_25pct",
        "net_expectancy_not_positive",
    }


def test_candidate_selection_prefers_coverage_then_worst_symbol_expectancy_then_simplicity() -> None:
    selected = select_best_candidate(
        [
            {
                "candidate_id": "wide",
                "eligible": True,
                "symbol": "BTC/USDT",
                "net_expectancy": 0.003,
                "signal_count": 10,
            },
            {
                "candidate_id": "wide",
                "eligible": True,
                "symbol": "ETH/USDT",
                "net_expectancy": 0.001,
                "signal_count": 10,
            },
            {
                "candidate_id": "focused",
                "eligible": True,
                "symbol": "BTC/USDT",
                "net_expectancy": 0.004,
                "signal_count": 3,
            },
            {
                "candidate_id": "focused",
                "eligible": True,
                "symbol": "ETH/USDT",
                "net_expectancy": 0.002,
                "signal_count": 3,
            },
            {
                "candidate_id": "single",
                "eligible": True,
                "symbol": "BTC/USDT",
                "net_expectancy": 0.02,
                "signal_count": 1,
            },
        ]
    )
    assert selected == "focused"
