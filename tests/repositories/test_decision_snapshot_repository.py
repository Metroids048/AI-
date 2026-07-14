from __future__ import annotations

from datetime import UTC, datetime, timedelta

from services.strategy_library.repository import (
    DecisionSnapshotRepository,
    PaperRunRepository,
    StrategyRepository,
)
from shared.models import (
    DecisionSnapshot,
    PaperRun,
    StrategyCreate,
    StrategyRules,
    StrategyVersion,
)


def _create_paper_run(db_session) -> PaperRun:
    strategy_repo = StrategyRepository(db_session)
    paper_repo = PaperRunRepository(db_session)
    strategy = strategy_repo.create_strategy(
        StrategyCreate(
            strategy_key="Decision_Snapshot_Test_v1",
            source="manual",
            core_thesis="directional",
            rules=StrategyRules(
                entry_rules={"trend": "ema_cross"},
                exit_rules={"trend_reverse": True},
                stoploss_rules={"atr_multiple": 2.0},
                takeprofit_rules={"atr_multiple": 3.0},
                position_rules={"risk_per_trade": 0.01},
            ),
        )
    )
    version = strategy_repo.create_version(
        StrategyVersion(
            strategy_id=strategy.strategy_id,
            version_label="v1",
            change_summary="decision snapshot test",
        )
    )
    return paper_repo.create_paper_run(
        PaperRun(
            strategy_id=strategy.strategy_id,
            version_id=version.version_id,
            symbol_scope=["BTC/USDT"],
            candidate_symbols=["BTC/USDT"],
            selection_basis="binance_top20_quote_volume",
        )
    )


def test_create_and_list_decision_snapshots(db_session) -> None:
    paper_run = _create_paper_run(db_session)
    repo = DecisionSnapshotRepository(db_session)
    now = datetime.now(UTC)

    created = repo.create_snapshot(
        DecisionSnapshot(
            paper_run_id=paper_run.paper_run_id,
            symbol="BTC/USDT",
            action="skip_no_trade_decision",
            pipeline_status="technical_signals_insufficient",
            reason="technical_signals_insufficient",
            decision_trace={"pipeline_status": "technical_signals_insufficient", "signals": []},
            cycle_time=now,
        )
    )
    assert created.decision_snapshot_id is not None
    assert created.pipeline_status == "technical_signals_insufficient"

    fetched = repo.list_snapshots(paper_run_id=paper_run.paper_run_id, symbol="BTC/USDT")
    assert len(fetched) == 1
    assert fetched[0].decision_trace["pipeline_status"] == "technical_signals_insufficient"


def test_list_decision_snapshots_filters_by_pipeline_status_and_since(db_session) -> None:
    paper_run = _create_paper_run(db_session)
    repo = DecisionSnapshotRepository(db_session)
    now = datetime.now(UTC)
    old = now - timedelta(days=10)

    repo.create_snapshot(
        DecisionSnapshot(
            paper_run_id=paper_run.paper_run_id,
            symbol="BTC/USDT",
            action="skip_no_trade_decision",
            pipeline_status="confirmation_unavailable_fail_closed",
            decision_trace={"pipeline_status": "confirmation_unavailable_fail_closed"},
            cycle_time=old,
        )
    )
    repo.create_snapshot(
        DecisionSnapshot(
            paper_run_id=paper_run.paper_run_id,
            symbol="BTC/USDT",
            action="open_long",
            pipeline_status="confirmed",
            decision_trace={"pipeline_status": "confirmed"},
            cycle_time=now,
        )
    )

    since = now - timedelta(days=7)
    recent = repo.list_snapshots(paper_run_id=paper_run.paper_run_id, since=since)
    assert len(recent) == 1
    assert recent[0].pipeline_status == "confirmed"

    rejections_only = repo.list_snapshots(
        paper_run_id=paper_run.paper_run_id,
        pipeline_status="confirmation_unavailable_fail_closed",
    )
    assert len(rejections_only) == 1
    assert rejections_only[0].pipeline_status == "confirmation_unavailable_fail_closed"
