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
from services.strategy_library.canonical import canonical_hash
from services.strategy_library.context import TIMEFRAME_DELTAS, MarketContext, MarketContextBuilder
from services.strategy_library.proposal_pipeline import (
    PROPOSAL_CONTEXT_WINDOW_LENGTHS,
    RESEARCH_CANDIDATE_IDS,
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


def _load_v2_market_context(
    symbol: str,
    decision_time: datetime,
    *,
    active_entry_timeframe: TimeframeView | None = None,
) -> MarketContext:
    """Load one point-in-time research context without touching execution state."""

    from services.data.repository import DataRepository

    with get_session_factory()() as session:
        repo = DataRepository(session)
        bars_by_timeframe: dict[str, Any] = {}
        if active_entry_timeframe is not None:
            from shared.models.enums import Timeframe
            from shared.models.market import OHLCVBar

            bars_by_timeframe["15m"] = tuple(
                OHLCVBar(
                    symbol=symbol,
                    timeframe=Timeframe.M15,
                    time=bar.timestamp - TIMEFRAME_DELTAS["15m"],
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    volume=bar.volume,
                )
                for bar in active_entry_timeframe.bars
            )
        for timeframe in PROPOSAL_CONTEXT_WINDOW_LENGTHS:
            if timeframe == "15m" and active_entry_timeframe is not None:
                continue
            bars_by_timeframe[timeframe] = repo.list_ohlcv_bars(
                symbol=symbol,
                timeframe=timeframe,
                end_at=decision_time - TIMEFRAME_DELTAS[timeframe],
                limit=PROPOSAL_CONTEXT_WINDOW_LENGTHS[timeframe],
            )
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


def _safe_error_message(exc: Exception) -> str:
    return " ".join(str(exc).split())[:240] or "research shadow evaluation failed"


def _payload_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _payload_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _same_cycle_research_shadow_payload(
    context: MarketContext,
    *,
    active_entry_timeframe: TimeframeView,
    scheduler_session_id: str,
    scheduler_cycle_id: str,
    cycle_id: str,
    symbol: str,
    bar_close_time: datetime,
    active_strategy_id: str,
    active_decision: str,
    active_terminal_reason: str,
    created_at: datetime,
) -> dict[str, Any]:
    """Normalize pure research results for one authoritative ACTIVE bar.

    This function receives no adapter or execution service. Its return value is
    evidence only and cannot create an intent, order, position, or protection.
    """
    active_bar = active_entry_timeframe.last_closed
    research_bar = context.bars_15m.bars[-1] if context.bars_15m.bars else None
    if active_bar is None or research_bar is None:
        raise ValueError("same-cycle research requires an ACTIVE and research closed 15m bar")
    if context.symbol != symbol:
        raise ValueError(f"research symbol {context.symbol} does not match ACTIVE symbol {symbol}")
    if active_bar.timestamp != bar_close_time or context.bars_15m.last_closed_at != bar_close_time:
        raise ValueError("research and ACTIVE closed-bar timestamps are not aligned")

    active_bar_hash = canonical_hash(
        {
            "symbol": symbol,
            "bar_close_time": bar_close_time,
            "open": active_bar.open,
            "high": active_bar.high,
            "low": active_bar.low,
            "close": active_bar.close,
            "volume": active_bar.volume,
        }
    )
    research_bar_hash = canonical_hash(
        {
            "symbol": symbol,
            "bar_close_time": context.bars_15m.last_closed_at,
            "open": research_bar.open,
            "high": research_bar.high,
            "low": research_bar.low,
            "close": research_bar.close,
            "volume": research_bar.volume,
        }
    )
    if active_bar_hash != research_bar_hash:
        raise ValueError("research and ACTIVE closed-bar OHLCV values are not aligned")

    pipeline = _research_shadow_payload(context)
    context_hash = str(pipeline.get("context_hash") or "")
    market_snapshot_reference = canonical_hash(
        {
            "source_ids": context.source_ids,
            "symbol": symbol,
            "bar_close_time": bar_close_time,
            "active_bar_hash": active_bar_hash,
            "research_context_hash": context_hash,
        }
    )
    proposals = {
        str(proposal.get("strategy_id")): proposal
        for proposal in _payload_list(pipeline.get("proposals"))
        if isinstance(proposal, dict) and proposal.get("strategy_id")
    }
    selection = _payload_mapping(pipeline.get("selection"))
    selection_rejections = _payload_mapping(selection.get("rejected_reasons"))
    rejection_reasons = _payload_mapping(pipeline.get("rejection_reasons"))
    strategy_versions = _payload_mapping(pipeline.get("strategy_versions"))
    evaluation_errors = _payload_mapping(pipeline.get("evaluation_errors"))
    created_at_value = created_at.isoformat()
    bar_close_value = bar_close_time.isoformat()
    observations: list[dict[str, Any]] = []

    for strategy_id in RESEARCH_CANDIDATE_IDS:
        proposal = proposals.get(strategy_id)
        error = evaluation_errors.get(strategy_id)
        proposal_data = proposal if proposal is not None else {}
        strategy_version = str(proposal_data.get("strategy_version") or strategy_versions.get(strategy_id) or "unknown")
        observation: dict[str, Any] = {
            "scheduler_session_id": scheduler_session_id,
            "scheduler_cycle_id": scheduler_cycle_id,
            "cycle_id": cycle_id,
            "symbol": symbol,
            "bar_close_time": bar_close_value,
            "market_snapshot_reference": market_snapshot_reference,
            "strategy_id": strategy_id,
            "strategy_version": strategy_version,
            "lane": "RESEARCH_SHADOW",
            "signal_direction": None,
            "signal_present": proposal is not None,
            "decision_status": "SHADOW_NO_SIGNAL",
            "terminal_reason": str(rejection_reasons.get(strategy_id) or "candidate_conditions_not_met"),
            "entry_reference_price": None,
            "stop_reference_price": None,
            "target_reference_prices": [],
            "created_at": created_at_value,
        }
        if isinstance(error, dict):
            observation.update(
                {
                    "signal_present": False,
                    "decision_status": "SHADOW_STRATEGY_ERROR",
                    "terminal_reason": "SHADOW_STRATEGY_ERROR",
                    "error_class": str(error.get("error_class") or "Exception"),
                    "safe_error_message": str(error.get("safe_message") or "candidate evaluation failed"),
                }
            )
        elif proposal is not None:
            proposal_id = str(proposal.get("proposal_id") or "")
            rejected_reason = selection_rejections.get(proposal_id)
            entry_trigger = _payload_mapping(proposal.get("entry_trigger"))
            invalidation = _payload_mapping(proposal.get("invalidation"))
            targets = _payload_list(proposal.get("targets"))
            observation.update(
                {
                    "signal_direction": proposal.get("side"),
                    "decision_status": "SHADOW_STRATEGY_REJECTED" if rejected_reason else "SHADOW_SIGNAL_READY",
                    "terminal_reason": str(rejected_reason or "SHADOW_SIGNAL_READY"),
                    "entry_reference_price": entry_trigger.get("reference_price"),
                    "stop_reference_price": invalidation.get("stop_price"),
                    "target_reference_prices": [
                        target.get("price") for target in targets if isinstance(target, dict) and target.get("price")
                    ],
                }
            )
        observations.append(observation)

    return {
        "schema_version": "p1-same-cycle-research-shadow-v1",
        "status": "COMPLETED_WITH_ERRORS" if evaluation_errors else "COMPLETED",
        "lane": "RESEARCH_SHADOW",
        "scheduler_session_id": scheduler_session_id,
        "scheduler_cycle_id": scheduler_cycle_id,
        "cycle_id": cycle_id,
        "symbol": symbol,
        "bar_close_time": bar_close_value,
        "market_snapshot_reference": market_snapshot_reference,
        "active_strategy_id": active_strategy_id,
        "active_decision": active_decision,
        "active_terminal_reason": active_terminal_reason,
        "pipeline_version": pipeline.get("pipeline_version"),
        "context_hash": context_hash,
        "observations": observations,
        "created_at": created_at_value,
    }


def _research_shadow_error_payload(
    *,
    scheduler_session_id: str,
    scheduler_cycle_id: str,
    cycle_id: str,
    symbol: str,
    bar_close_time: datetime,
    active_strategy_id: str,
    active_decision: str,
    active_terminal_reason: str,
    created_at: datetime,
    error: Exception,
) -> dict[str, Any]:
    """Record a framework error without making ACTIVE depend on research."""
    return {
        "schema_version": "p1-same-cycle-research-shadow-v1",
        "status": "RESEARCH_SHADOW_ERROR",
        "lane": "RESEARCH_SHADOW",
        "scheduler_session_id": scheduler_session_id,
        "scheduler_cycle_id": scheduler_cycle_id,
        "cycle_id": cycle_id,
        "symbol": symbol,
        "bar_close_time": bar_close_time.isoformat(),
        "market_snapshot_reference": None,
        "active_strategy_id": active_strategy_id,
        "active_decision": active_decision,
        "active_terminal_reason": active_terminal_reason,
        "observations": [],
        "error_class": type(error).__name__,
        "safe_error_message": _safe_error_message(error),
        "created_at": created_at.isoformat(),
    }


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


def _append_research_shadow_payload(
    *,
    decision_id: str,
    research_shadow: dict[str, Any],
) -> None:
    """Append evidence to a completed decision after ACTIVE leases are released."""
    from services.automated_trading.infrastructure.models import V2ExecutionDecision

    with get_session_factory()() as session:
        decision = session.get(V2ExecutionDecision, decision_id)
        if decision is None:
            raise RuntimeError(f"ACTIVE decision {decision_id} disappeared before Research Shadow append")
        payload = dict(decision.payload or {})
        payload["market_snapshot_reference"] = research_shadow.get("market_snapshot_reference")
        payload["research_shadow"] = research_shadow
        decision.payload = payload
        session.commit()


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
    pending_research: list[dict[str, Any]] = []
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
                ai_review_budget_seconds=settings.v2_ai_review_budget_seconds,
            )
            try:
                result = run_automated_trading_cycle(request, adapter)
            except Exception as exc:  # noqa: BLE001
                symbol_result.update({"status": "error", "error": str(exc)})
                symbol_results.append(symbol_result)
                task_failed = True
                continue

            funnel_payload = dict(result.funnel_payload)
            active_strategy_id = str(funnel_payload.get("strategy_id") or "testnet_sampling_v2")
            active_decision = str(funnel_payload.get("terminal_stage") or "UNKNOWN")
            active_terminal_reason = str(funnel_payload.get("reason_code") or "UNKNOWN")
            funnel_payload.update(
                {
                    "active_strategy_id": active_strategy_id,
                    "active_decision": active_decision,
                    "active_terminal_reason": active_terminal_reason,
                }
            )
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
            pending_research.append(
                {
                    "decision_id": decision_id,
                    "cycle_id": cycle_id,
                    "symbol": symbol,
                    "bar_timestamp": bar_timestamp,
                    "entry_timeframe": entry_timeframe,
                    "active_strategy_id": active_strategy_id,
                    "active_decision": active_decision,
                    "active_terminal_reason": active_terminal_reason,
                }
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

    for pending in pending_research:
        cycle_id = str(pending["cycle_id"])
        symbol = str(pending["symbol"])
        bar_timestamp = pending["bar_timestamp"]
        try:
            if market_context_loader is None:
                context = _load_v2_market_context(
                    symbol,
                    bar_timestamp,
                    active_entry_timeframe=pending["entry_timeframe"],
                )
            else:
                context = load_market_context(symbol, bar_timestamp)
            research_shadow = _same_cycle_research_shadow_payload(
                context,
                active_entry_timeframe=pending["entry_timeframe"],
                scheduler_session_id=instance_id,
                scheduler_cycle_id=claim.scheduler_cycle_id,
                cycle_id=cycle_id,
                symbol=symbol,
                bar_close_time=bar_timestamp,
                active_strategy_id=str(pending["active_strategy_id"]),
                active_decision=str(pending["active_decision"]),
                active_terminal_reason=str(pending["active_terminal_reason"]),
                created_at=datetime.now(UTC),
            )
        except Exception as exc:  # noqa: BLE001
            research_shadow = _research_shadow_error_payload(
                scheduler_session_id=instance_id,
                scheduler_cycle_id=claim.scheduler_cycle_id,
                cycle_id=cycle_id,
                symbol=symbol,
                bar_close_time=bar_timestamp,
                active_strategy_id=str(pending["active_strategy_id"]),
                active_decision=str(pending["active_decision"]),
                active_terminal_reason=str(pending["active_terminal_reason"]),
                created_at=datetime.now(UTC),
                error=exc,
            )
            logger.warning(
                "Research Shadow observer failed without blocking ACTIVE cycle=%s symbol=%s error=%s",
                cycle_id,
                symbol,
                research_shadow["safe_error_message"],
            )
        try:
            _append_research_shadow_payload(
                decision_id=str(pending["decision_id"]),
                research_shadow=research_shadow,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Research Shadow evidence append failed without blocking ACTIVE cycle=%s symbol=%s error=%s",
                cycle_id,
                symbol,
                _safe_error_message(exc),
            )

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
