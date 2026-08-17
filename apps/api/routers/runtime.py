"""Runtime Truth API: exchange facts, local projections, and decision reasons."""

from __future__ import annotations

import asyncio
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Literal

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.auth import websocket_token_is_valid
from services.automated_trading.domain.client_order_id import is_v2_client_order_id
from services.automated_trading.domain.enums import V2ExecutionMode
from services.automated_trading.infrastructure.models import (
    V2ExchangeFill,
    V2ExchangeOrder,
    V2ExecutionIntent,
    V2ManagedPosition,
    V2ProtectionRecord,
)
from services.automated_trading.infrastructure.repository import AutomatedTradingRepository
from services.database import get_db_session, get_session_factory
from services.execution.gateway import configured_gateways
from services.execution.runtime_state import load_external_scheduler_state
from services.strategy_library import (
    LlmInvocationRepository,
    PaperRunRepository,
)
from shared.models import PaperRun, RuntimeDatum

router = APIRouter(prefix="/runtime", tags=["runtime-truth"])
# Serve-from-cache window. This MUST exceed the console poll interval
# (FALLBACK_INTERVAL_MS = 30s in frontend/admin/src/hooks/useRuntimeTruth.js).
# It was previously 15s, i.e. shorter than the poll interval, so every single UI poll
# found an expired cache and paid the full reconcile latency. Measured reconcile cost
# on this host is 4-9s, so any jitter past the request budget flipped the console to
# "unavailable" at random.
_EXCHANGE_CACHE_SECONDS = 45.0
# Age past which a cached snapshot is refreshed in the background while still being
# served immediately. Keeps the console responsive without ever serving data that is
# older than one poll cycle.
_EXCHANGE_BACKGROUND_REFRESH_SECONDS = 20.0
# Age past which cached data is too old to serve without confirmation; the caller waits
# for a live probe instead.
_EXCHANGE_MAX_SERVE_SECONDS = 90.0
# One reconcile is ~6 sequential authenticated Binance calls, and the first probe in
# a fresh API process also pays CCXT load_markets(). Measured cold cost on this host
# exceeded the previous 8s budget, so every probe timed out and the console stayed
# permanently "unavailable". This value is a request-side responsiveness budget, not a
# probe deadline: a probe that outlives its waiter keeps running and caches its own
# result, so the next poll is served from real exchange data instead of restarting
# the work from scratch.
_EXCHANGE_TRUTH_TIMEOUT_SECONDS = 20.0
_exchange_cache_lock = Lock()
_exchange_probe_lock = Lock()
_exchange_cache: tuple[datetime, dict] | None = None
_exchange_inflight: Future | None = None
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


def _cached_exchange_age_seconds() -> float | None:
    with _exchange_cache_lock:
        if _exchange_cache is None:
            return None
        return (datetime.now(UTC) - _exchange_cache[0]).total_seconds()


def _tag_order_ownership(order: dict) -> dict:
    """Label an exchange open order as system-owned or externally placed.

    V2 issues `A2E-`/`A2S-`/`A2T-` client ids. Anything else (for example the
    Binance web UI's `web_algo_` algo orders) is an operator/manual order that must
    never be counted as a system entry or pending reservation.
    """
    client_order_id = str(order.get("client_order_id") or "")
    system_owned = is_v2_client_order_id(client_order_id)
    return {
        **order,
        "ownership": "SYSTEM_V2_ORDER" if system_owned else "EXTERNAL_MANUAL_ORDER",
        "system_owned": system_owned,
    }


def _probe_exchange_truth() -> dict:
    """Run one reconcile and cache its outcome. Runs on the probe worker thread.

    This caches its own result before returning so that a probe whose waiter already
    timed out still publishes real exchange data for the next poll.
    """
    global _exchange_cache
    observed_at = datetime.now(UTC)
    try:
        gateway = configured_gateways()[0]
        snapshot = gateway.reconcile(
            live_run_id=f"runtime-truth:{observed_at.isoformat()}",
            include_account_summary=True,
        )
        if "open_positions" not in snapshot:
            raise ValueError("exchange snapshot omitted open_positions")
        account = snapshot.get("account")
        exchange_value = {
            "positions": snapshot["open_positions"],
            "open_orders": [_tag_order_ownership(order) for order in (snapshot.get("open_orders") or [])],
            "reconciliation_status": snapshot.get("reconciliation_status"),
            "notes": snapshot.get("notes") or [],
        }
        if account is not None:
            exchange_value["account"] = account
        result = _datum(
            value=exchange_value,
            source="BINANCE_USDT_M_TESTNET",
            observed_at=observed_at,
        )
    except Exception as exc:  # noqa: BLE001 - unavailable must be explicit
        stale = _cached_exchange_truth(allow_stale=True)
        if stale is not None and stale.get("value") is not None:
            result = dict(stale)
            result["status"] = "stale"
            result["freshness"] = "stale"
            result["error"] = str(exc)
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


def _ensure_exchange_probe() -> Future:
    """Return the in-flight probe, starting one only when none is running.

    Concurrent callers share a single reconcile. The previous code rejected them with
    "probe already in progress", which made 2 of the 3 exchange-backed endpoints report
    unavailable on every cold page load because the console polls them in one batch.
    """
    global _exchange_inflight
    with _exchange_probe_lock:
        inflight = _exchange_inflight
        if inflight is not None and not inflight.done():
            return inflight
        future = _exchange_truth_executor.submit(_probe_exchange_truth)
        _exchange_inflight = future
        return future


def _exchange_truth() -> dict:
    age = _cached_exchange_age_seconds()
    if age is not None and age < _EXCHANGE_MAX_SERVE_SECONDS:
        cached = _cached_exchange_truth(allow_stale=True)
        if cached is not None and cached.get("value") is not None:
            # Refresh ahead of expiry so the next poll is also a cache hit. The console
            # polls every 30s; blocking each poll on a 4-9s reconcile is what made the
            # account panel flicker to "unavailable" on jitter.
            if age >= _EXCHANGE_BACKGROUND_REFRESH_SECONDS:
                _ensure_exchange_probe()
            return cached
    fresh = _cached_exchange_truth(allow_stale=False)
    if fresh is not None:
        return fresh
    future = _ensure_exchange_probe()
    try:
        return future.result(timeout=_EXCHANGE_TRUTH_TIMEOUT_SECONDS)
    except FutureTimeoutError:
        # Deliberately do not cancel: the probe keeps running and caches its own
        # result, so the next poll is served from real exchange data. Cancelling here
        # is what previously made every probe restart from zero and time out forever.
        stale = _cached_exchange_truth(allow_stale=True)
        if stale is not None:
            return stale
        return _datum(
            value=None,
            source="BINANCE_USDT_M_TESTNET",
            observed_at=datetime.now(UTC),
            status="unavailable",
            error=f"exchange truth probe exceeded {_EXCHANGE_TRUTH_TIMEOUT_SECONDS:.0f}s",
        )


def _platform_symbol(value: object) -> str:
    symbol = str(value or "")
    return symbol[:-5] if symbol.endswith(":USDT") else symbol


def _position_truth(
    *,
    exchange: dict,
    local_open: list[V2ManagedPosition],
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
        (item.symbol, item.direction): abs(float(item.quantity)) for item in local_open if abs(float(item.quantity)) > 0
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
    blocked_symbols.update(str(item["symbol"]) for item in quantity_mismatches)
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


def _v2_repo(db: Session) -> AutomatedTradingRepository:
    return AutomatedTradingRepository(db)


def _v2_decision_payload(decision, cycle) -> dict:
    """Normalize the persisted V2 decision fact for the Runtime Truth UI."""
    payload = dict(decision.payload or {})
    stages = payload.get("stages")
    exchange_submitted = any(
        isinstance(stage, dict) and stage.get("stage") == "EXCHANGE_SUBMITTED" and stage.get("outcome") == "PASSED"
        for stage in (stages if isinstance(stages, list) else [])
    )
    return {
        "decision_id": decision.decision_id,
        "cycle_id": cycle.cycle_id,
        "symbol": cycle.symbol,
        "created_at": decision.created_at.isoformat(),
        "last_decision_at": decision.created_at.isoformat(),
        "strategy": payload.get("active_entry_strategy") or payload.get("strategy_id"),
        "entry_authority": payload.get("entry_authority"),
        "signal_generated": bool(payload.get("candidate_key")),
        "entry_gate_result": payload.get("terminal_stage") or cycle.decision_terminal or "UNKNOWN",
        "entry_submitted": exchange_submitted,
        "terminal_reason": decision.terminal_reason or payload.get("reason_code") or "UNKNOWN",
    }


def _v2_position_payload(position: V2ManagedPosition) -> dict:
    """Keep established Runtime Truth names while sourcing V2 position facts."""
    return {
        "position_record_id": position.position_id,
        "intent_id": position.intent_id,
        "order_record_id": position.order_record_id,
        "symbol": position.symbol,
        "position_side": position.direction,
        "direction": position.direction,
        "quantity": float(position.quantity),
        "entry_price": float(position.entry_price),
        "entry_fee": float(position.entry_fee),
        "management_status": position.state,
        "state": position.state,
        "execution_mode": position.execution_mode,
        "opened_at": position.projected_at.isoformat() if position.projected_at else None,
        "protected_at": position.protected_at.isoformat() if position.protected_at else None,
        "closed_at": position.closed_at.isoformat() if position.closed_at else None,
        "realized_pnl": float(position.realized_pnl) if position.realized_pnl is not None else None,
    }


def _v2_order_payload(order: V2ExchangeOrder, intent: V2ExecutionIntent) -> dict:
    system_owned = is_v2_client_order_id(str(order.client_order_id or ""))
    return {
        "exchange_order_record_id": order.order_record_id,
        "intent_id": order.intent_id,
        "client_order_id": order.client_order_id,
        "exchange_order_id": order.exchange_order_id,
        "ownership": "SYSTEM_V2_ORDER" if system_owned else "EXTERNAL_MANUAL_ORDER",
        "system_owned": system_owned,
        "symbol": intent.symbol,
        "side": intent.direction,
        "direction": intent.direction,
        "state": intent.state,
        "requested_quantity": float(order.quantity),
        "filled_quantity": float(order.filled_quantity) if order.filled_quantity is not None else None,
        "average_fill_price": float(order.average_fill_price) if order.average_fill_price is not None else None,
        "leverage": order.leverage,
        "submitted_at": order.submitted_at.isoformat() if order.submitted_at else None,
        "acknowledged_at": order.acknowledged_at.isoformat() if order.acknowledged_at else None,
        "fill_timestamp": order.fill_timestamp.isoformat() if order.fill_timestamp else None,
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "rejection_reason": order.rejection_reason,
        "execution_mode": intent.execution_mode,
    }


def _v2_protection_payload(protection: V2ProtectionRecord, position: V2ManagedPosition) -> dict:
    return {
        "protection_record_id": protection.protection_id,
        "position_id": protection.position_id,
        "symbol": position.symbol,
        "position_side": position.direction,
        "direction": position.direction,
        "status": protection.state,
        "state": protection.state,
        "stop_loss_price": float(protection.stop_loss_price),
        "take_profit_price": (
            float(protection.take_profit_price) if protection.take_profit_price is not None else None
        ),
        "stop_client_order_id": protection.stop_client_order_id,
        "tp_client_order_id": protection.tp_client_order_id,
        "stop_exchange_order_id": protection.stop_exchange_order_id,
        "tp_exchange_order_id": protection.tp_exchange_order_id,
        "created_at": protection.created_at.isoformat() if protection.created_at else None,
        "activated_at": protection.activated_at.isoformat() if protection.activated_at else None,
        "execution_mode": position.execution_mode,
    }


def _v2_fill_payload(fill: V2ExchangeFill, intent: V2ExecutionIntent) -> dict:
    return {
        "fill_receipt_id": fill.fill_id,
        "intent_id": fill.intent_id,
        "exchange_order_record_id": fill.exchange_order_record_id,
        "exchange_order_id": fill.exchange_order_id,
        "trade_id": fill.trade_id,
        "symbol": fill.symbol,
        "side": fill.side,
        "direction": intent.direction,
        "reduce_only": fill.reduce_only,
        "filled_quantity": float(fill.filled_quantity),
        "fill_price": float(fill.fill_price),
        "commission": float(fill.commission) if fill.commission is not None else None,
        "commission_asset": fill.commission_asset,
        "exchange_event_time": fill.exchange_event_time.isoformat(),
        "received_at": fill.received_at.isoformat(),
        "execution_mode": intent.execution_mode,
    }


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


_ENTRY_BLOCKING_REASONS = frozenset(
    {
        "POSITION_ALREADY_OPEN",
        "ENTRY_KILL_SWITCH_ACTIVE",
        "DAILY_TRADE_LIMIT_REACHED",
        "SYMBOL_COOLDOWN_ACTIVE",
        "RISK_LIMIT_EXCEEDED",
        "PRICE_DRIFT_EXCEEDED",
        "NET_EDGE_AFTER_COST_NEGATIVE",
        "NO_TRADE_COST_INEFFICIENT",
        "PRETRADE_DECISION_STALE",
        "RECONCILIATION_BLOCKED",
    }
)
_DECISION_PIPELINE_MAX_AGE = timedelta(minutes=30)


def _iso_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _decision_at(item: dict) -> datetime | None:
    value = item.get("at")
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _entry_blocking_reason(reason: str) -> bool:
    return reason in _ENTRY_BLOCKING_REASONS or reason.startswith(("RISK_", "POSITION_", "PRICE_DRIFT_"))


def build_no_trade_summary(
    *,
    observed_at: datetime,
    window_hours: int,
    scheduler: dict,
    exchange: dict,
    data: dict,
    reconciliation: dict,
    entry_runtime: dict,
    decisions: list[dict],
    entry_fills: list[dict],
    funnel: dict | None = None,
) -> dict:
    """Project existing Runtime Truth facts into one deterministic no-trade status."""
    window_start = observed_at - timedelta(hours=window_hours)
    normalized_decisions = sorted(
        ({**item, "at": at} for item in decisions if (at := _decision_at(item)) is not None and at >= window_start),
        key=lambda item: item["at"],
        reverse=True,
    )
    duplicate_decisions = [item for item in normalized_decisions if item.get("reason") == "DUPLICATE_DECISION"]
    effective_decisions = [item for item in normalized_decisions if item.get("reason") != "DUPLICATE_DECISION"]
    reason_counts: dict[str, int] = {}
    for item in effective_decisions:
        reason = str(item.get("reason") or "UNKNOWN")
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
    dominant_reason = next(iter(reason_counts), None)
    if reason_counts:
        dominant_reason = max(reason_counts, key=lambda reason: (reason_counts[reason], reason))

    entry_fill_times = [
        at
        for item in entry_fills
        if not bool(item.get("reduce_only")) and (at := _decision_at(item)) is not None and at >= window_start
    ]
    last_entry = max(entry_fill_times, default=None)
    hours_since_last_entry = (
        round((observed_at - last_entry).total_seconds() / 3600, 3) if last_entry is not None else None
    )

    heartbeat_at = scheduler.get("heartbeat_at")
    last_auto_cycle_at = scheduler.get("last_auto_cycle_at")
    exchange_status = str(exchange.get("status") or "unavailable")
    reconciliation_status = str(reconciliation.get("status") or "unavailable")
    trading_state = str(entry_runtime.get("trading_state") or "ENTRY_PAUSED")
    entry_authorized = bool(entry_runtime.get("entry_authorized"))
    latest_decision_at = normalized_decisions[0]["at"] if normalized_decisions else None

    if not bool(scheduler.get("running")):
        summary_code = "SCHEDULER_OFFLINE"
    elif exchange_status == "unavailable" or exchange.get("value") is None:
        summary_code = "EXCHANGE_UNAVAILABLE"
    elif not bool(data.get("fresh")) or not bool(data.get("exchange_info_ready")):
        summary_code = "MARKET_DATA_STALE"
    elif reconciliation_status != "healthy" or reconciliation.get("blocked_symbols"):
        summary_code = "RECONCILIATION_BLOCKED"
    elif trading_state == "ENTRY_PAUSED" or not entry_authorized:
        summary_code = "ENTRY_PAUSED"
    elif latest_decision_at is None or observed_at - latest_decision_at > _DECISION_PIPELINE_MAX_AGE:
        summary_code = "DECISION_PIPELINE_STALLED"
    elif dominant_reason is not None and _entry_blocking_reason(dominant_reason):
        summary_code = "ENTRY_BLOCKED"
    else:
        summary_code = "HEALTHY_WAITING_FOR_SIGNAL"

    return {
        "window_hours": window_hours,
        "window_start": window_start.isoformat(),
        "observed_at": observed_at.isoformat(),
        "last_entry_at": _iso_datetime(last_entry),
        "hours_since_last_entry": hours_since_last_entry,
        "runtime_status": "异常"
        if summary_code
        in {
            "SCHEDULER_OFFLINE",
            "EXCHANGE_UNAVAILABLE",
            "MARKET_DATA_STALE",
            "RECONCILIATION_BLOCKED",
            "DECISION_PIPELINE_STALLED",
        }
        else "正常",
        "scheduler": {
            "running": bool(scheduler.get("running")),
            "heartbeat_at": _iso_datetime(heartbeat_at) if isinstance(heartbeat_at, datetime) else heartbeat_at,
            "last_auto_cycle_at": (
                _iso_datetime(last_auto_cycle_at) if isinstance(last_auto_cycle_at, datetime) else last_auto_cycle_at
            ),
        },
        "exchange": {"status": exchange_status},
        "data": {
            "fresh": bool(data.get("fresh")),
            "exchange_info_ready": bool(data.get("exchange_info_ready")),
        },
        "reconciliation": {
            "status": reconciliation_status,
            "blocked_symbols": list(reconciliation.get("blocked_symbols") or []),
        },
        "entry_runtime": {
            "trading_state": trading_state,
            "entry_authority": entry_runtime.get("entry_authority"),
            "entry_authorized": entry_authorized,
            "reason": entry_runtime.get("reason") or entry_runtime.get("entry_authority_reason"),
        },
        "decisions": {
            "total": len(normalized_decisions),
            "effective": len(effective_decisions),
            "duplicate": len(duplicate_decisions),
            "latest_at": _iso_datetime(latest_decision_at),
            "reason_counts": reason_counts,
            "dominant_reason": dominant_reason,
        },
        "funnel": funnel or {},
        "summary_code": summary_code,
    }


@router.get("/snapshot")
def runtime_snapshot(db: Session = Depends(get_db_session)) -> dict:
    observed_at = datetime.now(UTC)
    v2_repo = _v2_repo(db)
    exchange = _exchange_truth()
    local_open = v2_repo.get_open_positions(V2ExecutionMode.BINANCE_TESTNET)
    mismatch, _blocked_symbols = _position_truth(
        exchange=exchange,
        local_open=local_open,
        observed_at=observed_at,
    )
    scheduler = load_external_scheduler_state(now=observed_at)
    paper_runs = PaperRunRepository(db).list_paper_runs()
    entry_runtime = {
        "entry_authority": scheduler.entry_authority or "NONE",
        "entry_authorized": bool(scheduler.entry_authorized),
        "entry_authority_reason": scheduler.entry_authority_reason or scheduler.reason,
        "production_authorization_state": scheduler.production_authorization_state or "PENDING",
        "active_entry_strategy": scheduler.active_entry_strategy,
        "promotion_eligible": bool(scheduler.promotion_eligible),
        "trading_state": scheduler.trading_state or "ENTRY_PAUSED",
    }
    return {
        "observed_at": observed_at.isoformat(),
        "exchange": exchange,
        "local_projection": _datum(
            value=[_v2_position_payload(item) for item in local_open],
            source="V2_MANAGED_POSITION_FACTS",
            observed_at=observed_at,
        ),
        "mismatch": mismatch,
        "scheduler": _datum(
            value={
                **scheduler.__dict__,
                **entry_runtime,
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
        "entry_runtime": _datum(
            value=entry_runtime,
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
            value=[
                _v2_protection_payload(protection, position)
                for protection, position in v2_repo.list_protection_records(
                    execution_mode=V2ExecutionMode.BINANCE_TESTNET,
                    limit=200,
                )
            ],
            source="V2_PROTECTION_FACTS",
            observed_at=observed_at,
        ),
    }


@router.get("/no-trade-summary")
def runtime_no_trade_summary(
    window_hours: int = Query(default=3, ge=1, le=24),
    db: Session = Depends(get_db_session),
) -> dict:
    observed_at = datetime.now(UTC)
    window_start = observed_at - timedelta(hours=window_hours)
    v2_repo = _v2_repo(db)
    scheduler = load_external_scheduler_state(now=observed_at)
    exchange = _exchange_truth()
    reconciliation = runtime_reconciliation(db)
    decision_rows = v2_repo.list_recent_decisions(since=window_start, limit=500)
    decisions = []
    for decision, cycle in decision_rows:
        payload = _v2_decision_payload(decision, cycle)
        decisions.append(
            {
                "at": decision.created_at,
                "reason": payload["terminal_reason"],
                "signal_generated": payload["signal_generated"],
                "entry_gate_result": payload["entry_gate_result"],
                "entry_submitted": payload["entry_submitted"],
            }
        )
    fills = [
        {"at": fill.received_at, "reduce_only": fill.reduce_only}
        for fill, _intent in v2_repo.list_exchange_fills(
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            limit=500,
        )
    ]
    summary = build_no_trade_summary(
        observed_at=observed_at,
        window_hours=window_hours,
        scheduler={
            "running": scheduler.running,
            "heartbeat_at": scheduler.heartbeat_at,
            "last_auto_cycle_at": scheduler.last_auto_cycle_at,
        },
        exchange=exchange,
        data={"fresh": scheduler.data_fresh, "exchange_info_ready": scheduler.exchange_info_ready},
        reconciliation={
            "status": reconciliation["status"],
            "blocked_symbols": reconciliation["entry_blocked_symbols"],
        },
        entry_runtime={
            "trading_state": scheduler.trading_state or "ENTRY_PAUSED",
            "entry_authority": scheduler.entry_authority or "NONE",
            "entry_authorized": bool(scheduler.entry_authorized),
            "reason": scheduler.entry_authority_reason or scheduler.reason,
        },
        decisions=decisions,
        entry_fills=fills,
    )
    effective = [item for item in decisions if item["reason"] != "DUPLICATE_DECISION"]
    orders = v2_repo.list_exchange_orders(execution_mode=V2ExecutionMode.BINANCE_TESTNET, limit=500)
    order_rows = [
        (order, intent)
        for order, intent in orders
        if (created_at := order.created_at) is not None
        and (created_at if created_at.tzinfo else created_at.replace(tzinfo=UTC)) >= window_start
    ]
    fill_rows = [
        (fill, intent)
        for fill, intent in v2_repo.list_exchange_fills(
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            limit=500,
        )
        if not fill.reduce_only
        and fill.received_at is not None
        and (fill.received_at if fill.received_at.tzinfo else fill.received_at.replace(tzinfo=UTC)) >= window_start
    ]
    protection_rows = [
        (protection, position)
        for protection, position in v2_repo.list_protection_records(
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            limit=500,
        )
        if (created_at := protection.created_at) is not None
        and (created_at if created_at.tzinfo else created_at.replace(tzinfo=UTC)) >= window_start
    ]
    intent_rows = [
        intent
        for intent in db.scalars(
            select(V2ExecutionIntent)
            .where(V2ExecutionIntent.execution_mode == V2ExecutionMode.BINANCE_TESTNET.value)
            .order_by(V2ExecutionIntent.created_at.desc())
            .limit(500)
        )
        if (created_at := intent.created_at) is not None
        and (created_at if created_at.tzinfo else created_at.replace(tzinfo=UTC)) >= window_start
    ]
    summary["funnel"] = {
        "decision_cycles": len(effective),
        "signal_generated": sum(bool(item.get("signal_generated")) for item in effective),
        "candidate_created": sum(
            bool(item.get("signal_generated")) and item.get("reason") != "NO_ENTRY_SIGNAL" for item in effective
        ),
        "r2_cost_rejected": sum(item["reason"] == "NO_TRADE_COST_INEFFICIENT" for item in effective),
        "position_rejected": sum(item["reason"].startswith("POSITION_") for item in effective),
        "price_drift_rejected": sum(item["reason"].startswith("PRICE_DRIFT_") for item in effective),
        "intent_created": len(intent_rows),
        "exchange_submitted": sum(order.submitted_at is not None for order, _intent in order_rows),
        "exchange_filled": len(fill_rows),
        "protection_confirmed": sum(
            protection.state in {"PROTECTION_ACTIVE", "PROTECTION_FILLED"} for protection, _position in protection_rows
        ),
    }
    return summary


@router.get("/decisions")
def runtime_decisions(
    symbol: str | None = None,
    since: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    cursor: str | None = None,
    db: Session = Depends(get_db_session),
) -> dict:
    del cursor
    items = _v2_repo(db).list_recent_decisions(
        symbol=symbol,
        since=since,
        limit=limit,
    )
    return {
        "items": [_v2_decision_payload(decision, cycle) for decision, cycle in items],
        "next_cursor": items[-1][0].decision_id if len(items) == limit else None,
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
    items = _v2_repo(db).list_exchange_orders(
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        symbol=symbol,
        limit=limit,
    )
    if since is not None:
        items = [item for item in items if item[0].created_at is not None and item[0].created_at >= since]
    return {
        "items": [_v2_order_payload(order, intent) for order, intent in items],
        "next_cursor": (items[-1][0].order_record_id if len(items) == limit else None),
    }


@router.get("/positions")
def runtime_positions(db: Session = Depends(get_db_session)) -> dict:
    v2_repo = _v2_repo(db)
    return {
        "exchange": _exchange_truth(),
        "local": _datum(
            value=[
                _v2_position_payload(item)
                for item in v2_repo.list_managed_positions(
                    execution_mode=V2ExecutionMode.BINANCE_TESTNET,
                    limit=200,
                )
            ],
            source="V2_MANAGED_POSITION_FACTS",
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
    v2_repo = _v2_repo(db)
    local_open = v2_repo.get_open_positions(V2ExecutionMode.BINANCE_TESTNET)
    mismatch, blocked_symbols = _position_truth(
        exchange=exchange,
        local_open=local_open,
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
    unknown_orders = v2_repo.list_exchange_unknown_intents(execution_mode=V2ExecutionMode.BINANCE_TESTNET)
    exchange_status = str(exchange.get("status") or "")
    exchange_has_snapshot = exchange.get("value") is not None
    # Stale cached truth is degraded for ops UI, but must not invent a full BTC/ETH
    # block when positions/orders were recently observed. Only hard-unavailable
    # (no usable value) forces the full entry block in this projection endpoint.
    if exchange_status == "unavailable" or (exchange_status != "available" and not exchange_has_snapshot):
        reconciliation_status = "unavailable"
        blocked_symbols = {"BTC/USDT", "ETH/USDT"}
    elif exchange_status == "stale":
        reconciliation_status = "degraded"
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
        "unresolved_exchange_order_ids": [order.intent_id for order in unknown_orders],
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
                v2_repo = _v2_repo(db)
                latest = v2_repo.list_recent_decisions(limit=20)
                orders = v2_repo.list_exchange_orders(
                    execution_mode=V2ExecutionMode.BINANCE_TESTNET,
                    limit=20,
                )
                protections = v2_repo.list_protection_records(
                    execution_mode=V2ExecutionMode.BINANCE_TESTNET,
                    limit=20,
                )
                fills = v2_repo.list_exchange_fills(
                    execution_mode=V2ExecutionMode.BINANCE_TESTNET,
                    limit=20,
                )
                llm_invocations = LlmInvocationRepository(db).list_invocations(limit=20)
                await websocket.send_json(
                    {
                        "event": "runtime_truth",
                        "observed_at": datetime.now(UTC).isoformat(),
                        "decisions": [_v2_decision_payload(decision, cycle) for decision, cycle in latest],
                        "exchange_orders": [_v2_order_payload(order, intent) for order, intent in orders],
                        "fill_receipts": [_v2_fill_payload(fill, intent) for fill, intent in fills],
                        "protections": [
                            _v2_protection_payload(protection, position) for protection, position in protections
                        ],
                        "llm_invocations": [item.model_dump(mode="json") for item in llm_invocations],
                    }
                )
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        return
