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
from services.automated_trading.application.production_strategy import EntryAuthority
from services.automated_trading.domain.candidates import CandidateLane
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
from services.execution import v2_scheduler_entry as v2_scheduler_entry_mod
from services.execution.scheduler import SchedulerCoordinator
from services.execution.v2_scheduler_entry import (
    _load_already_evaluated_bars,
    _load_v2_entry_timeframe,
    _load_v2_market_context,
    execute_v2_automated_trading_cycles,
    resolve_scheduler_v2_jobs,
)
from services.strategy_library import ConfigSnapshotRepository, PaperRunRepository, StrategyRepository
from services.strategy_library import proposal_pipeline as proposal_pipeline_module
from services.strategy_library.context import TIMEFRAME_DELTAS, MarketContextBuilder
from services.strategy_library.proposal_pipeline import PROPOSAL_CONTEXT_WINDOW_LENGTHS
from shared.models import ConfigSnapshot, Exchange, OHLCVBar, PaperRun, StrategyCreate, StrategyRules, Timeframe


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


def _research_context(bar_close: datetime):
    timeframe_minutes = {
        "1m": (Timeframe.M1, 1),
        "5m": (Timeframe.M5, 5),
        "15m": (Timeframe.M15, 15),
        "1h": (Timeframe.H1, 60),
        "4h": (Timeframe.H4, 240),
    }
    bars_by_timeframe = {}
    for timeframe, (timeframe_enum, minutes) in timeframe_minutes.items():
        count = PROPOSAL_CONTEXT_WINDOW_LENGTHS[timeframe]
        bars_by_timeframe[timeframe] = [
            OHLCVBar(
                symbol="BTC/USDT",
                exchange=Exchange.BINANCE,
                timeframe=timeframe_enum,
                time=bar_close - timedelta(minutes=minutes * (count - index)),
                open=Decimal("65000"),
                high=Decimal("65100"),
                low=Decimal("64900"),
                close=Decimal("65050"),
                volume=Decimal("100"),
            )
            for index in range(count)
        ]
    return MarketContextBuilder().build(
        symbol="BTC/USDT",
        decision_time=bar_close,
        bars_by_timeframe=bars_by_timeframe,
        source_ids=("runtime_data_repository",),
    )


def _active_config() -> EngineActivationConfig:
    return EngineActivationConfig(
        v2_activation=EngineActivation.ACTIVE,
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        allow_legacy_writer=False,
        warnings=[],
    )


def _position_already_open_result(*, cycle_id: str = "set-by-cycle") -> SimpleNamespace:
    return SimpleNamespace(
        funnel_payload={
            "cycle_id": cycle_id,
            "symbol": "BTC/USDT",
            "lane": "TESTNET_SAMPLING",
            "strategy_id": "testnet_sampling_v2",
            "strategy_version": "1.0.0",
            "terminal_stage": "RISK_APPROVED",
            "terminal_outcome": "REJECTED",
            "reason_code": "POSITION_ALREADY_OPEN",
            "candidate_id": "active-candidate-1",
        },
        reconciliation_status=SimpleNamespace(value="HEALTHY"),
        entry_submitted=False,
        errors=[],
    )


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


def test_v2_market_context_loader_reuses_active_15m_window(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeDataRepository:
        def __init__(self, _session) -> None:
            pass

        def list_ohlcv_bars(self, **kwargs):  # noqa: ANN003, ANN201
            calls.append(kwargs)
            return []

        def list_market_extras(self, **_kwargs):  # noqa: ANN003, ANN201
            return []

    active_timeframe = _bars()
    bar = active_timeframe.last_closed
    assert bar is not None
    monkeypatch.setattr("services.execution.v2_scheduler_entry.get_session_factory", lambda: lambda: nullcontext())
    monkeypatch.setattr("services.data.repository.DataRepository", FakeDataRepository)

    context = _load_v2_market_context(
        "BTC/USDT",
        bar.timestamp,
        active_entry_timeframe=active_timeframe,
    )

    assert "15m" not in {str(call["timeframe"]) for call in calls}
    assert context.bars_15m.last_closed_at == bar.timestamp


def _timeframe_of(bar: BarView) -> TimeframeView:
    return TimeframeView(timeframe="15m", bars=(bar, bar))


def _replaced_bar(bar: BarView, **overrides) -> BarView:  # noqa: ANN003
    fields = {
        "timestamp": bar.timestamp,
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
    }
    fields.update(overrides)
    return BarView(**fields)


def test_research_shadow_rejects_bar_and_symbol_misalignment() -> None:
    """Same-bar alignment is hash-enforced: any drift must raise, never silently observe."""
    active_timeframe = _bars()
    bar = active_timeframe.last_closed
    assert bar is not None

    def _observe(**overrides):  # noqa: ANN003, ANN202
        kwargs = {
            "active_entry_timeframe": active_timeframe,
            "scheduler_session_id": "misalignment-session",
            "scheduler_cycle_id": "scheduler-cycle-misalignment",
            "cycle_id": "cycle-misalignment",
            "symbol": "BTC/USDT",
            "bar_close_time": bar.timestamp,
            "active_strategy_id": "testnet_sampling_v2",
            "active_decision": "CANDLE_CLOSED",
            "active_terminal_reason": "DUPLICATE_DECISION",
            "created_at": bar.timestamp,
        }
        kwargs.update(overrides)
        return v2_scheduler_entry_mod._same_cycle_research_shadow_payload(_research_context(bar.timestamp), **kwargs)

    with pytest.raises(ValueError, match="does not match ACTIVE symbol"):
        _observe(symbol="ETH/USDT")

    drifted = _replaced_bar(bar, timestamp=bar.timestamp + timedelta(minutes=15))
    with pytest.raises(ValueError, match="timestamps are not aligned"):
        _observe(active_entry_timeframe=_timeframe_of(drifted))

    repriced = _replaced_bar(bar, close=bar.close + Decimal("1"))
    with pytest.raises(ValueError, match="OHLCV values are not aligned"):
        _observe(active_entry_timeframe=_timeframe_of(repriced))


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


def test_generic_scheduler_payload_cannot_arm_testnet_canary(v2_db, monkeypatch) -> None:
    """Only the dedicated acceptance capability may create a Canary writer."""
    monkeypatch.setattr(
        "services.execution.v2_scheduler_entry.resolve_engine_activation",
        lambda _settings: EngineActivationConfig(
            v2_activation=EngineActivation.ACTIVE,
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            allow_legacy_writer=False,
            warnings=[],
        ),
    )
    monkeypatch.setattr(
        "services.execution.v2_scheduler_entry._managed_symbols_requiring_recovery",
        lambda **_kwargs: (),
    )
    captured_requests = []

    def fake_cycle(request, _adapter):
        captured_requests.append(request)
        return SimpleNamespace(
            funnel_payload={"terminal_stage": "NO_CANDIDATE", "reason_code": "test"},
            reconciliation_status=SimpleNamespace(value="HEALTHY"),
            entry_submitted=False,
            errors=[],
        )

    monkeypatch.setattr("services.execution.v2_scheduler_entry.run_automated_trading_cycle", fake_cycle)

    execute_v2_automated_trading_cycles(
        {
            "symbols": ["BTC/USDT"],
            "canary_acceptance": True,
            "interval_seconds": 60,
            "scheduler_instance_id": "forged-canary-payload",
        },
        adapter_factory=_fake_adapter,
        timeframe_loader=lambda _symbol, _tf: _bars(),
    )

    assert captured_requests
    assert captured_requests[0].entry_authority is EntryAuthority.NONE


def test_historical_managed_research_symbol_reaches_recovery_cycle_without_ohlcv(v2_db, monkeypatch) -> None:
    """A scope reduction cannot strand existing XRP/SOL/BNB managed exposure."""
    now = datetime.now(UTC)
    session: Session = v2_db()
    try:
        session.add(
            V2ExecutionCycle(
                cycle_id="legacy-xrp-cycle",
                symbol="XRP/USDT",
                timeframe="15m",
                bar_timestamp=now,
                execution_mode="BINANCE_TESTNET",
                fencing_token="legacy-xrp-fence",
            )
        )
        session.add(
            V2ExecutionDecision(
                decision_id="legacy-xrp-decision",
                cycle_id="legacy-xrp-cycle",
                candidate_key="testnet_sampling_v2",
                payload={},
            )
        )
        session.flush()
        session.add(
            V2ExecutionIntent(
                intent_id="legacy-xrp-intent",
                cycle_id="legacy-xrp-cycle",
                decision_id="legacy-xrp-decision",
                symbol="XRP/USDT",
                direction="short",
                candidate_key="testnet_sampling_v2",
                candidate_type="SAMPLING",
                execution_mode="BINANCE_TESTNET",
                decision_bar_timestamp=now,
                state="FILLED",
            )
        )
        session.flush()
        session.add(
            V2ExchangeOrder(
                order_record_id="legacy-xrp-order",
                intent_id="legacy-xrp-intent",
                client_order_id="legacy-xrp-client",
                exchange_order_id="legacy-xrp-exchange-order",
                quantity=Decimal("10"),
                leverage=1,
                average_fill_price=Decimal("1"),
                filled_quantity=Decimal("10"),
                trade_ids={"trade_ids": ["legacy-xrp-trade"]},
                created_at=now,
            )
        )
        session.flush()
        session.add(
            V2ManagedPosition(
                position_id="legacy-xrp-position",
                intent_id="legacy-xrp-intent",
                order_record_id="legacy-xrp-order",
                symbol="XRP/USDT",
                direction="short",
                execution_mode="BINANCE_TESTNET",
                quantity=Decimal("10"),
                entry_price=Decimal("1"),
                entry_fee=Decimal("0"),
                state="PROTECTED",
                projected_at=now,
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
            funnel_payload={"terminal_stage": "NO_CANDIDATE", "reason_code": "test"},
            reconciliation_status=SimpleNamespace(value="HEALTHY"),
            entry_submitted=False,
            errors=[],
        )

    monkeypatch.setattr("services.execution.v2_scheduler_entry.run_automated_trading_cycle", fake_cycle)

    def unavailable_only_for_legacy_symbol(symbol: str, _timeframe: str) -> TimeframeView:
        if symbol == "XRP/USDT":
            raise RuntimeError("retired research feed")
        return _bars()

    result = execute_v2_automated_trading_cycles(
        {"symbols": ["BTC/USDT"], "interval_seconds": 60, "scheduler_instance_id": "legacy-xrp-recovery"},
        adapter_factory=_fake_adapter,
        timeframe_loader=unavailable_only_for_legacy_symbol,
    )

    assert result["status"] in {"completed", "partial_failure"}
    managed = next(request for request in captured_requests if request.symbol == "XRP/USDT")
    assert managed.entry_authority is EntryAuthority.NONE
    assert managed.entry_timeframe.bars == ()


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
            "max_margin_fraction": 0.05,
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
            "max_margin_fraction": 0.03,
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
    assert request.max_margin_fraction == Decimal("0.03")
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
    shadow_bar = _bars().last_closed
    assert shadow_bar is not None
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
        market_context_loader=lambda _symbol, _time: _research_context(shadow_bar.timestamp),
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


def test_v2_active_persists_same_cycle_research_shadow_when_position_already_open(v2_db, monkeypatch) -> None:
    bar_close = _bars().last_closed
    assert bar_close is not None
    captured_active_results: list[SimpleNamespace] = []

    def fake_cycle(request, _adapter):  # noqa: ANN001, ANN202
        active_result = _position_already_open_result(cycle_id=request.cycle_id)
        captured_active_results.append(active_result)
        return active_result

    monkeypatch.setattr(
        "services.execution.v2_scheduler_entry.resolve_engine_activation",
        lambda _settings: _active_config(),
    )
    monkeypatch.setattr(
        "services.execution.v2_scheduler_entry.run_automated_trading_cycle",
        fake_cycle,
    )

    result = execute_v2_automated_trading_cycles(
        {
            "symbols": ["BTC/USDT"],
            "interval_seconds": 60,
            "scheduler_instance_id": "p1-active-session",
        },
        adapter_factory=_fake_adapter,
        timeframe_loader=lambda _symbol, _tf: _bars(),
        market_context_loader=lambda _symbol, _time: _research_context(bar_close.timestamp),
    )

    assert result["status"] == "completed"
    cycle_id = result["results"][0]["cycle_id"]
    decision_id = result["results"][0]["decision_id"]
    active_result = captured_active_results[0]
    assert active_result.funnel_payload == _position_already_open_result(cycle_id=cycle_id).funnel_payload
    session: Session = v2_db()
    try:
        cycle = session.get(V2ExecutionCycle, cycle_id)
        decision = session.get(V2ExecutionDecision, decision_id)
        assert cycle is not None
        assert decision is not None
        payload = decision.payload
        shadow = payload["research_shadow"]
        assert payload["active_strategy_id"] == "testnet_sampling_v2"
        assert payload["active_decision"] == "RISK_APPROVED"
        assert payload["active_terminal_reason"] == "POSITION_ALREADY_OPEN"
        assert payload["market_snapshot_reference"] == shadow["market_snapshot_reference"]
        assert shadow["scheduler_session_id"] == "p1-active-session"
        assert shadow["scheduler_cycle_id"] == result["scheduler_cycle_id"]
        assert shadow["cycle_id"] == cycle_id
        assert shadow["symbol"] == "BTC/USDT"
        assert shadow["bar_close_time"] == bar_close.timestamp.isoformat()
        assert len(shadow["observations"]) == 5
        assert {item["strategy_id"] for item in shadow["observations"]} == {
            "loss_aware_trend_pullback_v1",
            "trend_pullback_v2",
            "range_sweep_reversion_v1",
            "failed_breakout_reversal_v1",
            "breakout_continuation_v1",
        }
        for observation in shadow["observations"]:
            assert observation["scheduler_session_id"] == "p1-active-session"
            assert observation["cycle_id"] == cycle_id
            assert observation["symbol"] == "BTC/USDT"
            assert observation["bar_close_time"] == bar_close.timestamp.isoformat()
            assert observation["market_snapshot_reference"] == payload["market_snapshot_reference"]
            assert observation["lane"] == "RESEARCH_SHADOW"
    finally:
        session.close()


def test_research_shadow_runs_after_all_active_symbols(v2_db, monkeypatch) -> None:
    events: list[str] = []

    def fake_cycle(request, _adapter):  # noqa: ANN001, ANN202
        events.append(f"active:{request.symbol}")
        return _position_already_open_result(cycle_id=request.cycle_id)

    def context_loader(symbol, _time):  # noqa: ANN001, ANN202
        events.append(f"shadow:{symbol}")
        return _research_context(_bars().last_closed.timestamp)

    monkeypatch.setattr(
        "services.execution.v2_scheduler_entry.resolve_engine_activation",
        lambda _settings: _active_config(),
    )
    monkeypatch.setattr("services.execution.v2_scheduler_entry.run_automated_trading_cycle", fake_cycle)
    monkeypatch.setattr(
        "services.execution.v2_scheduler_entry._same_cycle_research_shadow_payload",
        lambda _context, **_kwargs: {"market_snapshot_reference": "test-reference", "observations": []},
    )

    result = execute_v2_automated_trading_cycles(
        {
            "symbols": ["BTC/USDT", "ETH/USDT"],
            "interval_seconds": 60,
            "scheduler_instance_id": "p1-order-session",
        },
        adapter_factory=_fake_adapter,
        timeframe_loader=lambda _symbol, _tf: _bars(),
        market_context_loader=context_loader,
    )

    assert result["status"] == "completed"
    assert events == ["active:BTC/USDT", "active:ETH/USDT", "shadow:BTC/USDT", "shadow:ETH/USDT"]


def test_research_shadow_observer_has_zero_execution_authority(monkeypatch) -> None:
    bar = _bars().last_closed
    assert bar is not None
    raw_shadow_payload = {
        "pipeline_version": "proposal-pipeline-v1",
        "context_hash": "a" * 64,
        "strategy_versions": {
            "trend_pullback_v2": "2.0.0-research",
            "range_sweep_reversion_v1": "1.0.0-research",
            "failed_breakout_reversal_v1": "1.0.0-research",
        },
        "proposals": [
            {
                "proposal_id": "trend-pullback-signal",
                "strategy_id": "trend_pullback_v2",
                "strategy_version": "2.0.0-research",
                "symbol": "BTC/USDT",
                "side": "long",
                "entry_trigger": {"reference_price": "100"},
                "invalidation": {"stop_price": "98"},
                "targets": [{"price": "102"}, {"price": "104"}],
            }
        ],
        "selection": {
            "status": "SELECTED",
            "selected": {"proposal_id": "trend-pullback-signal"},
            "supporting_proposal_ids": ["trend-pullback-signal"],
            "rejected_reasons": {},
        },
        "rejection_reasons": {
            "range_sweep_reversion_v1": "candidate_conditions_not_met",
            "failed_breakout_reversal_v1": "candidate_conditions_not_met",
        },
        "evaluation_errors": {},
    }
    monkeypatch.setattr(v2_scheduler_entry_mod, "_research_shadow_payload", lambda _context: raw_shadow_payload)

    intent_builder = MagicMock()
    entry_executor = MagicMock()
    position_writer = MagicMock()
    protection_writer = MagicMock()
    exchange_adapter = MagicMock()
    monkeypatch.setattr(
        "services.automated_trading.application.fact_persistence.persist_entry_intent_before_submission",
        intent_builder,
    )
    monkeypatch.setattr("services.automated_trading.application.entry_service.execute_entry", entry_executor)
    monkeypatch.setattr(
        "services.automated_trading.application.fact_persistence.persist_entry_and_protection",
        position_writer,
    )
    monkeypatch.setattr(
        "services.automated_trading.application.protection_service.ensure_protection",
        protection_writer,
    )

    shadow = v2_scheduler_entry_mod._same_cycle_research_shadow_payload(
        _research_context(bar.timestamp),
        active_entry_timeframe=_bars(),
        scheduler_session_id="zero-submit-session",
        scheduler_cycle_id="scheduler-cycle-zero-submit",
        cycle_id="cycle-zero-submit",
        symbol="BTC/USDT",
        bar_close_time=bar.timestamp,
        active_strategy_id="testnet_sampling_v2",
        active_decision="CANDIDATE_CREATED",
        active_terminal_reason="CANDIDATE_READY",
        created_at=bar.timestamp,
    )

    signal = next(item for item in shadow["observations"] if item["strategy_id"] == "trend_pullback_v2")
    assert signal["decision_status"] == "SHADOW_SIGNAL_READY"
    assert signal["signal_present"] is True
    assert signal["signal_direction"] == "long"
    intent_builder.assert_not_called()
    entry_executor.assert_not_called()
    exchange_adapter.submit_market_order.assert_not_called()
    position_writer.assert_not_called()
    protection_writer.assert_not_called()


def test_research_shadow_failure_does_not_change_active_result(v2_db, monkeypatch) -> None:
    bar = _bars().last_closed
    assert bar is not None
    captured_active_results: list[SimpleNamespace] = []

    def fake_cycle(request, _adapter):  # noqa: ANN001, ANN202
        active_result = _position_already_open_result(cycle_id=request.cycle_id)
        captured_active_results.append(active_result)
        return active_result

    def fails(_context, _regime):  # noqa: ANN001, ANN202
        raise LookupError("one research strategy failed")

    def no_signal(_context, _regime):  # noqa: ANN001, ANN202
        return None

    monkeypatch.setattr(
        proposal_pipeline_module,
        "_evaluators",
        lambda: (
            ("trend_pullback_v2", fails),
            ("range_sweep_reversion_v1", no_signal),
            ("failed_breakout_reversal_v1", no_signal),
        ),
    )
    monkeypatch.setattr(
        "services.execution.v2_scheduler_entry.resolve_engine_activation",
        lambda _settings: _active_config(),
    )
    monkeypatch.setattr(
        "services.execution.v2_scheduler_entry.run_automated_trading_cycle",
        fake_cycle,
    )

    result = execute_v2_automated_trading_cycles(
        {
            "symbols": ["BTC/USDT"],
            "interval_seconds": 60,
            "scheduler_instance_id": "p1-error-session",
        },
        adapter_factory=_fake_adapter,
        timeframe_loader=lambda _symbol, _tf: _bars(),
        market_context_loader=lambda _symbol, _time: _research_context(bar.timestamp),
    )

    assert result["status"] == "completed"
    assert result["results"][0]["reconciliation_status"] == "HEALTHY"
    cycle_id = result["results"][0]["cycle_id"]
    decision_id = result["results"][0]["decision_id"]
    active_result = captured_active_results[0]
    assert active_result.funnel_payload == _position_already_open_result(cycle_id=cycle_id).funnel_payload
    session: Session = v2_db()
    try:
        decision = session.get(V2ExecutionDecision, decision_id)
        assert decision is not None
        payload = decision.payload
        assert payload["active_decision"] == "RISK_APPROVED"
        assert payload["active_terminal_reason"] == "POSITION_ALREADY_OPEN"
        error = next(
            item for item in payload["research_shadow"]["observations"] if item["strategy_id"] == "trend_pullback_v2"
        )
        assert error["decision_status"] == "SHADOW_STRATEGY_ERROR"
        assert error["terminal_reason"] == "SHADOW_STRATEGY_ERROR"
        assert error["error_class"] == "LookupError"
        assert error["safe_error_message"] == "one research strategy failed"
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
        session.add(
            V2ExecutionDecision(
                decision_id="prior-decision",
                cycle_id="prior-cycle",
                terminal_reason="ENTRY_SIGNAL_EVALUATED",
                payload={"lane": CandidateLane.PRODUCTION.value},
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


def test_scheduler_duplicate_history_is_scoped_to_candidate_lane(v2_db) -> None:
    prior_bar = _bars().last_closed
    assert prior_bar is not None
    session: Session = v2_db()
    try:
        session.add(
            V2ExecutionCycle(
                cycle_id="lane-prior-cycle",
                symbol="BTC/USDT",
                timeframe="15m",
                bar_timestamp=prior_bar.timestamp,
                execution_mode=V2ExecutionMode.BINANCE_TESTNET.value,
                fencing_token="lane-prior-fence",
                completed_at=datetime.now(UTC),
                decision_terminal="RISK_APPROVED",
            )
        )
        session.add(
            V2ExecutionDecision(
                decision_id="lane-prior-decision",
                cycle_id="lane-prior-cycle",
                terminal_reason="NO_AUTHORIZED_PRODUCTION_STRATEGY",
                payload={"lane": CandidateLane.PRODUCTION.value},
            )
        )
        sampling_bar = prior_bar.timestamp + timedelta(minutes=15)
        session.add(
            V2ExecutionCycle(
                cycle_id="sampling-prior-cycle",
                symbol="BTC/USDT",
                timeframe="15m",
                bar_timestamp=sampling_bar,
                execution_mode=V2ExecutionMode.BINANCE_TESTNET.value,
                fencing_token="sampling-prior-fence",
                completed_at=datetime.now(UTC),
                decision_terminal="ENTRY_SIGNAL_EVALUATED",
            )
        )
        session.add(
            V2ExecutionDecision(
                decision_id="sampling-prior-decision",
                cycle_id="sampling-prior-cycle",
                terminal_reason="ENTRY_SIGNAL_EVALUATED",
                payload={"lane": CandidateLane.TESTNET_SAMPLING.value},
            )
        )
        session.commit()
    finally:
        session.close()

    production_bars = _load_already_evaluated_bars(
        symbol="BTC/USDT",
        timeframe="15m",
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        lane=CandidateLane.PRODUCTION,
    )
    sampling_bars = _load_already_evaluated_bars(
        symbol="BTC/USDT",
        timeframe="15m",
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        lane=CandidateLane.TESTNET_SAMPLING,
    )

    assert prior_bar.timestamp in production_bars
    assert prior_bar.timestamp not in sampling_bars
    assert sampling_bar in sampling_bars


def test_scheduler_global_writer_lease_blocks_overlapping_slots(v2_db, monkeypatch) -> None:
    # Direct entry calls without RuntimeScheduler provenance still resolve a
    # Testnet clock. Keep this unit test on the same reference as its holder.
    monkeypatch.setattr(
        "services.execution.v2_scheduler_entry.fetch_binance_server_time",
        lambda: datetime.now(UTC),
    )
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
