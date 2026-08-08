"""Gate 2: V2 must be driven from Scheduler/Celery task entry, not direct cycle calls.

Tests invoke the formal task module / execute_v2 bridge, then open a NEW
DB session to assert persisted Cycle + Decision facts.
"""

from __future__ import annotations

import logging
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from services.automated_trading.application.decision_service import BarView, TimeframeView
from services.automated_trading.domain.enums import V2ExecutionMode
from services.automated_trading.infrastructure.models import (
    Base,
    V2ExchangeOrder,
    V2ExecutionCycle,
    V2ExecutionDecision,
    V2ExecutionIntent,
    V2ManagedPosition,
)
from services.automated_trading.infrastructure.runtime_lock import EngineActivation, EngineActivationConfig
from services.execution import tasks as tasks_mod
from services.execution.scheduler import SchedulerCoordinator
from services.execution.v2_scheduler_entry import (
    _load_v2_entry_timeframe,
    _load_v2_market_context,
    execute_v2_automated_trading_cycles,
    resolve_scheduler_v2_jobs,
)
from services.strategy_library import ConfigSnapshotRepository, PaperRunRepository, StrategyRepository
from services.strategy_library.context import TIMEFRAME_DELTAS
from services.strategy_library.proposal_pipeline import PROPOSAL_CONTEXT_WINDOW_LENGTHS
from shared.models import ConfigSnapshot, PaperRun, StrategyCreate, StrategyRules


@pytest.fixture
def v2_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'gate2.db'}")

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _connection_record):  # type: ignore[no-untyped-def]
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    # Also create scheduler coordination tables used by the lease path
    from services.strategy_library.models import Base as SharedBase

    SharedBase.metadata.create_all(engine, checkfirst=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr("services.database.get_session_factory", lambda: factory)
    monkeypatch.setattr("services.execution.v2_scheduler_entry.get_session_factory", lambda: factory)
    yield factory
    engine.dispose()


def _bars() -> TimeframeView:
    ts = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    bar = BarView(
        timestamp=ts,
        open=Decimal("65000"),
        high=Decimal("65100"),
        low=Decimal("64900"),
        close=Decimal("65050"),
        volume=Decimal("100"),
    )
    return TimeframeView(timeframe="15m", bars=(bar, bar))


def _fake_adapter(_mode: V2ExecutionMode):
    adapter = MagicMock()
    adapter.fetch_authoritative_snapshot.return_value = SimpleNamespace(
        equity=Decimal("10000"),
        positions=(),
        open_orders=(),
        available=True,
    )
    adapter.fetch_market_snapshot.return_value = SimpleNamespace(
        symbol="BTC/USDT",
        current_price=Decimal("65000"),
        tick_size=Decimal("0.1"),
        step_size=Decimal("0.001"),
        atr=Decimal("100"),
    )
    return adapter


def test_formal_task_entrypoint_exists() -> None:
    assert hasattr(tasks_mod, "run_v2_automated_trading_cycles")
    assert tasks_mod.run_v2_automated_trading_cycles.name.endswith("run_v2_automated_trading_cycles")


def test_v2_active_does_not_register_legacy_writer() -> None:
    config = EngineActivationConfig(
        v2_activation=EngineActivation.ACTIVE,
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        allow_legacy_writer=False,
        warnings=[],
    )
    jobs = resolve_scheduler_v2_jobs(config)
    assert "automated_trading_v2_cycle" in jobs
    assert "paper_runtime_cycle" not in jobs


def test_v2_shadow_keeps_legacy_and_v2() -> None:
    config = EngineActivationConfig(
        v2_activation=EngineActivation.SHADOW,
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        allow_legacy_writer=True,
        warnings=[],
    )
    jobs = resolve_scheduler_v2_jobs(config)
    assert "automated_trading_v2_cycle" in jobs
    assert "paper_runtime_cycle" in jobs


def test_v2_timeframe_loader_exposes_exchange_bar_close_time(monkeypatch) -> None:
    open_time = datetime(2026, 7, 29, 7, 15, tzinfo=UTC)
    row = SimpleNamespace(
        timestamp=open_time,
        open=Decimal("65000"),
        high=Decimal("65100"),
        low=Decimal("64900"),
        close=Decimal("65050"),
        volume=Decimal("100"),
    )

    class FakeDataRepository:
        def __init__(self, _session) -> None:
            pass

        def list_ohlcv_bars(self, **_kwargs):
            return [row]

    monkeypatch.setattr("services.execution.v2_scheduler_entry.get_session_factory", lambda: lambda: nullcontext())
    monkeypatch.setattr("services.data.repository.DataRepository", FakeDataRepository)

    timeframe = _load_v2_entry_timeframe("BTC/USDT", "15m")

    assert timeframe.last_closed is not None
    assert timeframe.last_closed.timestamp == datetime(2026, 7, 29, 7, 30, tzinfo=UTC)


def test_v2_timeframe_loader_excludes_current_unclosed_bar(monkeypatch) -> None:
    observed_at = datetime.now(UTC)
    closed_row = SimpleNamespace(
        timestamp=observed_at - timedelta(minutes=16),
        open=Decimal("65000"),
        high=Decimal("65100"),
        low=Decimal("64900"),
        close=Decimal("65050"),
        volume=Decimal("100"),
    )
    open_row = SimpleNamespace(
        timestamp=observed_at - timedelta(minutes=5),
        open=Decimal("65050"),
        high=Decimal("65200"),
        low=Decimal("65000"),
        close=Decimal("65150"),
        volume=Decimal("80"),
    )

    class FakeDataRepository:
        def __init__(self, _session) -> None:
            pass

        def list_ohlcv_bars(self, **_kwargs):
            return [closed_row, open_row]

    monkeypatch.setattr("services.execution.v2_scheduler_entry.get_session_factory", lambda: lambda: nullcontext())
    monkeypatch.setattr("services.data.repository.DataRepository", FakeDataRepository)

    timeframe = _load_v2_entry_timeframe("BTC/USDT", "15m")

    assert len(timeframe.bars) == 1
    assert timeframe.last_closed is not None
    assert timeframe.last_closed.close == Decimal("65050")


def test_v2_market_context_loader_uses_shared_replay_window_lengths(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeDataRepository:
        def __init__(self, _session) -> None:
            pass

        def list_ohlcv_bars(self, **kwargs):  # noqa: ANN003, ANN201
            calls.append(kwargs)
            return []

        def list_market_extras(self, **_kwargs):  # noqa: ANN003, ANN201
            return []

    decision_time = datetime(2026, 7, 29, 8, 0, tzinfo=UTC)
    monkeypatch.setattr("services.execution.v2_scheduler_entry.get_session_factory", lambda: lambda: nullcontext())
    monkeypatch.setattr("services.data.repository.DataRepository", FakeDataRepository)

    _load_v2_market_context("BTC/USDT", decision_time)

    assert {str(call["timeframe"]): int(call["limit"]) for call in calls} == PROPOSAL_CONTEXT_WINDOW_LENGTHS
    assert all(call["end_at"] == decision_time - TIMEFRAME_DELTAS[str(call["timeframe"])] for call in calls)


def test_v2_active_scheduler_persists_entry_fact_chain(v2_db, monkeypatch, caplog) -> None:
    """Formal entry must persist Cycle+Decision; verified via a fresh Session."""
    caplog.set_level(logging.INFO, logger="services.execution.v2_scheduler_entry")
    monkeypatch.setattr(
        "services.execution.v2_scheduler_entry.resolve_engine_activation",
        lambda _settings: EngineActivationConfig(
            v2_activation=EngineActivation.ACTIVE,
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            allow_legacy_writer=False,
            warnings=[],
        ),
    )
    captured_requests = []

    def fake_cycle(request, _adapter):
        captured_requests.append(request)
        return SimpleNamespace(
            funnel_payload={
                "terminal_stage": "NO_CANDIDATE",
                "reason_code": "technical_signals_insufficient",
                "candidate_key": None,
            },
            reconciliation_status=SimpleNamespace(value="HEALTHY"),
            entry_submitted=False,
            errors=[],
        )

    monkeypatch.setattr(
        "services.execution.v2_scheduler_entry.run_automated_trading_cycle",
        fake_cycle,
    )

    result = execute_v2_automated_trading_cycles(
        {
            "symbols": ["BTC/USDT"],
            "interval_seconds": 60,
            "scheduler_instance_id": "gate2-test-instance",
        },
        adapter_factory=_fake_adapter,
        timeframe_loader=lambda _symbol, _tf: _bars(),
    )

    assert result["status"] in {"completed", "partial_failure"}
    assert result.get("results")
    assert "V2 cycle started" in caplog.text
    # The contract is that the scheduler forwards the operator profile verbatim,
    # not that any specific value is frozen (risk_per_trade was retuned
    # 0.05 -> 0.10 by operator decision on 2026-08-07).
    from shared.models.risk import PAPER_RUNTIME_LIMITS

    assert captured_requests[0].risk_per_trade == Decimal(str(PAPER_RUNTIME_LIMITS["risk_per_trade"]))
    assert captured_requests[0].max_leverage == int(PAPER_RUNTIME_LIMITS["max_leverage"])
    # Sizing must receive a usable AI-review budget so provider latency cannot be
    # charged against the entry's price-drift ceiling.
    assert captured_requests[0].ai_review_budget_seconds > 0

    session: Session = v2_db()
    try:
        cycles = list(session.scalars(select(V2ExecutionCycle)))
        decisions = list(session.scalars(select(V2ExecutionDecision)))
        assert len(cycles) >= 1
        assert len(decisions) >= 1
        assert decisions[0].cycle_id == cycles[0].cycle_id
    finally:
        session.close()


def test_v2_scheduler_uses_operator_profile_after_next_cycle_activation(v2_db, monkeypatch) -> None:
    """S-201/S-202/S-203: API-owned profile becomes V2's next-cycle input."""
    session: Session = v2_db()
    try:
        paper_repo = PaperRunRepository(session)
        config_repo = ConfigSnapshotRepository(session)
        strategy = StrategyRepository(session).create_strategy(
            StrategyCreate(
                strategy_key="v2-directional-profile",
                source="test",
                core_thesis="test V2 operator profile consumption",
                rules=StrategyRules(),
            )
        )
        initial_profile = {
            "strategy_lane": "directional",
            "risk_per_trade": 0.05,
            "max_leverage": 40,
            "simulation_sampling_fallback_enabled": True,
        }
        run = paper_repo.create_paper_run(
            PaperRun(
                strategy_id=strategy.strategy_id,
                paper_status="running",
                execution_profile=initial_profile,
            )
        )
        assert run.paper_run_id is not None
        initial = config_repo.create_snapshot(
            ConfigSnapshot.create(
                paper_run_id=run.paper_run_id,
                config={"execution_profile": initial_profile},
                created_by="test-bootstrap",
                effective_cycle_id="INITIAL",
            ),
            base_config_hash=None,
        )
        operator_profile = {
            **initial_profile,
            "risk_per_trade": 0.012,
            "max_leverage": 7,
            "order_notional_usdt": 123,
            "max_symbol_exposure": 0.11,
            "simulation_sampling_fallback_enabled": False,
        }
        paper_repo.update_paper_run(run.paper_run_id, execution_profile=operator_profile)
        config_repo.create_snapshot(
            ConfigSnapshot.create(
                paper_run_id=run.paper_run_id,
                config={"execution_profile": operator_profile},
                created_by="test-operator-save",
                effective_cycle_id="NEXT_CYCLE",
                previous_snapshot_id=initial.config_snapshot_id,
            ),
            base_config_hash=initial.config_hash,
        )
    finally:
        session.close()

    monkeypatch.setattr(
        "services.execution.v2_scheduler_entry.resolve_engine_activation",
        lambda _settings: EngineActivationConfig(
            v2_activation=EngineActivation.ACTIVE,
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            allow_legacy_writer=False,
            warnings=[],
        ),
    )
    captured_requests = []
    monkeypatch.setattr(
        "services.execution.v2_scheduler_entry.run_automated_trading_cycle",
        lambda request, _adapter: (
            captured_requests.append(request)
            or SimpleNamespace(
                funnel_payload={"terminal_stage": "SAMPLING_DISABLED", "reason_code": "SAMPLING_FALLBACK_DISABLED"},
                reconciliation_status=SimpleNamespace(value="HEALTHY"),
                entry_submitted=False,
                errors=[],
            )
        ),
    )

    execute_v2_automated_trading_cycles(
        {"symbols": ["BTC/USDT"], "interval_seconds": 60, "scheduler_instance_id": "operator-profile"},
        adapter_factory=_fake_adapter,
        timeframe_loader=lambda _symbol, _tf: _bars(),
    )

    assert captured_requests
    request = captured_requests[0]
    assert request.risk_per_trade == Decimal("0.012")
    assert request.max_leverage == 7
    assert request.order_notional_usdt == Decimal("123")
    assert request.max_position_fraction == Decimal("0.11")
    assert request.sampling_fallback_enabled is False

    session = v2_db()
    try:
        active = ConfigSnapshotRepository(session).get_active(run.paper_run_id)
        assert active is not None
        assert active.config["execution_profile"] == operator_profile
        assert ConfigSnapshotRepository(session).get_pending(run.paper_run_id) is None
    finally:
        session.close()


def test_v2_shadow_persists_research_payload_without_execution_facts(v2_db, monkeypatch) -> None:
    monkeypatch.setattr(
        "services.execution.v2_scheduler_entry.resolve_engine_activation",
        lambda _settings: EngineActivationConfig(
            v2_activation=EngineActivation.SHADOW,
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            allow_legacy_writer=True,
            warnings=[],
        ),
    )
    monkeypatch.setattr(
        "services.execution.v2_scheduler_entry._research_shadow_payload",
        lambda _context: {"pipeline_version": "proposal-pipeline-v1", "context_hash": "a" * 64},
    )
    monkeypatch.setattr(
        "services.execution.v2_scheduler_entry.run_automated_trading_cycle",
        lambda request, _adapter: SimpleNamespace(
            funnel_payload={
                "terminal_stage": "NO_CANDIDATE",
                "reason_code": "technical_signals_insufficient",
                "candidate_key": None,
            },
            reconciliation_status=SimpleNamespace(value="HEALTHY"),
            entry_submitted=False,
            errors=[],
        ),
    )

    result = execute_v2_automated_trading_cycles(
        {"symbols": ["BTC/USDT"], "interval_seconds": 60, "scheduler_instance_id": "shadow-research"},
        adapter_factory=_fake_adapter,
        timeframe_loader=lambda _symbol, _tf: _bars(),
        market_context_loader=lambda _symbol, _time: MagicMock(),
    )

    assert result["status"] == "completed"
    session: Session = v2_db()
    try:
        decision = session.scalar(select(V2ExecutionDecision))
        assert decision is not None
        assert decision.payload["research_shadow"]["context_hash"] == "a" * 64
        assert session.scalar(select(V2ExecutionIntent)) is None
        assert session.scalar(select(V2ExchangeOrder)) is None
        assert session.scalar(select(V2ManagedPosition)) is None
    finally:
        session.close()


def test_scheduler_passes_previously_evaluated_closed_bar_to_cycle(v2_db, monkeypatch) -> None:
    """A 5-minute scheduler tick must not re-evaluate the same closed 15m candle."""
    prior_bar = _bars().last_closed
    assert prior_bar is not None
    session: Session = v2_db()
    try:
        session.add(
            V2ExecutionCycle(
                cycle_id="prior-cycle",
                symbol="BTC/USDT",
                timeframe="15m",
                bar_timestamp=prior_bar.timestamp,
                execution_mode=V2ExecutionMode.BINANCE_TESTNET.value,
                fencing_token="prior-fence",
                completed_at=datetime.now(UTC),
                decision_terminal="ENTRY_SIGNAL_EVALUATED",
            )
        )
        session.commit()
    finally:
        session.close()

    monkeypatch.setattr(
        "services.execution.v2_scheduler_entry.resolve_engine_activation",
        lambda _settings: EngineActivationConfig(
            v2_activation=EngineActivation.ACTIVE,
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            allow_legacy_writer=False,
            warnings=[],
        ),
    )
    captured_requests = []

    def fake_cycle(request, _adapter):
        captured_requests.append(request)
        return SimpleNamespace(
            funnel_payload={
                "terminal_stage": "CANDLE_CLOSED",
                "reason_code": "DUPLICATE_DECISION",
                "candidate_key": None,
            },
            reconciliation_status=SimpleNamespace(value="HEALTHY"),
            entry_submitted=False,
            errors=[],
        )

    monkeypatch.setattr(
        "services.execution.v2_scheduler_entry.run_automated_trading_cycle",
        fake_cycle,
    )

    execute_v2_automated_trading_cycles(
        {
            "symbols": ["BTC/USDT"],
            "interval_seconds": 60,
            "scheduler_instance_id": "duplicate-bar-test-instance",
        },
        adapter_factory=_fake_adapter,
        timeframe_loader=lambda _symbol, _tf: _bars(),
    )

    assert captured_requests
    assert prior_bar.timestamp in captured_requests[0].already_evaluated_bars


def test_scheduler_global_writer_lease_blocks_overlapping_slots(v2_db, monkeypatch) -> None:
    monkeypatch.setattr(
        "services.execution.v2_scheduler_entry.resolve_engine_activation",
        lambda _settings: EngineActivationConfig(
            v2_activation=EngineActivation.ACTIVE,
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            allow_legacy_writer=False,
            warnings=[],
        ),
    )
    holder = SchedulerCoordinator(
        session_factory=v2_db,
        instance_id="already-running-v2-cycle",
    )
    writer_lease_name = "automated_trading_v2:BINANCE_TESTNET:single_writer"
    assert holder.acquire_or_renew_lease(
        lease_name=writer_lease_name,
        now=datetime.now(UTC),
        ttl_seconds=900,
    )

    try:
        result = execute_v2_automated_trading_cycles(
            {
                "symbols": ["BTC/USDT"],
                "interval_seconds": 60,
                "scheduler_instance_id": "overlap-attempt",
            },
            adapter_factory=_fake_adapter,
            timeframe_loader=lambda _symbol, _tf: _bars(),
        )
    finally:
        holder.release_lease(lease_name=writer_lease_name)

    assert result["status"] == "active_cycle_running"
    assert result["writer_lease_name"] == writer_lease_name
