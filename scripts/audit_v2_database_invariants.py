"""Dynamically attempt illegal V2 writes; each must be REJECTED_AS_EXPECTED."""

from __future__ import annotations

import json
import sys
import traceback
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from services.automated_trading.domain.enums import (
    V2CandidateType,
    V2ExecutionMode,
    V2IntentState,
    V2PositionState,
    V2ProtectionState,
)
from services.automated_trading.infrastructure.models import Base
from services.automated_trading.infrastructure.repository import AutomatedTradingRepository


def _engine_session() -> Session:
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
                CREATE UNIQUE INDEX ix_v2_position_one_open_per_symbol_direction_mode
                ON v2_managed_positions (symbol, direction, execution_mode)
                WHERE state NOT IN ('CLOSED', 'QUARANTINED')
                """
            )
        )
    return Session(engine)


def _seed_open_chain(repo: AutomatedTradingRepository, suffix: str) -> tuple[str, str]:
    cycle_id = f"cycle-{suffix}"
    intent_id = f"intent-{suffix}"
    repo.create_cycle(
        cycle_id=cycle_id,
        symbol="BTC/USDT",
        timeframe="15m",
        bar_timestamp=datetime(2026, 7, 28, 10, 0, tzinfo=UTC),
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        fencing_token=f"fence-{suffix}",
    )
    repo.create_intent(
        intent_id=intent_id,
        cycle_id=cycle_id,
        symbol="BTC/USDT",
        direction="long",
        candidate_key="primary",
        candidate_type=V2CandidateType.PRIMARY,
        execution_mode=V2ExecutionMode.BINANCE_TESTNET,
        decision_bar_timestamp=datetime(2026, 7, 28, 10, 0, tzinfo=UTC),
        decision_funnel_id=None,
        state=V2IntentState.INTENT_CREATED,
    )
    order_id = repo.save_order_submission(
        intent_id=intent_id,
        client_order_id=f"co-{suffix}",
        quantity=0.001,
        leverage=20,
        submitted_at=datetime(2026, 7, 28, 10, 0, tzinfo=UTC),
    )
    repo.save_exchange_order_receipt(
        order_record_id=order_id,
        exchange_order_id=f"xo-{suffix}",
        acknowledged_at=datetime(2026, 7, 28, 10, 0, 30, tzinfo=UTC),
    )
    repo.save_exchange_fill_receipt(
        intent_id=intent_id,
        exchange_order_record_id=order_id,
        account_id="binance_testnet",
        exchange_order_id=f"xo-{suffix}",
        trade_id=f"trade-{suffix}",
        symbol="BTC/USDT",
        side="BUY",
        reduce_only=False,
        filled_quantity=Decimal("0.001"),
        fill_price=Decimal("65000"),
        commission=Decimal("0.1"),
        commission_asset="USDT",
        exchange_event_time=datetime(2026, 7, 28, 10, 1, tzinfo=UTC),
        received_at=datetime(2026, 7, 28, 10, 1, tzinfo=UTC),
        raw_hash=f"hash-{suffix}",
    )
    return intent_id, order_id


def _attempt(name: str, fn) -> dict:
    try:
        fn()
        return {"name": name, "result": "UNEXPECTED_ACCEPT", "detail": "write succeeded"}
    except (ValueError, IntegrityError) as exc:
        return {"name": name, "result": "REJECTED_AS_EXPECTED", "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {
            "name": name,
            "result": "UNEXPECTED_ERROR",
            "detail": f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
        }


def main() -> int:
    results: list[dict] = []

    # 1) FILLED -> INTENT_CREATED
    session = _engine_session()
    repo = AutomatedTradingRepository(session)

    def filled_to_created() -> None:
        repo.create_cycle(
            cycle_id="c-filled",
            symbol="BTC/USDT",
            timeframe="15m",
            bar_timestamp=datetime(2026, 7, 28, 10, 0, tzinfo=UTC),
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            fencing_token="fence-filled",
        )
        repo.create_intent(
            intent_id="i-filled",
            cycle_id="c-filled",
            symbol="BTC/USDT",
            direction="long",
            candidate_key="primary",
            candidate_type=V2CandidateType.PRIMARY,
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            decision_bar_timestamp=datetime(2026, 7, 28, 10, 0, tzinfo=UTC),
            decision_funnel_id=None,
            state=V2IntentState.FILLED,
        )
        repo.transition_intent(
            intent_id="i-filled",
            expected_current=V2IntentState.FILLED,
            next_state=V2IntentState.INTENT_CREATED,
            event_type="Illegal",
            payload={},
        )

    results.append(_attempt("FILLED_TO_INTENT_CREATED", filled_to_created))
    session.close()

    # 2) CLOSED -> POSITION_PROJECTED
    session = _engine_session()
    repo = AutomatedTradingRepository(session)

    def closed_reopen() -> None:
        intent_id, order_id = _seed_open_chain(repo, "closed")
        repo.project_position_from_confirmed_fills(
            position_id="pos-closed",
            intent_id=intent_id,
            order_record_id=order_id,
            symbol="BTC/USDT",
            direction="long",
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            projected_at=datetime(2026, 7, 28, 10, 2, tzinfo=UTC),
        )
        repo.transition_position(
            position_id="pos-closed",
            expected_current=V2PositionState.POSITION_PROJECTED,
            next_state=V2PositionState.PROTECTED,
            event_type="Protected",
            payload={},
        )
        repo.transition_position(
            position_id="pos-closed",
            expected_current=V2PositionState.PROTECTED,
            next_state=V2PositionState.CLOSED,
            event_type="Closed",
            payload={},
        )
        repo.transition_position(
            position_id="pos-closed",
            expected_current=V2PositionState.CLOSED,
            next_state=V2PositionState.POSITION_PROJECTED,
            event_type="IllegalReopen",
            payload={},
        )

    results.append(_attempt("CLOSED_TO_POSITION_PROJECTED", closed_reopen))
    session.close()

    # 3) Duplicate trade ID
    session = _engine_session()
    repo = AutomatedTradingRepository(session)

    def dup_trade() -> None:
        intent_id, order_id = _seed_open_chain(repo, "dup")
        repo.save_exchange_fill_receipt(
            intent_id=intent_id,
            exchange_order_record_id=order_id,
            account_id="binance_testnet",
            exchange_order_id="xo-dup",
            trade_id="trade-dup",
            symbol="BTC/USDT",
            side="BUY",
            reduce_only=False,
            filled_quantity=Decimal("0.001"),
            fill_price=Decimal("65000"),
            commission=Decimal("0.1"),
            commission_asset="USDT",
            exchange_event_time=datetime(2026, 7, 28, 10, 1, tzinfo=UTC),
            received_at=datetime(2026, 7, 28, 10, 1, tzinfo=UTC),
            raw_hash="hash-dup-2",
        )
        session.commit()

    results.append(_attempt("DUPLICATE_TRADE_ID", dup_trade))
    session.close()

    # 4) Second open position
    session = _engine_session()
    repo = AutomatedTradingRepository(session)

    def second_open() -> None:
        intent_1, order_1 = _seed_open_chain(repo, "open1")
        repo.project_position_from_confirmed_fills(
            position_id="pos-open1",
            intent_id=intent_1,
            order_record_id=order_1,
            symbol="BTC/USDT",
            direction="long",
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            projected_at=datetime(2026, 7, 28, 10, 2, tzinfo=UTC),
        )
        intent_2, order_2 = _seed_open_chain(repo, "open2")
        repo.project_position_from_confirmed_fills(
            position_id="pos-open2",
            intent_id=intent_2,
            order_record_id=order_2,
            symbol="BTC/USDT",
            direction="long",
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            projected_at=datetime(2026, 7, 28, 10, 3, tzinfo=UTC),
        )
        session.commit()

    results.append(_attempt("SECOND_OPEN_POSITION", second_open))
    session.close()

    # 5) ACTIVE protection without exchange order id
    session = _engine_session()
    repo = AutomatedTradingRepository(session)

    def active_without_xo() -> None:
        intent_id, order_id = _seed_open_chain(repo, "prot")
        repo.project_position_from_confirmed_fills(
            position_id="pos-prot",
            intent_id=intent_id,
            order_record_id=order_id,
            symbol="BTC/USDT",
            direction="long",
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            projected_at=datetime(2026, 7, 28, 10, 2, tzinfo=UTC),
        )
        repo.save_protection(
            protection_id="prot-1",
            position_id="pos-prot",
            stop_loss_price=64000.0,
            take_profit_price=67000.0,
            stop_client_order_id="stop-audit",
            tp_client_order_id="tp-audit",
            state=V2ProtectionState.PROTECTION_INTENT,
        )
        repo.transition_protection(
            protection_id="prot-1",
            expected_current=V2ProtectionState.PROTECTION_INTENT,
            next_state=V2ProtectionState.PROTECTION_SUBMITTING,
            event_type="Submitting",
            payload={},
        )
        repo.transition_protection(
            protection_id="prot-1",
            expected_current=V2ProtectionState.PROTECTION_SUBMITTING,
            next_state=V2ProtectionState.PROTECTION_ACTIVE,
            event_type="ActiveIllegal",
            payload={},
        )

    results.append(_attempt("ACTIVE_PROTECTION_WITHOUT_EXCHANGE_ORDER_ID", active_without_xo))
    session.close()

    print(json.dumps({"attempts": results}, indent=2, ensure_ascii=False))
    all_ok = all(r["result"] == "REJECTED_AS_EXPECTED" for r in results)
    for r in results:
        print(f"{r['name']}: {r['result']}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
