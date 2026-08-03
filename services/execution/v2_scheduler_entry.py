"""V2 automated trading scheduler / Celery entry bridge.

Thin adapter between RuntimeScheduler / Celery tasks and the V2 cycle service.
Uses SchedulerCoordinator for leadership (same lease model as legacy paper cycles).
"""

from __future__ import annotations

import logging
import os
import socket
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from services.automated_trading.application.cycle_service import CycleRequest, run_automated_trading_cycle
from services.automated_trading.application.decision_service import BarView, TimeframeView
from services.automated_trading.application.operator_profile import V2ExecutionSettings, resolve_v2_execution_settings
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
from services.strategy_library.context import TIMEFRAME_DELTAS, MarketContext, MarketContextBuilder
from services.strategy_library.proposal_pipeline import (
    PROPOSAL_CONTEXT_WINDOW_LENGTHS,
    proposal_pipeline_payload,
    run_proposal_pipeline,
)
from shared.config import settings

logger = logging.getLogger(__name__)

V2_CYCLE_TIMEFRAME = "15m"
AdapterFactory = Callable[[V2ExecutionMode], Any]
MarketContextLoader = Callable[[str, datetime], MarketContext]


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
        from services.data.binance import TIMEFRAME_TO_SECONDS
        from services.data.repository import DataRepository

        rows = DataRepository(session).list_ohlcv_bars(symbol=symbol, timeframe=timeframe, limit=200)
    timeframe_seconds = TIMEFRAME_TO_SECONDS.get(timeframe)
    if timeframe_seconds is None:
        raise ValueError(f"Unsupported V2 decision timeframe: {timeframe}")
    observed_at = datetime.now(UTC)

    def closed_at(row) -> datetime:  # noqa: ANN001
        opened_at = row.timestamp
        if opened_at.tzinfo is None:
            opened_at = opened_at.replace(tzinfo=UTC)
        return opened_at.astimezone(UTC) + timedelta(seconds=timeframe_seconds)

    bars = tuple(
        BarView(
            timestamp=closed_at(row),
            open=Decimal(str(row.open)),
            high=Decimal(str(row.high)),
            low=Decimal(str(row.low)),
            close=Decimal(str(row.close)),
            volume=Decimal(str(row.volume)),
        )
        for row in rows
        if closed_at(row) <= observed_at
    )
    return TimeframeView(timeframe=timeframe, bars=bars)


def _load_v2_market_context(symbol: str, decision_time: datetime) -> MarketContext:
    """Load one point-in-time research context without touching execution state."""

    from services.data.repository import DataRepository

    with get_session_factory()() as session:
        repo = DataRepository(session)
        bars_by_timeframe = {
            timeframe: repo.list_ohlcv_bars(
                symbol=symbol,
                timeframe=timeframe,
                end_at=decision_time - TIMEFRAME_DELTAS[timeframe],
                limit=PROPOSAL_CONTEXT_WINDOW_LENGTHS[timeframe],
            )
            for timeframe in PROPOSAL_CONTEXT_WINDOW_LENGTHS
        }
        market_extras = repo.list_market_extras(symbol=symbol, end_at=decision_time, limit=1)
    return MarketContextBuilder().build(
        symbol=symbol,
        decision_time=decision_time,
        bars_by_timeframe=bars_by_timeframe,
        market_extras=market_extras,
        source_ids=("runtime_data_repository",),
    )


def _research_shadow_payload(context: MarketContext) -> dict[str, Any]:
    """Produce the same canonical research output consumed by replay."""

    return proposal_pipeline_payload(run_proposal_pipeline(context))


def _ensure_v2_cycle(
    *,
    cycle_id: str,
    symbol: str,
    timeframe: str,
    bar_timestamp: datetime,
    execution_mode: V2ExecutionMode,
    fencing_token: str,
) -> None:
    """Create the cycle row before entry so fact FKs can attach."""
    from services.automated_trading.infrastructure.models import V2ExecutionCycle

    with get_session_factory()() as session:
        if session.get(V2ExecutionCycle, cycle_id) is None:
            AutomatedTradingRepository(session).create_cycle(
                cycle_id=cycle_id,
                symbol=symbol,
                timeframe=timeframe,
                bar_timestamp=bar_timestamp,
                execution_mode=execution_mode,
                fencing_token=fencing_token,
            )
            session.commit()


def _load_already_evaluated_bars(
    *,
    symbol: str,
    timeframe: str,
    execution_mode: V2ExecutionMode,
) -> frozenset[datetime]:
    """Return closed bars that already reached a persisted terminal decision."""
    from sqlalchemy import select

    from services.automated_trading.infrastructure.models import V2ExecutionCycle

    with get_session_factory()() as session:
        timestamps = tuple(
            session.scalars(
                select(V2ExecutionCycle.bar_timestamp).where(
                    V2ExecutionCycle.symbol == symbol,
                    V2ExecutionCycle.timeframe == timeframe,
                    V2ExecutionCycle.execution_mode == execution_mode.value,
                    V2ExecutionCycle.decision_terminal.is_not(None),
                )
            )
        )
    return frozenset(
        timestamp.replace(tzinfo=UTC) if timestamp.tzinfo is None else timestamp.astimezone(UTC)
        for timestamp in timestamps
    )


def _load_v2_operator_execution_settings(*, symbol: str, cycle_id: str) -> V2ExecutionSettings:
    """Load the single running directional profile and activate NEXT_CYCLE once.

    The existing PaperRun/ConfigSnapshot pair remains the sole operator-facing
    store. V2 only consumes it; it never regenerates, overwrites, or mirrors a
    second profile.
    """
    from services.strategy_library import ConfigSnapshotRepository, PaperRunRepository

    with get_session_factory()() as session:
        paper_repo = PaperRunRepository(session)
        running_directional = [
            run
            for run in paper_repo.list_paper_runs()
            if run.paper_status == "running" and run.execution_profile.get("strategy_lane") == "directional"
        ]
        if not running_directional:
            return resolve_v2_execution_settings(symbol, None)
        if len(running_directional) != 1:
            raise RuntimeError("ambiguous running directional PaperRun; V2 entry configuration is fail-closed")

        run = running_directional[0]
        assert run.paper_run_id is not None
        snapshots = ConfigSnapshotRepository(session)
        snapshots.activate_pending(run.paper_run_id, cycle_id=cycle_id)
        active = snapshots.get_active(run.paper_run_id)
        active_profile = active.config.get("execution_profile") if active is not None else None
        profile = active_profile if isinstance(active_profile, Mapping) else run.execution_profile
        return resolve_v2_execution_settings(symbol, profile)


def _finalize_v2_cycle_decision(
    *,
    cycle_id: str,
    decision_id: str,
    symbol: str,
    timeframe: str,
    bar_timestamp: datetime,
    execution_mode: V2ExecutionMode,
    fencing_token: str,
    funnel_payload: dict[str, Any],
    completed_at: datetime,
) -> str:
    """Upsert decision payload and mark the cycle complete."""
    from services.automated_trading.infrastructure.models import (
        V2ExecutionCycle,
        V2ExecutionDecision,
    )

    terminal_stage = str(funnel_payload.get("terminal_stage") or "UNKNOWN")
    terminal_reason = funnel_payload.get("reason_code")
    with get_session_factory()() as session:
        repo = AutomatedTradingRepository(session)
        if session.get(V2ExecutionCycle, cycle_id) is None:
            repo.create_cycle(
                cycle_id=cycle_id,
                symbol=symbol,
                timeframe=timeframe,
                bar_timestamp=bar_timestamp,
                execution_mode=execution_mode,
                fencing_token=fencing_token,
            )
        existing = session.get(V2ExecutionDecision, decision_id)
        if existing is None:
            repo.create_decision(
                decision_id=decision_id,
                cycle_id=cycle_id,
                candidate_key=funnel_payload.get("candidate_key"),
                terminal_reason=str(terminal_reason) if terminal_reason is not None else None,
                payload=funnel_payload,
            )
        else:
            existing.candidate_key = funnel_payload.get("candidate_key")
            existing.terminal_reason = str(terminal_reason) if terminal_reason is not None else None
            existing.payload = funnel_payload
        repo.complete_cycle(cycle_id, decision_terminal=terminal_stage, completed_at=completed_at)
        session.commit()
    return decision_id


def execute_v2_automated_trading_cycles(
    request_payload: dict[str, Any] | None = None,
    *,
    adapter_factory: AdapterFactory | None = None,
    timeframe_loader: Callable[[str, str], TimeframeView] | None = None,
    market_context_loader: MarketContextLoader | None = None,
) -> dict[str, Any]:
    """Run one coordinated V2 cycle pass for BTC/USDT and ETH/USDT."""
    payload = dict(request_payload or {})
    config = resolve_engine_activation(settings)
    if config.v2_activation is EngineActivation.DISABLED:
        return {"status": "skipped", "reason": "v2_disabled", "v2_activation": config.v2_activation.value}
    logger.info(
        "V2 cycle started activation=%s execution_mode=%s scheduler_instance_id=%s",
        config.v2_activation.value,
        config.execution_mode.value,
        payload.get("scheduler_instance_id"),
    )

    now = datetime.now(UTC)
    interval_seconds = float(payload.get("interval_seconds") or settings.paper_runtime_cycle_seconds)
    bar_slot = _slot_start(now, interval_seconds)
    lease_name = f"automated_trading_v2:{config.execution_mode.value}:{int(bar_slot.timestamp())}"
    instance_id = str(
        payload.get("scheduler_instance_id") or f"v2:{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:12]}"
    )
    coordinator = SchedulerCoordinator(session_factory=get_session_factory(), instance_id=instance_id)
    lease_ttl = max(90.0, interval_seconds * 3)
    writer_lease_name = f"automated_trading_v2:{config.execution_mode.value}:single_writer"
    if not coordinator.acquire_or_renew_lease(
        lease_name=writer_lease_name,
        now=now,
        ttl_seconds=lease_ttl,
    ):
        return {
            "status": "active_cycle_running",
            "writer_lease_name": writer_lease_name,
        }

    if not coordinator.acquire_or_renew_lease(lease_name=lease_name, now=now, ttl_seconds=lease_ttl):
        coordinator.release_lease(lease_name=writer_lease_name)
        return {"status": "standby_not_leader", "lease_name": lease_name}

    fencing_token = coordinator.fencing_token(lease_name=lease_name)
    if fencing_token is None:
        coordinator.release_lease(lease_name=lease_name)
        coordinator.release_lease(lease_name=writer_lease_name)
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
        coordinator.release_lease(lease_name=writer_lease_name)
        return {
            "status": "duplicate_slot_skipped",
            "lease_name": lease_name,
            "scheduled_for": bar_slot.isoformat(),
        }

    build_adapter = adapter_factory or _default_adapter_factory
    load_timeframe = timeframe_loader or _load_v2_entry_timeframe
    load_market_context = market_context_loader or _load_v2_market_context
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
                operator_settings = _load_v2_operator_execution_settings(symbol=symbol, cycle_id=cycle_id)
            except Exception as exc:  # noqa: BLE001
                symbol_result.update({"status": "error", "error": f"operator_profile_unavailable: {exc}"})
                symbol_results.append(symbol_result)
                task_failed = True
                continue
            try:
                entry_timeframe = load_timeframe(symbol, timeframe)
            except Exception as exc:  # noqa: BLE001
                symbol_result.update({"status": "error", "error": f"market_data_unavailable: {exc}"})
                symbol_results.append(symbol_result)
                task_failed = True
                continue

            bar_timestamp = entry_timeframe.last_closed.timestamp if entry_timeframe.last_closed else bar_slot
            research_shadow: dict[str, Any] | None = None
            if config.v2_activation is EngineActivation.SHADOW:
                try:
                    research_shadow = _research_shadow_payload(load_market_context(symbol, bar_timestamp))
                except Exception as exc:  # noqa: BLE001
                    research_shadow = {
                        "pipeline_version": "proposal-pipeline-v1",
                        "status": "UNAVAILABLE",
                        "rejection_reasons": {"market_context": f"{type(exc).__name__}: {exc}"},
                    }
            decision_id = str(uuid.uuid4())
            try:
                _ensure_v2_cycle(
                    cycle_id=cycle_id,
                    symbol=symbol,
                    timeframe=timeframe,
                    bar_timestamp=bar_timestamp,
                    execution_mode=config.execution_mode,
                    fencing_token=cycle_fencing_token,
                )
            except Exception as exc:  # noqa: BLE001
                symbol_result.update({"status": "error", "error": f"cycle_persist_failed: {exc}"})
                symbol_results.append(symbol_result)
                task_failed = True
                continue

            request = CycleRequest(
                cycle_id=cycle_id,
                symbol=symbol,
                timeframe=timeframe,
                entry_timeframe=entry_timeframe,
                execution_mode=config.execution_mode,
                engine_activation=config.v2_activation,
                fencing_token=cycle_fencing_token,
                now=now,
                risk_per_trade=operator_settings.risk_per_trade,
                max_leverage=operator_settings.max_leverage,
                order_notional_usdt=operator_settings.order_notional_usdt,
                max_position_fraction=operator_settings.max_position_fraction,
                already_evaluated_bars=_load_already_evaluated_bars(
                    symbol=symbol,
                    timeframe=timeframe,
                    execution_mode=config.execution_mode,
                ),
                persist_facts=config.v2_activation is EngineActivation.ACTIVE,
                decision_id=decision_id,
                sampling_fallback_enabled=operator_settings.sampling_fallback_enabled,
            )
            try:
                result = run_automated_trading_cycle(request, adapter)
            except Exception as exc:  # noqa: BLE001
                symbol_result.update({"status": "error", "error": str(exc)})
                symbol_results.append(symbol_result)
                task_failed = True
                continue

            funnel_payload = dict(result.funnel_payload)
            if research_shadow is not None:
                funnel_payload["research_shadow"] = research_shadow
            decision_id = _finalize_v2_cycle_decision(
                cycle_id=cycle_id,
                decision_id=decision_id,
                symbol=symbol,
                timeframe=timeframe,
                bar_timestamp=bar_timestamp,
                execution_mode=config.execution_mode,
                fencing_token=cycle_fencing_token,
                funnel_payload=funnel_payload,
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
        coordinator.release_lease(lease_name=writer_lease_name)

    return {
        "status": "completed" if not task_failed else "partial_failure",
        "v2_activation": config.v2_activation.value,
        "execution_mode": config.execution_mode.value,
        "lease_name": lease_name,
        "writer_lease_name": writer_lease_name,
        "scheduler_cycle_id": claim.scheduler_cycle_id,
        "symbols": symbols,
        "results": symbol_results,
    }
