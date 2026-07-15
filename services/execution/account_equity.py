"""Resolve PaperRun account equity from exchange snapshots or persisted run state."""

from __future__ import annotations

from datetime import UTC, datetime

from services.execution.gateway import ExchangeGateway
from services.strategy_library import ExecutionRepository
from shared.models import ExchangeAccountSnapshot, PaperRun

# Bootstrap seed only — used when no exchange snapshot or run metrics exist yet.
BOOTSTRAP_EQUITY_SEED = 10_000.0


def latest_exchange_snapshot(
    execution_repo: ExecutionRepository,
) -> ExchangeAccountSnapshot | None:
    snapshots = execution_repo.list_account_snapshots()
    if not snapshots:
        return None
    return max(
        snapshots,
        key=lambda item: item.snapshot_time or datetime.min.replace(tzinfo=UTC),
    )


def resolve_paper_account_equity(
    *,
    paper_run: PaperRun,
    execution_repo: ExecutionRepository | None = None,
    gateway: ExchangeGateway | None = None,
    prefer_exchange: bool = False,
) -> tuple[float, str]:
    """Return (equity, source) with the freshest trustworthy balance available."""

    if prefer_exchange and gateway is not None:
        try:
            equity = float(gateway.account_equity())
            if equity > 0:
                return equity, "gateway_live"
        except Exception:
            pass

    if execution_repo is not None:
        snapshot = latest_exchange_snapshot(execution_repo)
        if snapshot is not None:
            equity = float(snapshot.margin_balance or snapshot.wallet_balance)
            if equity > 0:
                return equity, "exchange_account_snapshot"

    metrics_equity = paper_run.paper_metrics_summary.get("account_equity")
    if metrics_equity is not None:
        equity = float(metrics_equity)
        if equity > 0:
            return equity, "paper_metrics_summary"

    profile_equity = paper_run.execution_profile.get("account_equity")
    if profile_equity is not None:
        equity = float(profile_equity)
        if equity > 0:
            return equity, "execution_profile"

    return BOOTSTRAP_EQUITY_SEED, "bootstrap_seed"


def sync_paper_account_equity(
    *,
    paper_run: PaperRun,
    metrics: dict,
    execution_repo: ExecutionRepository,
    gateway: ExchangeGateway | None = None,
    prefer_exchange: bool = False,
    paper_run_id: str | None = None,
) -> dict:
    """Refresh metrics account_equity from exchange when mirror mode is armed."""

    if prefer_exchange and gateway is not None:
        try:
            snapshot = gateway.sync_account(live_run_id=f"paper:{paper_run_id or 'unknown'}")
            execution_repo.create_account_snapshot(snapshot)
        except Exception:
            pass

    equity, source = resolve_paper_account_equity(
        paper_run=paper_run,
        execution_repo=execution_repo,
        gateway=gateway,
        prefer_exchange=prefer_exchange,
    )
    if equity <= 0:
        return metrics
    equity_peak = max(float(metrics.get("equity_peak", equity)), equity)
    return {
        **metrics,
        "account_equity": equity,
        "equity_peak": equity_peak,
        "account_equity_source": source,
        "account_equity_synced_at": datetime.now(UTC).isoformat(),
    }
