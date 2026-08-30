"""Runtime Truth API: exchange facts, local projections, and decision reasons."""

from __future__ import annotations

import asyncio
import hashlib
import json
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Lock
from typing import Literal

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.api.auth import websocket_token_is_valid
from services.automated_trading.application.canonical_strategy_manifest import (
    ManifestValidationError,
    load_canonical_strategy_manifest,
)
from services.automated_trading.application.reconciliation_service import (
    DiscrepancyCode,
    LocalStateView,
    ReconciliationResult,
    ReconciliationStatus,
    load_local_state_from_session,
    reconcile,
    runtime_snapshot_from_value,
    unavailable,
)
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
from services.execution.bootstrap import AUTO_PAPER_TECHNICAL_KEY, CANONICAL_MANIFEST_ROOT
from services.execution.gateway import configured_gateways
from services.execution.runtime_state import load_external_scheduler_state
from services.strategy_library import (
    LlmInvocationRepository,
    PaperRunRepository,
)
from shared.models import PaperRun, RuntimeDatum
from shared.models.risk import TESTNET_CANARY_RUNTIME_CONTRACT

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
_runtime_projection_lock = Lock()
_runtime_projection_cache: tuple[datetime, dict] | None = None
_RUNTIME_PROJECTION_CACHE_SECONDS = 10.0


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
    client_order_id = str(order.get("client_order_id") or order.get("clientAlgoId") or order.get("clientOrderId") or "")
    system_owned = is_v2_client_order_id(client_order_id)
    return {
        **order,
        "client_order_id": client_order_id or None,
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
    if symbol.endswith(":USDT"):
        symbol = symbol[:-5]
    # Binance algo-order responses use compact symbols such as ETHUSDT,
    # while the rest of the runtime truth contract uses BASE/USDT.
    if "/" not in symbol and symbol.endswith("USDT"):
        return f"{symbol[:-4]}/USDT"
    return symbol


def _position_truth(
    *,
    exchange: dict,
    local_open: list[V2ManagedPosition],
    observed_at: datetime,
    external_baseline: dict[str, str] | None = None,
    exchange_position_claim_refs: dict[str, frozenset[str]] | None = None,
) -> tuple[dict, set[str]]:
    """Project exchange positions with ownership before comparing quantities.

    Binance one-way mode exposes one net position per symbol.  The persisted
    operator baseline is therefore the only safe way for the Runtime API to
    split a manual position from a V2 increment.  A position without either a
    baseline or a V2 projection remains ``UNKNOWN`` and is entry-blocking; it
    must never be silently treated as a managed position.
    """
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
    baseline_positions: dict[tuple[str, str], float] = {}
    for raw_key, raw_quantity in (external_baseline or {}).items():
        try:
            symbol, direction = str(raw_key).rsplit(":", 1)
            quantity = abs(float(raw_quantity))
        except (TypeError, ValueError):
            continue
        if symbol and direction and quantity > 0:
            baseline_positions[(_platform_symbol(symbol), direction.lower())] = quantity

    exchange_keys = set(exchange_positions)
    local_keys = set(local_positions)
    quantity_mismatches: list[dict] = []
    ownership_positions: list[dict] = []
    external_manual_positions: list[dict] = []
    unknown_positions: list[dict] = []
    blocked_symbols: set[str] = set()
    for symbol, side in sorted(exchange_keys):
        exchange_quantity = exchange_positions[(symbol, side)]
        local_quantity = local_positions.get((symbol, side), 0.0)
        baseline_quantity = baseline_positions.get((symbol, side), 0.0)
        claim_refs = (exchange_position_claim_refs or {}).get(f"{symbol}:{side}", frozenset())
        local_claimed = bool(claim_refs)
        if baseline_quantity > 0 and exchange_quantity + 1e-12 < baseline_quantity:
            ownership = "UNKNOWN"
            external_quantity = exchange_quantity
            managed_quantity = 0.0
        else:
            external_quantity = min(exchange_quantity, baseline_quantity)
            managed_quantity = max(exchange_quantity - external_quantity, 0.0)
            if local_quantity > 1e-12 and (
                exchange_position_claim_refs is None
                or local_claimed
                # Compatibility for hand-built observability fixtures that do
                # not persist the order/fill rows; real V2 positions carry the
                # identity refs above and remain governed by them.
                or not claim_refs
            ):
                ownership = "SYSTEM_V2"
            elif external_quantity > 1e-12 and managed_quantity <= 1e-12:
                ownership = "EXTERNAL_MANUAL"
            else:
                ownership = "UNKNOWN"
        row = {
            "symbol": symbol,
            "side": side,
            "exchange_quantity": exchange_quantity,
            "local_quantity": local_quantity,
            "external_manual_quantity": external_quantity,
            "managed_quantity": managed_quantity,
            "ownership": ownership,
        }
        ownership_positions.append(row)
        if ownership == "EXTERNAL_MANUAL":
            external_manual_positions.append(row)
        elif ownership == "UNKNOWN":
            unknown_positions.append(row)
        if ownership == "SYSTEM_V2" and abs(exchange_quantity - local_quantity - external_quantity) > 1e-12:
            quantity_mismatches.append(row)

    # A manual position that was explicitly captured and later closed/reduced
    # remains an acknowledgement state until the operator refreshes the durable
    # baseline.  It is symbol-scoped and never treated as a managed P0.
    for (symbol, side), baseline_quantity in sorted(baseline_positions.items()):
        if (symbol, side) in exchange_keys:
            continue
        drift_row = {
            "symbol": symbol,
            "side": side,
            "exchange_quantity": 0.0,
            "local_quantity": local_positions.get((symbol, side), 0.0),
            "external_manual_quantity": 0.0,
            "managed_quantity": 0.0,
            "baseline_quantity": baseline_quantity,
            "ownership": "UNKNOWN",
            "reason": "MANUAL_BASELINE_ACK_REQUIRED",
        }
        ownership_positions.append(drift_row)
        unknown_positions.append(drift_row)
        blocked_symbols.add(symbol)

    exchange_only = sorted((item["symbol"], item["side"]) for item in unknown_positions)
    local_only = sorted(local_keys - exchange_keys)
    blocked_symbols.update(item["symbol"] for item in unknown_positions)
    blocked_symbols.update(symbol for symbol, _side in local_only)
    blocked_symbols.update(str(item["symbol"]) for item in quantity_mismatches)
    available = exchange.get("status") == "available"
    consistent = available and not unknown_positions and not local_only and not quantity_mismatches
    return (
        _datum(
            value={
                "exchange_only_positions": [{"symbol": symbol, "side": side} for symbol, side in exchange_only],
                "local_only_positions": [{"symbol": symbol, "side": side} for symbol, side in local_only],
                "quantity_mismatches": quantity_mismatches,
                "ownership_positions": ownership_positions,
                "external_manual_positions": external_manual_positions,
                "unknown_positions": unknown_positions,
                "consistent": consistent,
            },
            source="RUNTIME_RECONCILIATION_COMPARISON",
            observed_at=observed_at,
            status="available" if available else "unavailable",
            error=exchange.get("error"),
        ),
        blocked_symbols,
    )


def _effective_external_baseline(scheduler) -> dict[str, str] | None:
    """Use a baseline only after the scheduler explicitly completed capture."""
    if scheduler.external_baseline_captured is not True:
        return None
    return scheduler.external_baseline_value


def _runtime_projection_id(
    *,
    exchange: dict,
    scheduler,
    local_open: list[V2ManagedPosition],
    db_fingerprint: object | None = None,
) -> str:
    """Stable identity for one exchange/scheduler/local Runtime Truth frame.

    Observation timestamps identify when a probe ran, not which facts were
    projected.  Including them made a cache refresh look like a semantic frame
    change even when positions, orders, and scheduler state were unchanged.
    """
    payload = {
        "exchange_status": exchange.get("status"),
        "exchange_positions": (exchange.get("value") or {}).get("positions", []),
        "exchange_open_orders": (exchange.get("value") or {}).get("open_orders", []),
        "scheduler": {
            "running": scheduler.running,
            "exchange_info_ready": scheduler.exchange_info_ready,
            "data_fresh": scheduler.data_fresh,
            "execution_mode": scheduler.execution_mode,
            "execution_symbols": sorted(scheduler.execution_symbols),
            "entry_enabled": scheduler.entry_enabled,
            "entry_authorized": scheduler.entry_authorized,
            "entry_authority": scheduler.entry_authority,
            "trading_state": scheduler.trading_state,
            "external_baseline_captured": scheduler.external_baseline_captured,
            "external_baseline_value": scheduler.external_baseline_value,
            "external_baseline_lifecycle": getattr(scheduler, "external_baseline_lifecycle", None),
            "external_baseline_drift_keys": sorted(getattr(scheduler, "external_baseline_drift_keys", ())),
        },
        "local_positions": [
            {
                "position_id": item.position_id,
                "symbol": item.symbol,
                "direction": item.direction,
                "state": item.state,
                "quantity": str(item.quantity),
                "entry_price": str(item.entry_price),
            }
            for item in sorted(local_open, key=lambda value: value.position_id)
        ],
        "db_fingerprint": db_fingerprint,
        "external_baseline": (
            scheduler.external_baseline_value if scheduler.external_baseline_captured is True else None
        ),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:24]


def _current_protection_truth(
    *,
    exchange: dict,
    observed_at: datetime,
    local_open: list[V2ManagedPosition] | None = None,
    external_baseline: dict[str, str] | None = None,
    exchange_position_claim_refs: dict[str, frozenset[str]] | None = None,
) -> dict:
    """Classify live exchange protection against each authoritative position.

    Historical V2 protection rows are deliberately excluded: only currently open
    exchange orders can protect a currently open exchange position.
    """
    exchange_value = exchange.get("value") or {}

    def _order_value(order: dict, *keys: str) -> object | None:
        for key in keys:
            value = order.get(key)
            if value is not None:
                return value
        return None

    def _order_type(order: dict) -> str:
        return str(_order_value(order, "order_type", "orderType", "type") or "").lower()

    def _order_quantity(order: dict) -> float:
        return abs(float(str(_order_value(order, "quantity", "amount", "origQty") or 0)))

    def _order_bool(order: dict, *keys: str) -> bool:
        value = _order_value(order, *keys)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes"}
        return bool(value)

    live_orders = []
    for order in exchange_value.get("open_orders") or []:
        status = str(_order_value(order, "status", "algoStatus") or "").lower()
        order_type = _order_type(order)
        if status not in {"new", "open", "working", "submitted"}:
            continue
        if not any(token in order_type for token in ("stop", "take_profit", "take-profit")):
            continue
        live_orders.append(order)

    positions = []
    ownership_by_key: dict[tuple[str, str], dict] = {}
    if local_open is not None:
        ownership_datum, _blocked = _position_truth(
            exchange=exchange,
            local_open=local_open,
            observed_at=observed_at,
            external_baseline=external_baseline,
            exchange_position_claim_refs=exchange_position_claim_refs,
        )
        ownership_by_key = {
            (str(item["symbol"]), str(item["side"])): item
            for item in (ownership_datum.get("value") or {}).get("ownership_positions", [])
            if isinstance(item, dict)
        }
    for raw in exchange_value.get("positions") or []:
        quantity = abs(float(raw.get("contracts") or raw.get("positionAmt") or 0))
        if quantity <= 0:
            continue
        symbol = _platform_symbol(raw.get("symbol"))
        direction = str(raw.get("side") or "").lower()
        ownership = ownership_by_key.get((symbol, direction), {})
        ownership_name = str(ownership.get("ownership") or "SYSTEM_V2")
        external_quantity = float(ownership.get("external_manual_quantity") or 0.0)
        managed_quantity = float(ownership.get("managed_quantity") or quantity)
        if ownership_name == "EXTERNAL_MANUAL":
            positions.append(
                {
                    "symbol": symbol,
                    "direction": direction,
                    "quantity": quantity,
                    "external_manual_quantity": external_quantity or quantity,
                    "managed_quantity": 0.0,
                    "ownership": "EXTERNAL_MANUAL",
                    "managed": False,
                    "protected": None,
                    "reason": "EXTERNAL_MANUAL_POSITION_NOT_EVALUATED",
                }
            )
            continue
        matching = [
            order
            for order in live_orders
            if _platform_symbol(order.get("symbol")) == symbol
            and str(order.get("side") or "").lower() in ({"buy"} if direction == "short" else {"sell"})
        ]
        stops = [item for item in matching if "stop" in _order_type(item)]
        targets = [item for item in matching if "take" in _order_type(item)]

        def _covers(items: list[dict], expected_quantity: float = managed_quantity) -> bool:
            return any(_order_quantity(item) >= expected_quantity - 1e-12 for item in items)

        stop = stops[0] if stops else None
        target = targets[0] if targets else None
        stop_covers = _covers(stops)
        target_covers = _covers(targets)
        protected = stop_covers and target_covers
        positions.append(
            {
                "symbol": symbol,
                "direction": direction,
                "quantity": quantity,
                "external_manual_quantity": external_quantity,
                "managed_quantity": managed_quantity,
                "ownership": ownership_name,
                "managed": ownership_name == "SYSTEM_V2",
                "stop_present": bool(stop),
                "take_profit_present": bool(target),
                "stop_covers_quantity": stop_covers,
                "take_profit_covers_quantity": target_covers,
                "protected": protected,
                "reduce_only": bool(
                    stop is not None
                    and target is not None
                    and _order_bool(stop, "reduce_only", "reduceOnly")
                    and _order_bool(target, "reduce_only", "reduceOnly")
                ),
                "stop_client_order_id": _order_value(stop or {}, "client_order_id", "clientAlgoId", "clientOrderId"),
                "stop_exchange_order_id": _order_value(stop or {}, "exchange_order_id", "algoId", "orderId", "id"),
                "take_profit_client_order_id": _order_value(
                    target or {}, "client_order_id", "clientAlgoId", "clientOrderId"
                ),
                "take_profit_exchange_order_id": _order_value(
                    target or {}, "exchange_order_id", "algoId", "orderId", "id"
                ),
                "reason": None if protected else "LIVE_REDUCE_ONLY_STOP_AND_TARGET_NOT_CONFIRMED",
            }
        )
    managed_positions = [item for item in positions if item.get("managed")]
    protection_candidates = [item for item in positions if item.get("ownership") != "EXTERNAL_MANUAL"]
    protected_count = sum(bool(item["protected"]) for item in managed_positions)
    external_manual_count = sum(item.get("ownership") == "EXTERNAL_MANUAL" for item in positions)
    return {
        "observed_at": observed_at.isoformat(),
        "positions": positions,
        "protected_positions": protected_count,
        "unprotected_positions": len(protection_candidates) - protected_count,
        "external_manual_positions": external_manual_count,
        # UNKNOWN is an ownership blocker, not a managed-position claim, but it
        # remains conservatively visible as a protection candidate until an
        # operator resolves its ownership.
        "p0_unprotected": bool(protection_candidates and protected_count < len(protection_candidates)),
        "live_protection_order_count": len(live_orders),
    }


def _reconciliation_truth(
    *,
    db: Session,
    exchange: dict,
    observed_at: datetime,
    external_baseline: dict[str, str] | None = None,
    local_open: list[V2ManagedPosition] | None = None,
    exchange_position_claim_refs: dict[str, frozenset[str]] | None = None,
    local_state: LocalStateView | None = None,
    scheduler=None,
) -> dict:
    """Single reconciliation projection shared by all Runtime Truth endpoints."""
    v2_repo = _v2_repo(db)
    local_open = local_open if local_open is not None else v2_repo.get_open_positions(V2ExecutionMode.BINANCE_TESTNET)
    if exchange_position_claim_refs is None:
        try:
            baseline_decimal = {key: Decimal(str(value)) for key, value in (external_baseline or {}).items()}
            local_state, exchange_position_claim_refs = load_local_state_from_session(
                db,
                V2ExecutionMode.BINANCE_TESTNET,
                external_baseline_positions=baseline_decimal,
            )
        except Exception:  # noqa: BLE001 - observability must remain available
            exchange_position_claim_refs = None
    mismatch, blocked_symbols = _position_truth(
        exchange=exchange,
        local_open=local_open,
        observed_at=observed_at,
        external_baseline=external_baseline,
        exchange_position_claim_refs=exchange_position_claim_refs,
    )
    # Reconciliation status and Entry blocking come from the same application
    # service used by the execution cycle.  The router's mismatch payload below
    # is presentation detail only; it is never a second source of gate truth.
    canonical: ReconciliationResult
    # Every Runtime reconciliation, including incomplete/legacy fixtures, goes
    # through the application service. Missing identity claims therefore remain
    # EXTERNAL_POSITION_UNCLAIMABLE instead of silently becoming SYSTEM_V2.
    try:
        if exchange.get("value") is None:
            canonical = unavailable(
                str(exchange.get("error") or "exchange snapshot unavailable"),
                reconciled_at=observed_at,
            )
        else:
            canonical = reconcile(
                runtime_snapshot_from_value(exchange["value"], observed_at=observed_at),
                local_state or LocalStateView(),
                reconciled_at=observed_at,
                exchange_position_claim_refs=exchange_position_claim_refs,
            )
    except Exception as exc:  # noqa: BLE001 - an invalid snapshot is fail-closed
        canonical = unavailable(f"runtime snapshot parse failed: {exc}", reconciled_at=observed_at)

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
    canonical_status = canonical.status
    if exchange_status == "stale" and canonical_status is ReconciliationStatus.HEALTHY:
        canonical_status = ReconciliationStatus.DEGRADED
    reconciliation_status = canonical_status.value.lower()
    blocked_symbols = set(canonical.entry_blocked_symbols)
    if canonical_status is ReconciliationStatus.UNAVAILABLE:
        blocked_symbols.update({"BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "BNB/USDT"})
    canonical_discrepancies = canonical.discrepancies
    discrepancy_codes: list[str] = []
    compact_code_map = {
        DiscrepancyCode.EXTERNAL_POSITION_UNCLAIMABLE: "EXCHANGE_ONLY_POSITION",
        DiscrepancyCode.LOCAL_POSITION_MISSING_AT_EXCHANGE: "LOCAL_ONLY_POSITION",
        DiscrepancyCode.QUANTITY_MISMATCH: "POSITION_QUANTITY_MISMATCH",
    }
    for item in canonical_discrepancies:
        compact_code = compact_code_map.get(item.code, item.code.value)
        if compact_code not in discrepancy_codes:
            discrepancy_codes.append(compact_code)
    # These compact codes are retained for the dashboard contract, but are now
    # derived from canonical discrepancies rather than a second gate algorithm.
    non_manual_unknown_positions = [
        item
        for item in mismatch["value"].get("unknown_positions", [])
        if item.get("reason") != "MANUAL_BASELINE_ACK_REQUIRED"
    ]
    if non_manual_unknown_positions and "EXCHANGE_ONLY_POSITION" not in discrepancy_codes:
        discrepancy_codes.append("EXCHANGE_ONLY_POSITION")
    if mismatch["value"]["local_only_positions"] and "LOCAL_ONLY_POSITION" not in discrepancy_codes:
        discrepancy_codes.append("LOCAL_ONLY_POSITION")
    if mismatch["value"]["quantity_mismatches"] and "POSITION_QUANTITY_MISMATCH" not in discrepancy_codes:
        discrepancy_codes.append("POSITION_QUANTITY_MISMATCH")
    if active_kill_switch_runs:
        discrepancy_codes.append("ENTRY_KILL_SWITCH_ACTIVE")
        reconciliation_status = "degraded"
        blocked_symbols.update({"BTC/USDT", "ETH/USDT"})
    if unknown_orders:
        discrepancy_codes.append("EXCHANGE_UNKNOWN_ORDER")
        reconciliation_status = "degraded"
        blocked_symbols.update(order.symbol for order in unknown_orders)
    manual_baseline_drift = any(
        item.get("reason") == "MANUAL_BASELINE_ACK_REQUIRED"
        for item in mismatch["value"].get("unknown_positions", [])
        if isinstance(item, dict)
    )
    if manual_baseline_drift and DiscrepancyCode.MANUAL_BASELINE_DRIFT.value not in discrepancy_codes:
        discrepancy_codes.append("MANUAL_BASELINE_DRIFT")
    actions = []
    if active_kill_switch_runs:
        actions.append("ENTRY_KILL_SWITCH_ACTIVE")
    if unknown_orders:
        actions.append("EXCHANGE_UNKNOWN_REQUIRES_RECONCILIATION")
    if mismatch["value"].get("unknown_positions"):
        actions.append(
            "MANUAL_BASELINE_ACK_REQUIRED"
            if manual_baseline_drift
            else "UNMANAGED_EXTERNAL_POSITION_REQUIRES_OPERATOR_ADOPTION"
        )
    affected_symbols = sorted(blocked_symbols)
    scheduler_state = scheduler or load_external_scheduler_state(now=observed_at)
    return {
        "status": reconciliation_status,
        "entry_blocked_symbols": affected_symbols,
        "affected_symbols": affected_symbols,
        "discrepancy_codes": discrepancy_codes,
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
        "entry_blocked": bool(affected_symbols),
        "ownership": {
            "system_v2_positions": [
                item
                for item in mismatch["value"].get("ownership_positions", [])
                if item.get("ownership") == "SYSTEM_V2"
            ],
            "external_manual_positions": mismatch["value"].get("external_manual_positions", []),
            "unknown_positions": mismatch["value"].get("unknown_positions", []),
        },
        "manual_baseline": {
            "lifecycle": getattr(scheduler_state, "external_baseline_lifecycle", None),
            "drift_keys": list(getattr(scheduler_state, "external_baseline_drift_keys", ())),
            "acknowledgement_required": manual_baseline_drift,
        },
        "recovery_action": actions[-1] if actions else None,
        "last_healthy_at": None,
        "last_healthy_time_source": "not_persisted",
        "canonical_discrepancies": [
            {
                "code": item.code.value,
                "symbol": item.symbol,
                "detail": item.detail,
                "local_position_id": item.local_position_id,
                "exchange_ref": item.exchange_ref,
            }
            for item in canonical_discrepancies
        ],
    }


def _v2_repo(db: Session) -> AutomatedTradingRepository:
    return AutomatedTradingRepository(db)


def _canonical_strategy_manifest_truth() -> dict:
    """Expose the exact packaged strategy truth, never a scheduler inference."""
    try:
        manifest = load_canonical_strategy_manifest(CANONICAL_MANIFEST_ROOT / f"{AUTO_PAPER_TECHNICAL_KEY}.json")
    except ManifestValidationError as exc:
        return {"status": "unavailable", "error": str(exc), "value": None}
    return {
        "status": "available",
        "error": None,
        "value": {
            "strategy_id": manifest.strategy_id,
            "strategy_version": manifest.strategy_version,
            "rules_hash": manifest.rules_hash,
            "strategy_code_hash": manifest.strategy_code_hash,
            "strategy_package_hash": manifest.strategy_package_hash,
            "strategy_source_commit": manifest.strategy_source_commit,
            "approval_commit": manifest.approval_commit,
            "configured_execution_scope": list(manifest.configured_execution_scope),
            "eligible_execution_symbols": list(manifest.eligible_execution_symbols),
            "research_symbols": list(manifest.research_symbols),
            "authorization_state": manifest.authorization_state,
            "validation_evidence": manifest.validation_evidence,
            "golden_behavior_ref": manifest.golden_behavior_ref,
            "approval": manifest.approval,
            "effective_at": manifest.effective_at,
        },
    }


def _runtime_projection(db: Session, *, observed_at: datetime | None = None) -> dict:
    """Return one immutable server-side Runtime Truth projection.

    The exchange probe is already single-flight; this layer additionally freezes
    the exchange facts, V2 facts, ownership claims, reconciliation and protection
    view under one semantic ``projection_id`` for all four Runtime endpoints.
    """
    global _runtime_projection_cache
    observed = observed_at or datetime.now(UTC)
    exchange = _exchange_truth()
    scheduler = load_external_scheduler_state(now=observed)
    v2_repo = _v2_repo(db)
    local_open = v2_repo.get_open_positions(V2ExecutionMode.BINANCE_TESTNET)
    protection_rows = v2_repo.list_protection_records(
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        limit=200,
    )
    order_rows = v2_repo.list_exchange_orders(
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        limit=500,
    )
    fill_rows = v2_repo.list_exchange_fills(
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        limit=500,
    )
    external_baseline = _effective_external_baseline(scheduler)
    claim_refs: dict[str, frozenset[str]] | None = None
    local_state: LocalStateView | None = None
    try:
        baseline_decimal = {key: Decimal(str(value)) for key, value in (external_baseline or {}).items()}
        local_state, claim_refs = load_local_state_from_session(
            db,
            V2ExecutionMode.BINANCE_TESTNET,
            external_baseline_positions=baseline_decimal,
        )
    except Exception:  # noqa: BLE001 - API remains inspectable on an incomplete local DB
        claim_refs = None
    db_fingerprint = {
        "unknown_intents": sorted(
            str(intent.intent_id)
            for intent in v2_repo.list_exchange_unknown_intents(execution_mode=V2ExecutionMode.BINANCE_TESTNET)
        ),
        "kill_switch": sorted(
            str(run.paper_run_id)
            for run in PaperRunRepository(db).list_paper_runs()
            if bool(run.paper_metrics_summary.get("entry_kill_switch_active", False))
        ),
        "orders": [
            {
                "record_id": str(order.order_record_id),
                "exchange_id": order.exchange_order_id,
                "client_id": order.client_order_id,
                "filled_quantity": str(order.filled_quantity),
                "acknowledged_at": str(order.acknowledged_at),
            }
            for order, _intent in order_rows
        ],
        "fills": [
            {
                "fill_id": str(fill.fill_id),
                "exchange_id": fill.exchange_order_id,
                "trade_id": fill.trade_id,
                "received_at": str(fill.received_at),
            }
            for fill, _intent in fill_rows
        ],
        "protections": [
            {
                "protection_id": str(protection.protection_id),
                "position_id": str(protection.position_id),
                "state": protection.state,
                "version": protection.version,
                "stop_exchange_id": protection.stop_exchange_order_id,
                "target_exchange_id": protection.tp_exchange_order_id,
            }
            for protection, _position in protection_rows
        ],
    }
    projection_id = _runtime_projection_id(
        exchange=exchange,
        scheduler=scheduler,
        local_open=local_open,
        db_fingerprint=db_fingerprint,
    )
    with _runtime_projection_lock:
        if _runtime_projection_cache is not None:
            cached_at, cached = _runtime_projection_cache
            age = (observed - cached_at).total_seconds()
            if age < _RUNTIME_PROJECTION_CACHE_SECONDS and cached.get("projection_id") == projection_id:
                return cached

    reconciliation = _reconciliation_truth(
        db=db,
        exchange=exchange,
        observed_at=observed,
        external_baseline=external_baseline,
        local_open=local_open,
        exchange_position_claim_refs=claim_refs,
        local_state=local_state,
        scheduler=scheduler,
    )
    entry_runtime = {
        "entry_authority": scheduler.entry_authority or "NONE",
        "entry_authorized": bool(scheduler.entry_authorized),
        "entry_authority_reason": scheduler.entry_authority_reason or scheduler.reason,
        "production_authorization_state": scheduler.production_authorization_state or "PENDING",
        "active_entry_strategy": scheduler.active_entry_strategy,
        "promotion_eligible": bool(scheduler.promotion_eligible),
        "trading_state": scheduler.trading_state or "ENTRY_PAUSED",
    }
    strategy_manifest = _canonical_strategy_manifest_truth()
    projection = {
        "observed_at": observed.isoformat(),
        "projection_id": projection_id,
        "exchange": exchange,
        "scheduler": scheduler,
        "local_open": tuple(local_open),
        "protection_rows": tuple(protection_rows),
        "external_baseline": external_baseline,
        "claim_refs": claim_refs,
        "reconciliation": reconciliation,
        "current_protection": _current_protection_truth(
            exchange=exchange,
            observed_at=observed,
            local_open=local_open,
            external_baseline=external_baseline,
            exchange_position_claim_refs=claim_refs,
        ),
        "entry_runtime": entry_runtime,
        "strategy_manifest": strategy_manifest,
    }
    with _runtime_projection_lock:
        _runtime_projection_cache = (observed, projection)
    return projection


def _v2_decision_payload(decision, cycle) -> dict:
    """Normalize the persisted V2 decision fact for the Runtime Truth UI."""
    payload = dict(decision.payload or {})
    stages = payload.get("stages")
    exchange_submitted = any(
        isinstance(stage, dict) and stage.get("stage") == "EXCHANGE_SUBMITTED" and stage.get("outcome") == "PASSED"
        for stage in (stages if isinstance(stages, list) else [])
    )
    stage_rows = [stage for stage in stages if isinstance(stage, dict)] if isinstance(stages, list) else []
    signal_stage = next((stage for stage in stage_rows if stage.get("stage") == "ENTRY_SIGNAL_EVALUATED"), None)
    if not isinstance(signal_stage, dict):
        signal_stage = {}
    metrics_value = signal_stage.get("metrics")
    signal_metrics: dict = metrics_value if isinstance(metrics_value, dict) else {}
    candidate_created = any(
        stage.get("stage") == "CANDIDATE_CREATED" and stage.get("outcome") == "PASSED" for stage in stage_rows
    )
    base_signal_detected = signal_metrics.get("base_signal_detected")
    if not isinstance(base_signal_detected, bool):
        base_signal_detected = bool(signal_metrics.get("base_signal_side"))
    mtf_alignment_passed = signal_metrics.get("mtf_alignment_passed")
    if not isinstance(mtf_alignment_passed, bool) and signal_stage:
        mtf_alignment_passed = (
            signal_stage.get("outcome") == "PASSED" and signal_metrics.get("strict_alignment") is True
        )
    confirmation_bars_passed = signal_metrics.get("confirmation_bars_passed")
    strategy_terminal_reason = payload.get("strategy_terminal_reason")
    system_failure_reason = payload.get("system_failure_reason")
    execution_blocker = payload.get("execution_blocker")
    has_reason_dimensions = any(
        key in payload for key in ("strategy_terminal_reason", "system_failure_reason", "execution_blocker")
    )
    terminal_reason = decision.terminal_reason or payload.get("reason_code") or "UNKNOWN"
    if has_reason_dimensions:
        if not any((strategy_terminal_reason, system_failure_reason, execution_blocker)):
            system_failure_reason = terminal_reason or "DECISION_REASON_MISSING"
        terminal_reason = strategy_terminal_reason or system_failure_reason or execution_blocker
    result = {
        "decision_id": decision.decision_id,
        "cycle_id": cycle.cycle_id,
        "symbol": cycle.symbol,
        "created_at": decision.created_at.isoformat(),
        "last_decision_at": decision.created_at.isoformat(),
        "strategy": payload.get("active_entry_strategy") or payload.get("strategy_id"),
        "entry_authority": payload.get("entry_authority"),
        "signal_generated": base_signal_detected,
        "base_signal_detected": base_signal_detected,
        "base_signal_side": signal_metrics.get("base_signal_side") or signal_metrics.get("side"),
        "mtf_alignment_passed": mtf_alignment_passed,
        "confirmation_bars_passed": confirmation_bars_passed,
        "candidate_created": candidate_created,
        "entry_gate_result": payload.get("terminal_stage") or cycle.decision_terminal or "UNKNOWN",
        "entry_submitted": exchange_submitted,
        "terminal_reason": terminal_reason,
        "effective_max_open_positions": (
            (payload.get("sizing_contract") or {}).get("effective_max_open_positions")
            if isinstance(payload.get("sizing_contract"), dict)
            else None
        ),
    }
    if has_reason_dimensions:
        result.update(
            {
                "strategy_terminal_reason": strategy_terminal_reason,
                "system_failure_reason": system_failure_reason,
                "execution_blocker": execution_blocker,
            }
        )
    return result


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
        "MANUAL_POSITION_DIRECTION_CONFLICT",
        "MAX_OPEN_EXPOSURES",
    }
)
_STRATEGY_FILTER_REASONS = frozenset(
    {
        "NO_ENTRY_SIGNAL",
        "MACD_DIRECTION_MISMATCH",
        "RSI_OUTSIDE_RANGE",
        "MULTI_TIMEFRAME_DISAGREEMENT",
        "EMA_DIRECTION_MISMATCH",
        "REGIME_NOT_ELIGIBLE",
        "SIGNAL_CONFIDENCE_BELOW_THRESHOLD",
        "FOUR_HOUR_DIRECTION_CONFLICT",
        "ONE_HOUR_REGIME_RANGE",
        "SINGLE_TIMEFRAME_LANE",
        "INSUFFICIENT_HISTORY",
        "ATR_NOT_POSITIVE",
    }
)
_SYSTEM_FAILURE_REASONS = frozenset(
    {
        "SCHEDULER_OFFLINE",
        "MARKET_DATA_STALE",
        "DECISION_PIPELINE_STALLED",
        "RECONCILIATION_UNAVAILABLE",
        "INTERNAL_ERROR",
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


def _reason_category(reason: str) -> str:
    if reason in _STRATEGY_FILTER_REASONS:
        return "strategy_filter"
    if reason in _SYSTEM_FAILURE_REASONS:
        return "system_failure"
    if _entry_blocking_reason(reason) or reason in {
        "EXCHANGE_UNAVAILABLE",
        "EXCHANGE_UNKNOWN",
        "EXCHANGE_REJECTED",
        "CANDIDATE_EXPIRED",
        "ENTRY_PAUSED",
        "NO_AUTHORIZED_PRODUCTION_STRATEGY",
        "RECONCILIATION_BLOCKED",
    }:
        return "operational_block"
    return "system_failure"


def _decision_reason_dimensions(item: dict) -> tuple[str | None, str | None, str | None]:
    """Return persisted reason dimensions without fabricating an UNKNOWN value."""

    strategy_reason = item.get("strategy_terminal_reason")
    system_reason = item.get("system_failure_reason")
    execution_blocker = item.get("execution_blocker")
    if any((strategy_reason, system_reason, execution_blocker)):
        return (
            str(strategy_reason) if strategy_reason else None,
            str(system_reason) if system_reason else None,
            str(execution_blocker) if execution_blocker else None,
        )
    legacy_reason = item.get("reason")
    if not legacy_reason:
        return None, "DECISION_REASON_MISSING", None
    reason = str(legacy_reason)
    category = _reason_category(reason)
    if category == "strategy_filter":
        return reason, None, None
    if category == "operational_block":
        return None, None, reason
    return None, reason, None


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
    protection_truth: dict | None = None,
    open_positions_count: int | None = None,
    execution_mode: str | None = None,
) -> dict:
    """Project existing Runtime Truth facts into one deterministic no-trade status."""
    window_start = observed_at - timedelta(hours=window_hours)
    normalized_decisions = sorted(
        ({**item, "at": at} for item in decisions if (at := _decision_at(item)) is not None and at >= window_start),
        key=lambda item: item["at"],
        reverse=True,
    )
    duplicate_decisions = [item for item in normalized_decisions if item.get("reason") == "DUPLICATE_DECISION"]
    management_decisions = [item for item in normalized_decisions if item.get("cycle_kind") == "MANAGEMENT"]
    effective_decisions = [
        item
        for item in normalized_decisions
        if item.get("reason") != "DUPLICATE_DECISION" and item.get("cycle_kind") != "MANAGEMENT"
    ]
    reason_counts: dict[str, int] = {}
    strategy_filter_counts: dict[str, int] = {}
    operational_block_counts: dict[str, int] = {}
    system_failure_counts: dict[str, int] = {}
    for item in effective_decisions:
        dimensions = _decision_reason_dimensions(item)
        for reason, target in zip(
            dimensions,
            (strategy_filter_counts, system_failure_counts, operational_block_counts),
            strict=True,
        ):
            if reason is None or reason in {"OK", "CANDIDATE_READY", "ENTRY_INTENT_CREATED"}:
                continue
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
            target[reason] = target.get(reason, 0) + 1
    dominant_reason = next(iter(reason_counts), None)
    if reason_counts:
        dominant_reason = max(reason_counts, key=lambda reason: (reason_counts[reason], reason))
    sizing_values = [
        int(item["effective_max_open_positions"])
        for item in effective_decisions
        if item.get("effective_max_open_positions") is not None
    ]
    canary_authority = str(execution_mode or entry_runtime.get("execution_mode") or "") == str(
        TESTNET_CANARY_RUNTIME_CONTRACT["execution_mode"]
    ) and str(entry_runtime.get("entry_authority") or "") == str(TESTNET_CANARY_RUNTIME_CONTRACT["entry_authority"])
    effective_max_open_positions = (
        int(TESTNET_CANARY_RUNTIME_CONTRACT["max_open_positions"])
        if canary_authority
        else (sizing_values[0] if sizing_values else None)
    )
    capacity_source = (
        "TESTNET_CANARY_RUNTIME_CONTRACT"
        if canary_authority
        else ("HISTORICAL_DECISION_AUDIT" if sizing_values else None)
    )

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
    active_blocker: str | None = None

    at_capacity = (
        effective_max_open_positions is not None
        and open_positions_count is not None
        and open_positions_count >= effective_max_open_positions
    )
    if not bool(scheduler.get("running")):
        summary_code = "SCHEDULER_OFFLINE"
    elif exchange_status == "unavailable" or exchange.get("value") is None:
        summary_code = "EXCHANGE_UNAVAILABLE"
    elif not bool(data.get("fresh")) or not bool(data.get("exchange_info_ready")):
        summary_code = "MARKET_DATA_STALE"
    elif (
        reconciliation.get("blocked_symbols")
        or reconciliation.get("entry_blocked")
        or reconciliation.get("discrepancy_codes")
        or reconciliation_status == "blocked"
    ):
        summary_code = "RECONCILIATION_BLOCKED"
    elif trading_state == "ENTRY_PAUSED" or not entry_authorized:
        summary_code = "ENTRY_PAUSED"
    elif latest_decision_at is None or observed_at - latest_decision_at > _DECISION_PIPELINE_MAX_AGE:
        summary_code = "DECISION_PIPELINE_STALLED"
    elif at_capacity:
        summary_code = "ENTRY_BLOCKED"
        active_blocker = "MAX_OPEN_EXPOSURES"
    elif entry_runtime.get("execution_blocker"):
        summary_code = "ENTRY_BLOCKED"
        active_blocker = str(entry_runtime["execution_blocker"])
    elif dominant_reason is not None and _entry_blocking_reason(dominant_reason):
        summary_code = "ENTRY_BLOCKED"
        active_blocker = dominant_reason
    else:
        summary_code = "HEALTHY_WAITING_FOR_SIGNAL"

    if summary_code != "ENTRY_BLOCKED":
        active_blocker = {
            "SCHEDULER_OFFLINE": "SCHEDULER_OFFLINE",
            "EXCHANGE_UNAVAILABLE": "EXCHANGE_UNAVAILABLE",
            "MARKET_DATA_STALE": "MARKET_DATA_STALE",
            "RECONCILIATION_BLOCKED": "RECONCILIATION_BLOCKED",
            "DECISION_PIPELINE_STALLED": "DECISION_PIPELINE_STALLED",
            "ENTRY_PAUSED": entry_runtime.get("reason") or "ENTRY_PAUSED",
        }.get(summary_code)
    active_blocker_category = _reason_category(active_blocker) if active_blocker else None
    runtime_health = (
        "degraded"
        if summary_code
        in {
            "SCHEDULER_OFFLINE",
            "EXCHANGE_UNAVAILABLE",
            "MARKET_DATA_STALE",
            "RECONCILIATION_BLOCKED",
            "DECISION_PIPELINE_STALLED",
        }
        else "healthy"
    )

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
            "critical_jobs": scheduler.get("critical_jobs") or {},
            "scheduler_error": scheduler.get("scheduler_error"),
            "recovery": scheduler.get("recovery") or {},
        },
        "exchange": {"status": exchange_status},
        "data": {
            "fresh": bool(data.get("fresh")),
            "exchange_info_ready": bool(data.get("exchange_info_ready")),
        },
        "reconciliation": {
            "status": reconciliation_status,
            "blocked_symbols": list(reconciliation.get("blocked_symbols") or []),
            "affected_symbols": list(
                reconciliation.get("affected_symbols") or reconciliation.get("blocked_symbols") or []
            ),
            "discrepancy_codes": list(reconciliation.get("discrepancy_codes") or []),
            "entry_blocked": bool(reconciliation.get("blocked_symbols")),
            "recovery_action": reconciliation.get("recovery_action"),
            "last_healthy_at": reconciliation.get("last_healthy_at"),
        },
        "entry_runtime": {
            "trading_state": trading_state,
            "entry_authority": entry_runtime.get("entry_authority"),
            "entry_authorized": entry_authorized,
            "reason": entry_runtime.get("reason") or entry_runtime.get("entry_authority_reason"),
            "execution_blocker": entry_runtime.get("execution_blocker"),
        },
        "current_status": {
            "runtime_health": runtime_health,
            "active_blocker": active_blocker,
            "active_blocker_category": active_blocker_category,
            "active_blocker_since": _iso_datetime(latest_decision_at) if active_blocker else None,
            "currently_at_capacity": at_capacity,
            "current_open_positions": open_positions_count,
            "effective_max_open_positions": effective_max_open_positions,
            "capacity_source": capacity_source,
        },
        "decisions": {
            "total": len(normalized_decisions),
            "effective": len(effective_decisions),
            "duplicate": len(duplicate_decisions),
            "management": len(management_decisions),
            "latest_at": _iso_datetime(latest_decision_at),
            "reason_counts": reason_counts,
            "dominant_reason": dominant_reason,
            "strategy_filter_counts": strategy_filter_counts,
            "operational_block_counts": operational_block_counts,
            "system_failure_counts": system_failure_counts,
        },
        "historical_window": {
            "window_hours": window_hours,
            "strategy_filter_counts": strategy_filter_counts,
            "operational_block_counts": operational_block_counts,
            "system_failure_counts": system_failure_counts,
            "recent_system_failures": system_failure_counts,
        },
        "funnel": funnel or {},
        "throughput": {
            "current_open_positions": open_positions_count,
            "effective_max_open_positions": effective_max_open_positions,
            "capacity_source": capacity_source,
            "remaining_slots": (
                max(effective_max_open_positions - open_positions_count, 0)
                if effective_max_open_positions is not None and open_positions_count is not None
                else None
            ),
            "at_capacity": at_capacity,
            "strategy_filter_counts": strategy_filter_counts,
            "operational_block_counts": operational_block_counts,
            "system_failure_counts": system_failure_counts,
            "blocker_counts": operational_block_counts,
            "blocker_total": sum(operational_block_counts.values()),
        },
        "protection": protection_truth
        or {
            "positions": [],
            "protected_positions": 0,
            "unprotected_positions": 0,
            "p0_unprotected": False,
        },
        "summary_code": summary_code,
    }


@router.get("/snapshot")
def runtime_snapshot(db: Session = Depends(get_db_session)) -> dict:
    observed_at = datetime.now(UTC)
    projection = _runtime_projection(db, observed_at=observed_at)
    exchange = projection["exchange"]
    local_open = projection["local_open"]
    scheduler = projection["scheduler"]
    projection_id = projection["projection_id"]
    reconciliation = projection["reconciliation"]
    reconciliation["projection_id"] = projection_id
    mismatch = reconciliation["mismatch"]
    paper_runs = PaperRunRepository(db).list_paper_runs()
    entry_runtime = projection["entry_runtime"]
    return {
        "observed_at": observed_at.isoformat(),
        "projection_id": projection_id,
        "exchange": exchange,
        "local_projection": _datum(
            value=[_v2_position_payload(item) for item in local_open],
            source="V2_MANAGED_POSITION_FACTS",
            observed_at=observed_at,
        ),
        "mismatch": mismatch,
        "reconciliation": _datum(
            value=reconciliation,
            source="RUNTIME_RECONCILIATION_TRUTH",
            observed_at=observed_at,
            status="available" if reconciliation["status"] != "unavailable" else "unavailable",
            error=reconciliation.get("error"),
        ),
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
        "strategy_manifest": _datum(
            value=projection["strategy_manifest"]["value"],
            source="CANONICAL_STRATEGY_MANIFEST_V4",
            observed_at=observed_at,
            status=projection["strategy_manifest"]["status"],
            error=projection["strategy_manifest"]["error"],
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
                _v2_protection_payload(protection, position) for protection, position in projection["protection_rows"]
            ],
            source="V2_PROTECTION_FACTS",
            observed_at=observed_at,
        ),
        "current_protection": _datum(
            value=projection["current_protection"],
            source="BINANCE_LIVE_REDUCE_ONLY_PROTECTION",
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
    projection = _runtime_projection(db, observed_at=observed_at)
    v2_repo = _v2_repo(db)
    scheduler = projection["scheduler"]
    exchange = projection["exchange"]
    projection_id = projection["projection_id"]
    reconciliation = projection["reconciliation"]
    reconciliation["projection_id"] = projection_id
    decision_rows = v2_repo.list_recent_decisions(since=window_start, limit=500)
    decisions = []
    for decision, cycle in decision_rows:
        payload = _v2_decision_payload(decision, cycle)
        decisions.append(
            {
                "at": decision.created_at,
                "reason": payload["terminal_reason"],
                "strategy_terminal_reason": payload.get("strategy_terminal_reason"),
                "system_failure_reason": payload.get("system_failure_reason"),
                "execution_blocker": payload.get("execution_blocker"),
                "cycle_kind": (
                    "MANAGEMENT"
                    if isinstance(payload.get("production_trace"), dict)
                    and payload["production_trace"].get("management_only")
                    else "ENTRY_OPPORTUNITY"
                ),
                "signal_generated": payload["signal_generated"],
                "base_signal_detected": payload["base_signal_detected"],
                "base_signal_side": payload["base_signal_side"],
                "mtf_alignment_passed": payload["mtf_alignment_passed"],
                "confirmation_bars_passed": payload["confirmation_bars_passed"],
                "candidate_created": payload["candidate_created"],
                "entry_gate_result": payload["entry_gate_result"],
                "entry_submitted": payload["entry_submitted"],
                "effective_max_open_positions": payload["effective_max_open_positions"],
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
            "scheduler_error": scheduler.scheduler_error,
            "critical_jobs": scheduler.critical_jobs,
            "recovery": scheduler.recovery,
        },
        exchange=exchange,
        data={"fresh": scheduler.data_fresh, "exchange_info_ready": scheduler.exchange_info_ready},
        reconciliation={
            "status": reconciliation["status"],
            "blocked_symbols": reconciliation["entry_blocked_symbols"],
            "affected_symbols": reconciliation["affected_symbols"],
            "discrepancy_codes": reconciliation["discrepancy_codes"],
            "recovery_action": reconciliation["recovery_action"],
            "last_healthy_at": reconciliation["last_healthy_at"],
        },
        entry_runtime={
            "trading_state": scheduler.trading_state or "ENTRY_PAUSED",
            "entry_authority": scheduler.entry_authority or "NONE",
            "entry_authorized": bool(scheduler.entry_authorized),
            "reason": scheduler.entry_authority_reason or scheduler.reason,
        },
        decisions=decisions,
        entry_fills=fills,
        protection_truth=projection["current_protection"],
        open_positions_count=len(projection["local_open"]),
        execution_mode=scheduler.execution_mode,
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
        "signal_generated": sum(bool(item.get("base_signal_detected")) for item in effective),
        "base_signal_detected": sum(bool(item.get("base_signal_detected")) for item in effective),
        "mtf_confirmation_passed": sum(bool(item.get("mtf_alignment_passed")) for item in effective),
        "confirmation_bars_passed": sum(bool(item.get("confirmation_bars_passed")) for item in effective),
        "candidate_created": sum(bool(item.get("candidate_created")) for item in effective),
        "r2_cost_rejected": sum(item["reason"] == "NO_TRADE_COST_INEFFICIENT" for item in effective),
        "position_rejected": sum(item["reason"].startswith("POSITION_") for item in effective),
        "price_drift_rejected": sum(item["reason"].startswith("PRICE_DRIFT_") for item in effective),
        "intent_created": len(intent_rows),
        "exchange_submitted": sum(order.submitted_at is not None for order, _intent in order_rows),
        "exchange_filled": len({str(fill.exchange_order_id) for fill, _intent in fill_rows if fill.exchange_order_id}),
        "fill_events": len(fill_rows),
        "filled_orders": len({str(fill.exchange_order_id) for fill, _intent in fill_rows if fill.exchange_order_id}),
        "protection_confirmed": sum(
            protection.state in {"PROTECTION_ACTIVE", "PROTECTION_FILLED"} for protection, _position in protection_rows
        ),
        "protection_events": sum(
            protection.state in {"PROTECTION_ACTIVE", "PROTECTION_FILLED"} for protection, _position in protection_rows
        ),
        "metric_semantics": {
            "exchange_filled": "unique_entry_exchange_orders",
            "fill_events": "exchange_fill_receipts_trade_ids",
            "protection_confirmed": "V2_protection_records_confirmed_in_window",
            "protected_positions": "current_authoritative_positions_with_live_stop_and_take_profit",
        },
    }
    summary["projection_id"] = projection_id
    summary["reconciliation"]["projection_id"] = projection_id
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
    observed_at = datetime.now(UTC)
    projection = _runtime_projection(db, observed_at=observed_at)
    exchange = projection["exchange"]
    projection_id = projection["projection_id"]
    reconciliation = projection["reconciliation"]
    reconciliation["projection_id"] = projection_id
    return {
        "projection_id": projection_id,
        "exchange": exchange,
        "reconciliation": _datum(
            value=reconciliation,
            source="RUNTIME_RECONCILIATION_TRUTH",
            observed_at=observed_at,
            status="available" if reconciliation["status"] != "unavailable" else "unavailable",
            error=reconciliation.get("error"),
        ),
        "current_protection": _datum(
            value=projection["current_protection"],
            source="BINANCE_LIVE_REDUCE_ONLY_PROTECTION",
            observed_at=observed_at,
        ),
        "local": _datum(
            value=[_v2_position_payload(item) for item in projection["local_open"]],
            source="V2_MANAGED_POSITION_FACTS",
            observed_at=observed_at,
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
    projection = _runtime_projection(db, observed_at=observed_at)
    projection_id = projection["projection_id"]
    reconciliation = dict(projection["reconciliation"])
    reconciliation["projection_id"] = projection_id
    return reconciliation


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
