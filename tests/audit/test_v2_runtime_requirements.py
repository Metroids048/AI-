"""Independent V2 runtime requirements audit.

Assertions are Gate acceptance criteria. Gate 1 subset must pass after Gate 1
implementation; Gate 2–5 assertions remain failing until those Gates land.
Do not weaken assertions or mock Repository/DB boundaries.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from services.automated_trading.domain.enums import (
    V2CandidateType,
    V2ExecutionMode,
    V2IntentState,
)
from services.automated_trading.infrastructure.models import (
    Base,
    V2ExchangeFill,
)
from services.automated_trading.infrastructure.repository import AutomatedTradingRepository

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def audit_db() -> Session:
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _connection_record):  # type: ignore[no-untyped-def]
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("DROP INDEX IF EXISTS ix_v2_position_one_open_per_symbol_direction_mode"))
        conn.execute(
            text(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS ix_v2_position_one_open_per_symbol_direction_mode
                ON v2_managed_positions (symbol, direction, execution_mode)
                WHERE state NOT IN ('CLOSED', 'QUARANTINED')
                """
            )
        )
    session = Session(engine)
    yield session
    session.close()


@pytest.fixture
def repo(audit_db: Session) -> AutomatedTradingRepository:
    return AutomatedTradingRepository(audit_db)


# ---------------------------------------------------------------------------
# Gate 1 — database facts and state machine
# ---------------------------------------------------------------------------


class TestGate1DatabaseIntegrity:
    def test_fill_table_and_trade_id_unique(self, repo: AutomatedTradingRepository, audit_db: Session) -> None:
        assert inspect(audit_db.get_bind()).has_table("v2_exchange_fills")
        repo.create_cycle(
            cycle_id="c1",
            symbol="BTC/USDT",
            timeframe="15m",
            bar_timestamp=datetime(2026, 7, 28, 10, 0, tzinfo=UTC),
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            fencing_token="f1",
        )
        repo.create_intent(
            intent_id="i1",
            cycle_id="c1",
            symbol="BTC/USDT",
            direction="long",
            candidate_key="primary",
            candidate_type=V2CandidateType.PRIMARY,
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            decision_bar_timestamp=datetime(2026, 7, 28, 10, 0, tzinfo=UTC),
            decision_funnel_id=None,
            state=V2IntentState.INTENT_CREATED,
        )
        oid = repo.save_order_submission(
            intent_id="i1",
            client_order_id="co1",
            quantity=0.001,
            leverage=20,
            submitted_at=datetime(2026, 7, 28, 10, 0, tzinfo=UTC),
        )
        repo.save_exchange_order_receipt(
            order_record_id=oid,
            exchange_order_id="xo1",
            acknowledged_at=datetime(2026, 7, 28, 10, 0, 30, tzinfo=UTC),
        )
        kwargs = {
            "intent_id": "i1",
            "exchange_order_record_id": oid,
            "account_id": "acct",
            "exchange_order_id": "xo1",
            "trade_id": "t1",
            "symbol": "BTC/USDT",
            "side": "BUY",
            "reduce_only": False,
            "filled_quantity": Decimal("0.001"),
            "fill_price": Decimal("65000"),
            "commission": Decimal("0.1"),
            "commission_asset": "USDT",
            "exchange_event_time": datetime(2026, 7, 28, 10, 1, tzinfo=UTC),
            "received_at": datetime(2026, 7, 28, 10, 1, tzinfo=UTC),
            "raw_hash": "h1",
        }
        repo.save_exchange_fill_receipt(**kwargs)
        with pytest.raises(IntegrityError):
            repo.save_exchange_fill_receipt(**{**kwargs, "raw_hash": "h2"})
            repo.commit()
        audit_db.rollback()

    def test_runtime_control_defaults_entry_disabled(self, repo: AutomatedTradingRepository) -> None:
        control = repo.get_runtime_control("global")
        assert control is not None
        assert control.entry_enabled is False

    def test_standard_runtime_control_initialization_arms_only_an_absent_row(
        self,
        repo: AutomatedTradingRepository,
    ) -> None:
        control = repo.ensure_runtime_control(
            scope="global",
            entry_enabled=True,
            reason="STANDARD_RUNTIME_ENABLED",
            updated_by="runtime-bootstrap",
        )
        repo.commit()

        assert control.entry_enabled is True
        assert control.reason == "STANDARD_RUNTIME_ENABLED"
        assert control.updated_by == "runtime-bootstrap"

    @pytest.mark.parametrize(
        "reason",
        ["ENTRY_PAUSED:operator", "LIVENESS_RECOVERY_HOLD:WORKER_EXITED:1"],
    )
    def test_standard_runtime_control_initialization_preserves_existing_pause(
        self,
        repo: AutomatedTradingRepository,
        reason: str,
    ) -> None:
        repo.set_runtime_control(
            scope="global",
            entry_enabled=False,
            reason=reason,
            updated_by="operator" if reason.startswith("ENTRY_PAUSED") else "liveness-supervisor",
        )
        repo.commit()

        control = repo.ensure_runtime_control(
            scope="global",
            entry_enabled=True,
            reason="STANDARD_RUNTIME_ENABLED",
            updated_by="runtime-bootstrap",
        )
        repo.commit()

        assert control.entry_enabled is False
        assert control.reason == reason

    def test_runtime_control_cas_rejects_stale_owner(self, repo: AutomatedTradingRepository) -> None:
        control = repo.get_runtime_control("global")
        observed_version = int(control.version)
        observed_reason = control.reason
        repo.set_runtime_control(
            scope="global",
            entry_enabled=False,
            reason="ENTRY_PAUSED:operator",
            updated_by="operator",
            expected_version=observed_version,
        )
        repo.commit()

        changed = repo.compare_and_set_runtime_control(
            scope="global",
            expected_version=observed_version,
            expected_entry_enabled=False,
            expected_reason=observed_reason,
            entry_enabled=True,
            reason="LIVENESS_RECOVERY_RECOVERED",
            updated_by="liveness-supervisor",
        )
        repo.commit()

        current = repo.get_runtime_control("global")
        assert changed is False
        assert current.entry_enabled is False
        assert current.reason == "ENTRY_PAUSED:operator"

    def test_illegal_terminal_transitions_rejected(self, repo: AutomatedTradingRepository) -> None:
        repo.create_cycle(
            cycle_id="c2",
            symbol="BTC/USDT",
            timeframe="15m",
            bar_timestamp=datetime(2026, 7, 28, 10, 0, tzinfo=UTC),
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            fencing_token="f2",
        )
        repo.create_intent(
            intent_id="i2",
            cycle_id="c2",
            symbol="BTC/USDT",
            direction="long",
            candidate_key="primary",
            candidate_type=V2CandidateType.PRIMARY,
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            decision_bar_timestamp=datetime(2026, 7, 28, 10, 0, tzinfo=UTC),
            decision_funnel_id=None,
            state=V2IntentState.FILLED,
        )
        with pytest.raises(ValueError, match="Illegal V2 intent transition"):
            repo.transition_intent(
                intent_id="i2",
                expected_current=V2IntentState.FILLED,
                next_state=V2IntentState.INTENT_CREATED,
                event_type="Illegal",
                payload={},
            )

    def test_no_public_free_state_mutators(self, repo: AutomatedTradingRepository) -> None:
        assert not hasattr(repo, "update_intent_state")
        assert not hasattr(repo, "update_position_state")

    def test_projection_aggregates_from_fill_table(self, repo: AutomatedTradingRepository, audit_db: Session) -> None:
        repo.create_cycle(
            cycle_id="c3",
            symbol="BTC/USDT",
            timeframe="15m",
            bar_timestamp=datetime(2026, 7, 28, 10, 0, tzinfo=UTC),
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            fencing_token="f3",
        )
        repo.create_intent(
            intent_id="i3",
            cycle_id="c3",
            symbol="BTC/USDT",
            direction="long",
            candidate_key="primary",
            candidate_type=V2CandidateType.PRIMARY,
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            decision_bar_timestamp=datetime(2026, 7, 28, 10, 0, tzinfo=UTC),
            decision_funnel_id=None,
            state=V2IntentState.INTENT_CREATED,
        )
        oid = repo.save_order_submission(
            intent_id="i3",
            client_order_id="co3",
            quantity=0.002,
            leverage=20,
            submitted_at=datetime(2026, 7, 28, 10, 0, tzinfo=UTC),
        )
        repo.save_exchange_order_receipt(
            order_record_id=oid,
            exchange_order_id="xo3",
            acknowledged_at=datetime(2026, 7, 28, 10, 0, 30, tzinfo=UTC),
        )
        for trade_id, qty, price in (("t3a", "0.001", "65000"), ("t3b", "0.001", "66000")):
            repo.save_exchange_fill_receipt(
                intent_id="i3",
                exchange_order_record_id=oid,
                account_id="acct",
                exchange_order_id="xo3",
                trade_id=trade_id,
                symbol="BTC/USDT",
                side="BUY",
                reduce_only=False,
                filled_quantity=Decimal(qty),
                fill_price=Decimal(price),
                commission=Decimal("0.1"),
                commission_asset="USDT",
                exchange_event_time=datetime(2026, 7, 28, 10, 1, tzinfo=UTC),
                received_at=datetime(2026, 7, 28, 10, 1, tzinfo=UTC),
                raw_hash=f"h-{trade_id}",
            )
        repo.project_position_from_confirmed_fills(
            position_id="p3",
            intent_id="i3",
            order_record_id=oid,
            symbol="BTC/USDT",
            direction="long",
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            projected_at=datetime(2026, 7, 28, 10, 2, tzinfo=UTC),
        )
        pos = repo.get_position_by_id("p3")
        assert pos is not None
        assert pos.quantity == Decimal("0.002")
        assert pos.entry_price == Decimal("65500.0")
        fills = list(audit_db.scalars(select(V2ExchangeFill).where(V2ExchangeFill.intent_id == "i3")))
        assert len(fills) == 2


# ---------------------------------------------------------------------------
# Gate 2 — scheduler / single writer (must remain red until Gate 2)
# ---------------------------------------------------------------------------


class TestGate2SchedulerWiring:
    def test_scheduler_registers_v2_task_entrypoint(self) -> None:
        from services.execution import scheduler as sched_mod

        source = Path(sched_mod.__file__).read_text(encoding="utf-8")
        assert "automated_trading" in source or "run_v2" in source.lower()
        # Formal production wiring must exist; baseline has none.
        assert "AutomatedTrading" in source or "v2_shadow" in source or "v2_active" in source

    def test_scheduler_entry_persists_facts_when_active(self) -> None:
        from services.execution import v2_scheduler_entry as entry_mod

        source = Path(entry_mod.__file__).read_text(encoding="utf-8")
        assert "persist_facts" in source
        assert "EngineActivation.ACTIVE" in source
        assert "_ensure_v2_cycle" in source
        assert "_finalize_v2_cycle_decision" in source


# ---------------------------------------------------------------------------
# Gate 3 — exit / recovery execution (must remain red until Gate 3)
# ---------------------------------------------------------------------------


class TestGate3ExitRecovery:
    def test_cycle_invokes_execute_reduce_only_exit(self) -> None:
        from services.automated_trading.application import cycle_service

        source = Path(cycle_service.__file__).read_text(encoding="utf-8")
        assert "execute_reduce_only_exit" in source
        assert "forced_exit_reason" in source
        # Must not blindly evaluate TIME_EXIT every cycle without an explicit reason.
        assert "never blind TIME_EXIT" in source


# ---------------------------------------------------------------------------
# Gate 4 — API / frontend persistence (must remain red until Gate 4)
# ---------------------------------------------------------------------------


class TestGate4RuntimeApiFrontend:
    def test_runtime_api_does_not_use_process_memory_state(self) -> None:
        api_path = REPO_ROOT / "apps" / "api" / "routers" / "automated_trading.py"
        source = api_path.read_text(encoding="utf-8")
        assert "_runtime_state" not in source


# ---------------------------------------------------------------------------
# Gate 5 — natural scheduler testnet evidence (COMPLETE requires real bundle)
# ---------------------------------------------------------------------------


class TestGate5NaturalEvidence:
    def test_natural_scheduler_evidence_or_explicit_block(self) -> None:
        import json

        evidence = REPO_ROOT / "audit" / "v2_closure" / "natural_scheduler_testnet_evidence.json"
        blocked = REPO_ROOT / "audit" / "v2_closure" / "gate5_status.json"
        if evidence.is_file():
            payload = json.loads(evidence.read_text(encoding="utf-8"))
            assert payload.get("proof_type") == "NATURAL_SCHEDULER_TESTNET"
            return
        assert blocked.is_file(), "Gate 5 requires NATURAL evidence or explicit BLOCKED status file"
        payload = json.loads(blocked.read_text(encoding="utf-8"))
        assert payload.get("status") == "BLOCKED"
        assert payload.get("proof_type") == "NATURAL_SCHEDULER_TESTNET"
