"""V2 automated trading scheduler / Celery entry bridge.

Thin adapter between RuntimeScheduler / Celery tasks and the V2 cycle service.
Uses SchedulerCoordinator for leadership (same lease model as legacy paper cycles).
"""

from __future__ import annotations

import logging
import os
import socket
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from services.automated_trading.application.cycle_service import CycleRequest, run_automated_trading_cycle
from services.automated_trading.application.decision_service import BarView, TimeframeView
from services.automated_trading.domain.enums import V2ExecutionMode
from services.automated_trading.infrastructure.repository import AutomatedTradingRepository
from services.automated_trading.infrastructure.runtime_lock import (
    EngineActivation,
    EngineActivationConfig,
    resolve_engine_activation,
)
from services.data.universe import AUTO_SIMULATION_EXECUTION_SYMBOLS
from services.database import get_session_factory
from services.execution.scheduler_coordination import SchedulerCoordinator
from shared.config import settings

logger = logging.getLogger(__name__)

V2_CYCLE_TIMEFRAME = "15m"
AdapterFactory = Callable[[V2ExecutionMode], Any]


def _slot_start(observed_at: datetime, interval_seconds: float) -> datetime:
    timestamp = observed_at.astimezone(UTC).timestamp()
    slot = int(timestamp / interval_seconds) * interval_seconds
    return datetime.fromtimestamp(slot, tz=UTC)


def resolve_scheduler_v2_jobs(config: EngineActivationConfig) -> frozenset[str]:
    """Return periodic job names to register for the in-process RuntimeScheduler."""
    jobs: set[str] = set()
    if config.allow_legacy_writer:
        jobs.update({"paper_runtime_cycle", "paper_observation_cycle"})
    if config.v2_activation is not EngineActivation.DISABLED:
        jobs.add("automated_trading_v2_cycle")
    return frozenset(jobs)


def _default_adapter_factory(execution_mode: V2ExecutionMode) -> Any:
    from services.automated_trading.infrastructure.binance_adapter import BinanceTestnetAdapter

    return BinanceTestnetAdapter(execution_mode=execution_mode)


def _load_v2_entry_timeframe(symbol: str, timeframe: str = V2_CYCLE_TIMEFRAME) -> TimeframeView:
    with get_session_factory()() as session:
        from services.data.repository import DataRepository

        rows = DataRepository(session).list_ohlcv_bars(symbol=symbol, timeframe=timeframe, limit=200)
    bars = tuple(
        BarView(
            timestamp=row.timestamp,
            open=Decimal(str(row.open)),
            high=Decimal(str(row.high)),
            low=Decimal(str(row.low)),
            close=Decimal(str(row.close)),
            volume=Decimal(str(row.volume)),
        )
        for row in rows
    )
    return TimeframeView(timeframe=timeframe, bars=bars)


def _persist_v2_cycle_facts(
    *,
    cycle_id: str,
    symbol: str,
    timeframe: str,
    bar_timestamp: datetime,
    execution_mode: V2ExecutionMode,
    fencing_token: str,
    funnel_payload: dict[str, Any],
    completed_at: datetime,
) -> str:
    decision_id = str(uuid.uuid4())
    terminal_stage = str(funnel_payload.get("terminal_stage") or "UNKNOWN")
    terminal_reason = funnel_payload.get("reason_code")
    with get_session_factory()() as session:
        repo = AutomatedTradingRepository(session)
        repo.create_cycle(
            cycle_id=cycle_id,
            symbol=symbol,
            timeframe=timeframe,
            bar_timestamp=bar_timestamp,
            execution_mode=execution_mode,
            fencing_token=fencing_token,
        )
        repo.create_decision(
            decision_id=decision_id,
            cycle_id=cycle_id,
            candidate_key=funnel_payload.get("candidate_key"),
            terminal_reason=str(terminal_reason) if terminal_reason is not None else None,
            payload=funnel_payload,
        )
        repo.complete_cycle(cycle_id, decision_terminal=terminal_stage, completed_at=completed_at)
        session.commit()
    return decision_id


def execute_v2_automated_trading_cycles(
    request_payload: dict[str, Any] | None = None,
    *,
    adapter_factory: AdapterFactory | None = None,
    timeframe_loader: Callable[[str, str], TimeframeView] | None = None,
) -> dict[str, Any]:
    """Run one coordinated V2 cycle pass for BTC/USDT and ETH/USDT."""
    payload = dict(request_payload or {})
    config = resolve_engine_activation(settings)
    if config.v2_activation is EngineActivation.DISABLED:
        return {"status": "skipped", "reason": "v2_disabled", "v2_activation": config.v2_activation.value}

    now = datetime.now(UTC)
    interval_seconds = float(payload.get("interval_seconds") or settings.paper_runtime_cycle_seconds)
    bar_slot = _slot_start(now, interval_seconds)
    lease_name = f"automated_trading_v2:{config.execution_mode.value}:{int(bar_slot.timestamp())}"
    instance_id = str(
        payload.get("scheduler_instance_id") or f"v2:{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:12]}"
    )
    coordinator = SchedulerCoordinator(session_factory=get_session_factory(), instance_id=instance_id)
    lease_ttl = max(90.0, interval_seconds * 3)

    if not coordinator.acquire_or_renew_lease(lease_name=lease_name, now=now, ttl_seconds=lease_ttl):
        return {"status": "standby_not_leader", "lease_name": lease_name}

    fencing_token = coordinator.fencing_token(lease_name=lease_name)
    if fencing_token is None:
        coordinator.release_lease(lease_name=lease_name)
        return {"status": "lease_lost", "lease_name": lease_name}

    claim = coordinator.claim_cycle(
        job_name=lease_name,
        scheduled_for=bar_slot,
        cycle_source="automated_trading_v2",
        run_mode=config.execution_mode.value,
        fencing_token=fencing_token,
    )
    if not claim.claimed or claim.scheduler_cycle_id is None:
        coordinator.release_lease(lease_name=lease_name)
        return {
            "status": "duplicate_slot_skipped",
            "lease_name": lease_name,
            "scheduled_for": bar_slot.isoformat(),
        }

    build_adapter = adapter_factory or _default_adapter_factory
    load_timeframe = timeframe_loader or _load_v2_entry_timeframe
    symbols = list(payload.get("symbols") or AUTO_SIMULATION_EXECUTION_SYMBOLS)
    timeframe = str(payload.get("timeframe") or V2_CYCLE_TIMEFRAME)

    symbol_results: list[dict[str, Any]] = []
    task_failed = False
    try:
        adapter = build_adapter(config.execution_mode)
        for symbol in symbols:
            cycle_id = str(uuid.uuid4())
            cycle_fencing_token = f"{lease_name}:{fencing_token}:{symbol}:{cycle_id}"
            symbol_result: dict[str, Any] = {"symbol": symbol, "cycle_id": cycle_id}
            try:
                entry_timeframe = load_timeframe(symbol, timeframe)
            except Exception as exc:  # noqa: BLE001
                symbol_result.update({"status": "error", "error": f"market_data_unavailable: {exc}"})
                symbol_results.append(symbol_result)
                task_failed = True
                continue

            bar_timestamp = entry_timeframe.last_closed.timestamp if entry_timeframe.last_closed else bar_slot
            request = CycleRequest(
                cycle_id=cycle_id,
                symbol=symbol,
                timeframe=timeframe,
                entry_timeframe=entry_timeframe,
                execution_mode=config.execution_mode,
                engine_activation=config.v2_activation,
                fencing_token=cycle_fencing_token,
                now=now,
            )
            try:
                result = run_automated_trading_cycle(request, adapter)
            except Exception as exc:  # noqa: BLE001
                symbol_result.update({"status": "error", "error": str(exc)})
                symbol_results.append(symbol_result)
                task_failed = True
                continue

            decision_id = _persist_v2_cycle_facts(
                cycle_id=cycle_id,
                symbol=symbol,
                timeframe=timeframe,
                bar_timestamp=bar_timestamp,
                execution_mode=config.execution_mode,
                fencing_token=cycle_fencing_token,
                funnel_payload=result.funnel_payload,
                completed_at=datetime.now(UTC),
            )
            symbol_result.update(
                {
                    "status": "completed",
                    "decision_id": decision_id,
                    "reconciliation_status": result.reconciliation_status.value,
                    "terminal_stage": result.funnel_payload.get("terminal_stage"),
                    "entry_submitted": result.entry_submitted,
                    "errors": list(result.errors),
                }
            )
            symbol_results.append(symbol_result)
    finally:
        coordinator.finish_cycle(
            claim.scheduler_cycle_id,
            status="failed" if task_failed else "completed",
            failure_reason="one_or_more_symbol_cycles_failed" if task_failed else None,
        )
        coordinator.release_lease(lease_name=lease_name)

    return {
        "status": "completed" if not task_failed else "partial_failure",
        "v2_activation": config.v2_activation.value,
        "execution_mode": config.execution_mode.value,
        "lease_name": lease_name,
        "scheduler_cycle_id": claim.scheduler_cycle_id,
        "symbols": symbols,
        "results": symbol_results,
    }
