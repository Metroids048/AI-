"""Resolve PaperRun account equity from exchange snapshots or persisted run state."""

from __future__ import annotations

from datetime import UTC, datetime

from services.data.universe import exchange_to_platform_symbol
from services.execution.gateway import ExchangeGateway
from services.strategy_library import ExecutionRepository
from shared.models import ExchangeAccountSnapshot, PaperRun, TradeSide

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


def resolve_manual_position_pnl(
    *,
    paper_run: PaperRun,
    execution_repo: ExecutionRepository,
    gateway: ExchangeGateway | None,
) -> float:
    """Sum unrealized PnL of exchange positions NOT owned by this strategy run.

    Manual/external positions (e.g. an operator's own long held outside the
    automated strategy) must not distort the strategy's own drawdown math —
    otherwise a manual position's unrealized loss can trip the strategy's
    risk gate and block unrelated automated entries. Every symbol/side found
    on the exchange is checked against find_unmanaged_position_record; only
    positions with no managed-strategy record contribute to the total.

    Returns 0.0 (conservative — no adjustment) if the gateway is unavailable
    or the reconcile call fails, so a transient API error never accidentally
    masks a real drawdown.
    """
    if gateway is None or not paper_run.paper_run_id:
        return 0.0
    try:
        snapshot = gateway.reconcile(live_run_id=f"paper-testnet:{paper_run.paper_run_id}")
        open_positions = snapshot.get("open_positions", [])

        capability = getattr(gateway, "capability", None)
        exchange_name = str(getattr(capability, "exchange", None) or "paper")
        market_type = str(getattr(capability, "market_type", None) or "paper")
        backend = str(getattr(gateway, "api_backend", None) or "local")
        exchange_account = f"{exchange_name}:{market_type}:{backend}"

        manual_pnl = 0.0
        for pos in open_positions:
            raw_symbol = str(pos.get("symbol") or "")
            symbol = exchange_to_platform_symbol(raw_symbol) or raw_symbol
            side_str = str(pos.get("side") or "").lower()
            side = TradeSide.SHORT if side_str == "short" else TradeSide.LONG

            record = execution_repo.find_unmanaged_position_record(
                exchange_account=exchange_account,
                symbol=symbol,
                position_side=side,
                run_id=paper_run.paper_run_id,
            )
            if record is not None:
                manual_pnl += float(pos.get("unrealized_pnl") or 0.0)
        return manual_pnl
    except Exception:  # noqa: BLE001 - conservative: no adjustment on gateway failure
        return 0.0


def resolve_strategy_attributed_equity(
    *,
    paper_run: PaperRun,
    execution_repo: ExecutionRepository | None = None,
    gateway: ExchangeGateway | None = None,
    prefer_exchange: bool = False,
) -> tuple[float, float]:
    """Return (strategy_equity, manual_pnl) — account equity with manual/external
    position PnL excluded, so risk gates only ever see the strategy's own performance.
    """
    account_equity, _source = resolve_paper_account_equity(
        paper_run=paper_run,
        execution_repo=execution_repo,
        gateway=gateway,
        prefer_exchange=prefer_exchange,
    )
    manual_pnl = (
        resolve_manual_position_pnl(paper_run=paper_run, execution_repo=execution_repo, gateway=gateway)
        if execution_repo is not None
        else 0.0
    )
    return account_equity - manual_pnl, manual_pnl


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
    baseline_initialized = bool(metrics.get("account_equity_baseline_initialized"))
    if source in {"gateway_live", "exchange_account_snapshot"} and not baseline_initialized:
        equity_peak = equity
        baseline_initialized = True
    else:
        equity_peak = max(float(metrics.get("equity_peak", equity)), equity)
    return {
        **metrics,
        "account_equity": equity,
        "equity_peak": equity_peak,
        "account_equity_source": source,
        "account_equity_baseline_initialized": baseline_initialized,
        "account_equity_synced_at": datetime.now(UTC).isoformat(),
    }
