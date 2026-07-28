"""Runtime Truth API: exchange facts, local projections, and decision reasons."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import UTC, datetime
from threading import Lock
from typing import Literal

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy.orm import Session

from apps.api.auth import websocket_token_is_valid
from services.database import get_db_session, get_session_factory
from services.execution.gateway import configured_gateways
from services.execution.runtime_state import load_external_scheduler_state
from services.strategy_library import (
    DecisionFunnelRepository,
    ExecutionRepository,
    LlmInvocationRepository,
    PaperRunRepository,
)
from shared.models import PaperRun, RuntimeDatum
from shared.models.execution_truth import ExchangeOrderState, ExecutionMode

router = APIRouter(prefix="/runtime", tags=["runtime-truth"])
_EXCHANGE_CACHE_SECONDS = 15.0
_EXCHANGE_TRUTH_TIMEOUT_SECONDS = 8.0
_exchange_cache_lock = Lock()
_exchange_probe_lock = Lock()
_exchange_cache: tuple[datetime, dict] | None = None
_exchange_truth_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="runtime-exchange-truth")


def _datum(
    *,
    value: object | None,
    source: str,
    observed_at: datetime | None,
    status: Literal["available", "stale", "unavailable"] = "available",
    error: str | None = None,
) -> dict:
    freshness = "unavailable" if status == "unavailable" else ("stale" if status == "stale" else "current")
    return RuntimeDatum(
        value=value,
        source=source,
        observed_at=observed_at,
        freshness=freshness,
        status=status,
        error=error,
    ).model_dump(mode="json")


def _cached_exchange_truth(*, allow_stale: bool) -> dict | None:
    with _exchange_cache_lock:
        if _exchange_cache is None:
            return None
        age = (datetime.now(UTC) - _exchange_cache[0]).total_seconds()
        if age < _EXCHANGE_CACHE_SECONDS or allow_stale:
            cached = dict(_exchange_cache[1])
            if allow_stale and age >= _EXCHANGE_CACHE_SECONDS and cached.get("status") == "available":
                cached["status"] = "stale"
                cached["freshness"] = "stale"
            return cached
        return None


def _exchange_truth() -> dict:
    global _exchange_cache
    fresh = _cached_exchange_truth(allow_stale=False)
    if fresh is not None:
        return fresh
    # One probe at a time. Concurrent UI polls must not queue behind a hung
    # Binance reconcile on the single worker thread.
    if not _exchange_probe_lock.acquire(blocking=False):
        stale = _cached_exchange_truth(allow_stale=True)
        if stale is not None:
            return stale
        return _datum(
            value=None,
            source="BINANCE_USDT_M_TESTNET",
            observed_at=datetime.now(UTC),
            status="unavailable",
            error="exchange truth probe already in progress",
        )
    try:
        observed_at = datetime.now(UTC)
        try:
            gateway = configured_gateways()[0]
            future = _exchange_truth_executor.submit(
                gateway.reconcile,
                live_run_id=f"runtime-truth:{observed_at.isoformat()}",
            )
            try:
                snapshot = future.result(timeout=_EXCHANGE_TRUTH_TIMEOUT_SECONDS)
            except FutureTimeoutError as exc:
                future.cancel()
                raise TimeoutError(f"exchange truth probe exceeded {_EXCHANGE_TRUTH_TIMEOUT_SECONDS:.0f}s") from exc
            if "open_positions" not in snapshot:
                raise ValueError("exchange snapshot omitted open_positions")
            result = _datum(
                value={
                    "positions": snapshot["open_positions"],
                    "open_orders": snapshot.get("open_orders") or [],
                    "reconciliation_status": snapshot.get("reconciliation_status"),
                    "notes": snapshot.get("notes") or [],
                },
                source="BINANCE_USDT_M_TESTNET",
                observed_at=observed_at,
            )
        except Exception as exc:  # noqa: BLE001 - unavailable must be explicit
            stale = _cached_exchange_truth(allow_stale=True)
            if stale is not None and stale.get("value") is not None:
                stale = dict(stale)
                stale["status"] = "stale"
                stale["freshness"] = "stale"
                stale["error"] = str(exc)
                result = stale
            else:
                result = _datum(
                    value=None,
                    source="BINANCE_USDT_M_TESTNET",
                    observed_at=observed_at,
                    status="unavailable",
                    error=str(exc),
                )
        with _exchange_cache_lock:
            _exchange_cache = (datetime.now(UTC), result)
        return result
    finally:
        _exchange_probe_lock.release()


def _platform_symbol(value: object) -> str:
    symbol = str(value or "")
    return symbol[:-5] if symbol.endswith(":USDT") else symbol


def _position_truth(
    *,
    exchange: dict,
    local_open: list,
    observed_at: datetime,
) -> tuple[dict, set[str]]:
    exchange_positions: dict[tuple[str, str], float] = {}
    for item in (exchange.get("value") or {}).get("positions") or []:
        if not isinstance(item, dict):
            continue
        symbol = _platform_symbol(item.get("symbol"))
        side = str(item.get("side") or "").lower()
        quantity = abs(float(item.get("contracts") or item.get("positionAmt") or 0.0))
        if symbol and side and quantity > 0:
            exchange_positions[(symbol, side)] = quantity
    local_positions = {
        (item.symbol, item.position_side.value): abs(float(item.quantity))
        for item in local_open
        if abs(float(item.quantity)) > 0
    }
    exchange_keys = set(exchange_positions)
    local_keys = set(local_positions)
    quantity_mismatches = [
        {
            "symbol": symbol,
            "side": side,
            "exchange_quantity": exchange_positions[(symbol, side)],
            "local_quantity": local_positions[(symbol, side)],
        }
        for symbol, side in sorted(exchange_keys & local_keys)
        if abs(exchange_positions[(symbol, side)] - local_positions[(symbol, side)]) > 1e-12
    ]
    exchange_only = sorted(exchange_keys - local_keys)
    local_only = sorted(local_keys - exchange_keys)
    blocked_symbols = {symbol for symbol, _side in [*exchange_only, *local_only]}
    blocked_symbols.update(item["symbol"] for item in quantity_mismatches)
    available = exchange.get("status") == "available"
    consistent = available and not exchange_only and not local_only and not quantity_mismatches
    return (
        _datum(
            value={
                "exchange_only_positions": [{"symbol": symbol, "side": side} for symbol, side in exchange_only],
                "local_only_positions": [{"symbol": symbol, "side": side} for symbol, side in local_only],
                "quantity_mismatches": quantity_mismatches,
                "consistent": consistent,
            },
            source="RUNTIME_RECONCILIATION_COMPARISON",
            observed_at=observed_at,
            status="available" if available else "unavailable",
            error=exchange.get("error"),
        ),
        blocked_symbols,
    )


def _exchange_linked_positions(local_open: list) -> list:
    return [
        item
        for item in local_open
        if item.execution_mode is ExecutionMode.BINANCE_TESTNET
        or item.exchange_account.startswith("binance:usdt_perpetual:testnet")
    ]


def _select_strategy_evidence_run(paper_runs: list[PaperRun]) -> PaperRun | None:
    active_directional = [
        run
        for run in paper_runs
        if run.execution_profile.get("strategy_lane") == "directional" and run.paper_status in {"running", "locked"}
    ]
    if not active_directional:
        return None

    def _rank(run: PaperRun) -> tuple[int, str]:
        profile = run.execution_profile or {}
        testnet_rank = 0 if str(profile.get("execution_mode") or "") == "binance_testnet" else 1
        return (testnet_rank, run.paper_run_id or "")

    return min(active_directional, key=_rank)


def _strategy_evidence_datum(*, paper_runs: list[PaperRun], observed_at: datetime) -> dict:
    paper_run = _select_strategy_evidence_run(paper_runs)
    if paper_run is None:
        return _datum(
            value=None,
            source="PAPER_RUN_REPOSITORY",
            observed_at=observed_at,
            status="unavailable",
            error="no active directional paper run",
        )
    profile = paper_run.execution_profile or {}
    sampling_enabled = bool(profile.get("simulation_sampling_fallback_enabled"))
    return _datum(
        value={
            "paper_run_id": paper_run.paper_run_id,
            "strategy_lane": profile.get("strategy_lane"),
            "acceptance_symbols": profile.get("acceptance_symbols") or paper_run.candidate_symbols,
            "simulation_sampling_fallback_enabled": sampling_enabled,
            "execution_mode": profile.get("execution_mode"),
            "evidence_class": "NON_PROMOTABLE_PIPELINE_SAMPLE" if sampling_enabled else None,
        },
        source="PAPER_RUN_REPOSITORY",
        observed_at=observed_at,
    )


@router.get("/snapshot")
def runtime_snapshot(db: Session = Depends(get_db_session)) -> dict:
    observed_at = datetime.now(UTC)
    execution_repo = ExecutionRepository(db)
    exchange = _exchange_truth()
    position_records = execution_repo.list_position_records(limit=200)
    local_open = [
        item for item in position_records if item.management_status.value not in {"CLOSED", "RECONCILED_GHOST"}
    ]
    mismatch, _blocked_symbols = _position_truth(
        exchange=exchange,
        local_open=_exchange_linked_positions(local_open),
        observed_at=observed_at,
    )
    scheduler = load_external_scheduler_state(now=observed_at)
    paper_runs = PaperRunRepository(db).list_paper_runs()
    return {
        "observed_at": observed_at.isoformat(),
        "exchange": exchange,
        "local_projection": _datum(
            value=[item.model_dump(mode="json") for item in local_open],
            source="LOCAL_SQL_PROJECTION",
            observed_at=observed_at,
        ),
        "mismatch": mismatch,
        "scheduler": _datum(
            value={
                **scheduler.__dict__,
                "heartbeat_at": (scheduler.heartbeat_at.isoformat() if scheduler.heartbeat_at else None),
                "last_auto_cycle_at": (
                    scheduler.last_auto_cycle_at.isoformat() if scheduler.last_auto_cycle_at else None
                ),
            },
            source="RUNTIME_SCHEDULER_HEARTBEAT",
            observed_at=scheduler.heartbeat_at,
            status="available" if scheduler.running else "unavailable",
            error=None if scheduler.running else scheduler.reason,
        ),
        "data_freshness": _datum(
            value={
                "data_fresh": scheduler.data_fresh,
                "exchange_info_ready": scheduler.exchange_info_ready,
                "heartbeat_at": (scheduler.heartbeat_at.isoformat() if scheduler.heartbeat_at else None),
                "running": scheduler.running,
            },
            source="RUNTIME_SCHEDULER_HEARTBEAT",
            observed_at=scheduler.heartbeat_at,
            status="available" if scheduler.running else "unavailable",
            error=None if scheduler.running else scheduler.reason,
        ),
        "strategy_evidence": _strategy_evidence_datum(paper_runs=paper_runs, observed_at=observed_at),
        "protections": _datum(
            value=[item.model_dump(mode="json") for item in execution_repo.list_protection_records(limit=200)],
            source="LOCAL_SQL_PROJECTION",
            observed_at=observed_at,
        ),
    }


@router.get("/decisions")
def runtime_decisions(
    symbol: str | None = None,
    since: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    cursor: str | None = None,
    db: Session = Depends(get_db_session),
) -> dict:
    del cursor
    items = DecisionFunnelRepository(db).list_terminals(
        symbol=symbol,
        since=since,
        limit=limit,
    )
    return {
        "items": [item.model_dump(mode="json") for item in items],
        "next_cursor": items[-1].terminal_id if len(items) == limit else None,
    }


@router.get("/exchange-orders")
def runtime_exchange_orders(
    symbol: str | None = None,
    since: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    cursor: str | None = None,
    db: Session = Depends(get_db_session),
) -> dict:
    del cursor
    items = ExecutionRepository(db).list_exchange_orders(symbol=symbol, limit=limit)
    if since is not None:
        items = [item for item in items if item.created_at is not None and item.created_at >= since]
    return {
        "items": [item.model_dump(mode="json") for item in items],
        "next_cursor": (items[-1].exchange_order_record_id if len(items) == limit else None),
    }


@router.get("/positions")
def runtime_positions(db: Session = Depends(get_db_session)) -> dict:
    return {
        "exchange": _exchange_truth(),
        "local": _datum(
            value=[item.model_dump(mode="json") for item in ExecutionRepository(db).list_position_records(limit=200)],
            source="LOCAL_SQL_PROJECTION",
            observed_at=datetime.now(UTC),
        ),
    }


@router.get("/llm-invocations")
def runtime_llm_invocations(
    symbol: str | None = None,
    since: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    cursor: str | None = None,
    db: Session = Depends(get_db_session),
) -> dict:
    del cursor
    items = LlmInvocationRepository(db).list_invocations(
        symbol=symbol,
        since=since,
        limit=limit,
    )
    return {
        "items": [item.model_dump(mode="json") for item in items],
        "next_cursor": items[-1].invocation_id if len(items) == limit else None,
        "status": "available",
        "source": "LLM_INVOCATION_JOURNAL",
        "error": None,
    }


@router.get("/reconciliation")
def runtime_reconciliation(db: Session = Depends(get_db_session)) -> dict:
    observed_at = datetime.now(UTC)
    exchange = _exchange_truth()
    execution_repo = ExecutionRepository(db)
    local_open = [
        item
        for item in execution_repo.list_position_records(limit=200)
        if item.management_status.value not in {"CLOSED", "RECONCILED_GHOST"}
    ]
    mismatch, blocked_symbols = _position_truth(
        exchange=exchange,
        local_open=_exchange_linked_positions(local_open),
        observed_at=observed_at,
    )
    paper_runs = PaperRunRepository(db).list_paper_runs()
    active_kill_switch_runs = [
        run for run in paper_runs if bool(run.paper_metrics_summary.get("entry_kill_switch_active", False))
    ]
    consecutive_failures = max(
        (int(run.paper_metrics_summary.get("reconciliation_consecutive_failures", 0)) for run in paper_runs),
        default=0,
    )
    unknown_orders = [
        order
        for order in execution_repo.list_exchange_orders(limit=500)
        if order.state is ExchangeOrderState.EXCHANGE_UNKNOWN
    ]
    if exchange.get("status") != "available":
        reconciliation_status = "unavailable"
        blocked_symbols = {"BTC/USDT", "ETH/USDT"}
    elif active_kill_switch_runs:
        reconciliation_status = "degraded"
        blocked_symbols.update({"BTC/USDT", "ETH/USDT"})
    elif unknown_orders:
        reconciliation_status = "degraded"
        blocked_symbols.update(order.symbol for order in unknown_orders)
    elif mismatch["value"]["consistent"]:
        reconciliation_status = "healthy"
    else:
        reconciliation_status = "degraded"
    actions = []
    if active_kill_switch_runs:
        actions.append("ENTRY_KILL_SWITCH_ACTIVE")
    if unknown_orders:
        actions.append("EXCHANGE_UNKNOWN_REQUIRES_RECONCILIATION")
    return {
        "status": reconciliation_status,
        "entry_blocked_symbols": sorted(blocked_symbols),
        "actions": actions,
        "snapshot": exchange,
        "mismatch": mismatch,
        "error": (
            exchange.get("error")
            or ("entry kill switch remains active" if active_kill_switch_runs else None)
            or ("unresolved exchange orders remain" if unknown_orders else None)
        ),
        "consecutive_failures": consecutive_failures,
        "entry_kill_switch_active": bool(active_kill_switch_runs),
        "unresolved_exchange_order_ids": [order.exchange_order_record_id for order in unknown_orders],
    }


@router.websocket("/events")
async def runtime_events(
    websocket: WebSocket,
    token: str = Query(default=""),
) -> None:
    if not websocket_token_is_valid(token):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await websocket.accept()
    try:
        while True:
            with get_session_factory()() as db:
                latest = DecisionFunnelRepository(db).list_terminals(limit=20)
                orders = ExecutionRepository(db).list_exchange_orders(limit=20)
                receipts = ExecutionRepository(db).list_exchange_fill_receipts(limit=20)
                protections = ExecutionRepository(db).list_protection_records(limit=20)
                llm_invocations = LlmInvocationRepository(db).list_invocations(limit=20)
                await websocket.send_json(
                    {
                        "event": "runtime_truth",
                        "observed_at": datetime.now(UTC).isoformat(),
                        "decisions": [item.model_dump(mode="json") for item in latest],
                        "exchange_orders": [item.model_dump(mode="json") for item in orders],
                        "fill_receipts": [item.model_dump(mode="json") for item in receipts],
                        "protections": [item.model_dump(mode="json") for item in protections],
                        "llm_invocations": [item.model_dump(mode="json") for item in llm_invocations],
                    }
                )
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        return
