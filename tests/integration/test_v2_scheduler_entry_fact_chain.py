"""Gate 2: V2 must be driven from Scheduler/Celery task entry, not direct cycle calls.

Tests invoke the formal task module / execute_v2 bridge, then open a NEW
DB session to assert persisted Cycle + Decision facts.
"""

from __future__ import annotations

from datetime import UTC, datetime
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
    V2ExecutionCycle,
    V2ExecutionDecision,
)
from services.automated_trading.infrastructure.runtime_lock import EngineActivation, EngineActivationConfig
from services.execution import tasks as tasks_mod
from services.execution.v2_scheduler_entry import (
    execute_v2_automated_trading_cycles,
    resolve_scheduler_v2_jobs,
)


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


def test_v2_active_scheduler_persists_entry_fact_chain(v2_db, monkeypatch) -> None:
    """Formal entry must persist Cycle+Decision; verified via a fresh Session."""
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
        "services.execution.v2_scheduler_entry.run_automated_trading_cycle",
        lambda request, adapter: SimpleNamespace(
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

    session: Session = v2_db()
    try:
        cycles = list(session.scalars(select(V2ExecutionCycle)))
        decisions = list(session.scalars(select(V2ExecutionDecision)))
        assert len(cycles) >= 1
        assert len(decisions) >= 1
        assert decisions[0].cycle_id == cycles[0].cycle_id
    finally:
        session.close()
