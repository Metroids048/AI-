from __future__ import annotations

from datetime import UTC, datetime

from services.execution.account_equity import (
    BOOTSTRAP_EQUITY_SEED,
    resolve_paper_account_equity,
    sync_paper_account_equity,
)
from services.strategy_library import ExecutionRepository
from shared.models import ExchangeAccountSnapshot, PaperRun


class _StubGateway:
    def account_equity(self) -> float:
        return 5_250.75

    def sync_account(self, *, live_run_id: str) -> ExchangeAccountSnapshot:
        return ExchangeAccountSnapshot(
            live_run_id=live_run_id,
            exchange="binance",
            wallet_balance=5_250.75,
            available_balance=5_100.0,
            margin_balance=5_250.75,
            snapshot_time=datetime.now(UTC),
        )


def test_resolve_prefers_exchange_snapshot_over_bootstrap(db_session) -> None:
    repo = ExecutionRepository(db_session)
    repo.create_account_snapshot(
        ExchangeAccountSnapshot(
            live_run_id="console_probe",
            exchange="binance",
            wallet_balance=5_250.75,
            available_balance=5_100.0,
            margin_balance=5_250.75,
            snapshot_time=datetime.now(UTC),
        )
    )
    paper_run = PaperRun(
        strategy_id="s1",
        execution_profile={"account_equity": 10_000},
        paper_metrics_summary={},
    )
    equity, source = resolve_paper_account_equity(paper_run=paper_run, execution_repo=repo)
    assert equity == 5_250.75
    assert source == "exchange_account_snapshot"


def test_resolve_prefers_live_gateway_when_requested() -> None:
    paper_run = PaperRun(strategy_id="s1", execution_profile={"account_equity": 10_000})
    equity, source = resolve_paper_account_equity(
        paper_run=paper_run,
        gateway=_StubGateway(),
        prefer_exchange=True,
    )
    assert equity == 5_250.75
    assert source == "gateway_live"


def test_sync_writes_snapshot_and_metrics(db_session) -> None:
    repo = ExecutionRepository(db_session)
    paper_run = PaperRun(
        paper_run_id="paper-1",
        strategy_id="s1",
        execution_profile={"account_equity": 10_000, "equity_peak": 10_000},
        paper_metrics_summary={"equity_peak": 10_000},
    )
    metrics = sync_paper_account_equity(
        paper_run=paper_run,
        metrics=dict(paper_run.paper_metrics_summary),
        execution_repo=repo,
        gateway=_StubGateway(),
        prefer_exchange=True,
        paper_run_id="paper-1",
    )
    assert metrics["account_equity"] == 5_250.75
    assert metrics["account_equity_source"] == "gateway_live"
    snapshots = repo.list_account_snapshots()
    assert len(snapshots) == 1
    assert snapshots[0].wallet_balance == 5_250.75


def test_bootstrap_seed_only_when_nothing_else_exists() -> None:
    paper_run = PaperRun(strategy_id="s1", execution_profile={}, paper_metrics_summary={})
    equity, source = resolve_paper_account_equity(paper_run=paper_run)
    assert equity == BOOTSTRAP_EQUITY_SEED
    assert source == "bootstrap_seed"


def test_first_exchange_sync_rebases_bootstrap_peak_to_account_equity(db_session) -> None:
    repo = ExecutionRepository(db_session)
    repo.create_account_snapshot(
        ExchangeAccountSnapshot(
            live_run_id="console_probe",
            exchange="binance",
            wallet_balance=5_250.75,
            available_balance=5_100.0,
            margin_balance=5_250.75,
            snapshot_time=datetime.now(UTC),
        )
    )
    paper_run = PaperRun(
        strategy_id="s1",
        execution_profile={"account_equity": 10_000},
        paper_metrics_summary={"account_equity": 10_000, "equity_peak": 10_000},
    )

    metrics = sync_paper_account_equity(
        paper_run=paper_run,
        metrics=dict(paper_run.paper_metrics_summary),
        execution_repo=repo,
    )

    assert metrics["account_equity"] == 5_250.75
    assert metrics["equity_peak"] == 5_250.75
    assert metrics["account_equity_baseline_initialized"] is True
