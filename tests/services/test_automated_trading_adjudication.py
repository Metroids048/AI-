from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from services.automated_trading.application.adjudication import (
    AdjudicationManifest,
    BinanceAggregateExitEvidenceProvider,
    ExchangeAggregateExitEvidence,
    finalize_adjudication,
    prepare_adjudication,
)
from services.automated_trading.domain.enums import V2ExecutionMode
from services.automated_trading.infrastructure.models import (
    Base,
    V2AdjudicationCase,
    V2AdjudicationFinalization,
    V2ExchangeFill,
    V2ExchangeOrder,
    V2ExecutionCycle,
    V2ExecutionEvent,
    V2ExecutionIntent,
    V2ManagedPosition,
)


@pytest.fixture
def db(tmp_path):
    engines = []
    sessions = []
    for index in range(2):
        engine = create_engine(f"sqlite:///{tmp_path / f'adjudication-{index}.db'}")

        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _connection_record):  # type: ignore[no-untyped-def]
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(engine)
        engines.append(engine)
        sessions.append(sessionmaker(bind=engine, expire_on_commit=False)())
    for index, session in enumerate(sessions):
        cycle_id = f"cycle-{index}"
        intent_id = f"intent-{index}"
        order_record_id = f"entry-order-{index}"
        position_id = f"position-{index}"
        session.add(
            V2ExecutionCycle(
                cycle_id=cycle_id,
                symbol="ETH/USDT",
                timeframe="15m",
                bar_timestamp=datetime(2026, 9, 3, 10, 15, tzinfo=UTC),
                execution_mode=V2ExecutionMode.BINANCE_TESTNET.value,
                fencing_token=f"fence-{index}",
            )
        )
        session.add(
            V2ExecutionIntent(
                intent_id=intent_id,
                cycle_id=cycle_id,
                symbol="ETH/USDT",
                direction="short",
                candidate_key="testnet_sampling_v2",
                candidate_type="SAMPLING",
                execution_mode=V2ExecutionMode.BINANCE_TESTNET.value,
                decision_bar_timestamp=datetime(2026, 9, 3, 10, 15, tzinfo=UTC),
                state="FILLED",
            )
        )
        session.flush()
        session.add(
            V2ExchangeOrder(
                order_record_id=order_record_id,
                intent_id=intent_id,
                client_order_id=f"entry-{index}",
                exchange_order_id=f"entry-x-{index}",
                quantity=Decimal("0.02"),
                leverage=30,
                filled_quantity=Decimal("0.02"),
                average_fill_price=Decimal("2389.6"),
                fill_timestamp=datetime(2026, 9, 3, 10, 15, tzinfo=UTC),
            )
        )
        session.flush()
        session.add(
            V2ExchangeFill(
                intent_id=intent_id,
                exchange_order_record_id=order_record_id,
                account_id="binance_testnet",
                exchange_order_id=f"entry-x-{index}",
                trade_id=f"entry-trade-{index}",
                symbol="ETH/USDT",
                side="SELL",
                reduce_only=False,
                filled_quantity=Decimal("0.02"),
                fill_price=Decimal("2389.6"),
                commission=Decimal("0.01"),
                commission_asset="USDT",
                exchange_event_time=datetime(2026, 9, 3, 10, 15, tzinfo=UTC),
                received_at=datetime(2026, 9, 3, 10, 16, tzinfo=UTC),
                raw_hash=f"entry-x-{index}:entry-trade-{index}",
            )
        )
        session.add(
            V2ManagedPosition(
                position_id=position_id,
                intent_id=intent_id,
                order_record_id=order_record_id,
                symbol="ETH/USDT",
                direction="short",
                execution_mode=V2ExecutionMode.BINANCE_TESTNET.value,
                quantity=Decimal("0.02"),
                entry_price=Decimal("2389.6"),
                entry_fee=Decimal("0.01"),
                state="QUARANTINED",
                projected_at=datetime(2026, 9, 3, 10, 16, tzinfo=UTC),
            )
        )
        session.commit()
    yield sessions
    for session in sessions:
        session.close()
    for engine in engines:
        engine.dispose()


def _manifest(*, quantity: str = "0.04") -> AdjudicationManifest:
    return AdjudicationManifest(
        adjudication_id="adj-eth-001",
        symbol="ETH/USDT",
        exchange_order_id="16782498190",
        exchange_trade_id="322607950",
        exchange_fill_quantity=Decimal(quantity),
        exchange_fill_side="BUY",
        exchange_fill_price=Decimal("2398.05"),
        exchange_fill_timestamp=datetime(2026, 9, 3, 11, 9, 29, tzinfo=UTC),
        operator_identity="operator@example",
        operator_reason="historical aggregate stop attribution",
        allocations=(
            ("db-a", "position-0", Decimal("0.02")),
            ("db-b", "position-1", Decimal("0.02")),
        ),
        account_scope_key="BINANCE:TESTNET:test-account",
    )


def _evidence() -> ExchangeAggregateExitEvidence:
    return ExchangeAggregateExitEvidence(
        exchange_order_id="16782498190",
        exchange_trade_id="322607950",
        symbol="ETH/USDT",
        side="BUY",
        reduce_only=True,
        executed_quantity=Decimal("0.04"),
        trade_quantity=Decimal("0.04"),
        price=Decimal("2398.05"),
        exchange_event_time=datetime(2026, 9, 3, 11, 9, 29, tzinfo=UTC),
        verification_reference="binance-testnet-audit:16782498190/322607950",
    )


def _provider(_order_id: str, _trade_id: str, _symbol: str) -> ExchangeAggregateExitEvidence:
    return _evidence()


def test_prepare_requires_exact_conservation_and_keeps_quarantine(db):
    manifest = _manifest()
    prepare_adjudication(db[0], manifest=manifest, evidence_provider=_provider, database_identity="db-a")
    db[0].commit()
    assert db[0].get(V2ManagedPosition, "position-0").state == "QUARANTINED"
    with pytest.raises(ValueError, match="UNALLOCATED_EXCHANGE_QUANTITY"):
        prepare_adjudication(
            db[1],
            manifest=AdjudicationManifest(
                **{
                    **manifest.__dict__,
                    "allocations": (("db-b", "position-1", Decimal("0.03")),),
                }
            ),
            evidence_provider=_provider,
            database_identity="db-b",
        )


def test_finalize_requires_all_participants_prepared(db):
    manifest = _manifest()
    prepare_adjudication(db[0], manifest=manifest, evidence_provider=_provider, database_identity="db-a")
    db[0].commit()
    with pytest.raises(ValueError, match="FINALIZABLE"):
        finalize_adjudication({"db-a": db[0], "db-b": db[1]}, manifest=manifest, evidence_provider=_provider)


def test_finalize_rejects_cross_database_different_case_for_same_trade(db):
    manifest = _manifest()
    prepare_adjudication(db[0], manifest=manifest, evidence_provider=_provider, database_identity="db-a")
    db[0].commit()
    conflicting = AdjudicationManifest(
        **{**manifest.__dict__, "adjudication_id": "adj-eth-002", "operator_reason": "different case"}
    )
    prepare_adjudication(db[1], manifest=conflicting, evidence_provider=_provider, database_identity="db-b")
    db[1].commit()
    with pytest.raises(ValueError, match="different adjudication"):
        finalize_adjudication({"db-a": db[0], "db-b": db[1]}, manifest=manifest, evidence_provider=_provider)


def test_finalize_requires_mapping_to_read_each_participant_case(db):
    manifest = _manifest()
    prepare_adjudication(db[0], manifest=manifest, evidence_provider=_provider, database_identity="db-a")
    db[0].commit()
    prepare_adjudication(db[1], manifest=manifest, evidence_provider=_provider, database_identity="db-b")
    db[1].commit()
    with pytest.raises(ValueError, match="one session per declared database"):
        finalize_adjudication({"db-a": db[0]}, manifest=manifest, evidence_provider=_provider)


def test_prepare_rejects_reusing_exchange_trade_for_different_case(db):
    manifest = _manifest()
    prepare_adjudication(db[0], manifest=manifest, evidence_provider=_provider, database_identity="db-a")
    db[0].commit()
    conflicting = AdjudicationManifest(
        **{**manifest.__dict__, "adjudication_id": "adj-eth-002", "operator_reason": "different"}
    )
    with pytest.raises(ValueError, match="ADJUDICATION_MANIFEST_CONFLICT"):
        prepare_adjudication(db[0], manifest=conflicting, evidence_provider=_provider, database_identity="db-a")


def test_case_key_is_deterministic_and_manifest_hash_ignores_audit_instance_id():
    first = _manifest()
    second = AdjudicationManifest(**{**first.__dict__, "adjudication_id": "another-audit-id"})
    assert first.case_key == second.case_key
    assert first.manifest_hash == second.manifest_hash


def test_same_logical_case_allows_distinct_audit_instance_ids_across_participants(db):
    first = _manifest()
    second = AdjudicationManifest(**{**first.__dict__, "adjudication_id": "participant-b-audit"})
    prepare_adjudication(db[0], manifest=first, evidence_provider=_provider, database_identity="db-a")
    prepare_adjudication(db[1], manifest=second, evidence_provider=_provider, database_identity="db-b")
    db[0].commit()
    db[1].commit()
    finalize_adjudication({"db-a": db[0], "db-b": db[1]}, manifest=first, evidence_provider=_provider)
    assert db[0].get(V2ManagedPosition, "position-0").state == "CLOSED"
    assert db[1].get(V2ManagedPosition, "position-1").state == "CLOSED"


def test_same_manifest_prepare_is_idempotent_and_adds_second_participant(db):
    manifest = _manifest()
    first = prepare_adjudication(db[0], manifest=manifest, evidence_provider=_provider, database_identity="db-a")
    db[0].commit()
    second = prepare_adjudication(db[0], manifest=manifest, evidence_provider=_provider, database_identity="db-a")
    db[0].commit()
    assert first.adjudication_id == second.adjudication_id
    assert db[0].get(V2ManagedPosition, "position-0").state == "QUARANTINED"


def test_unverified_evidence_fails_closed(db):
    with pytest.raises(ValueError, match="UNVERIFIED_EXCHANGE_EVIDENCE"):
        prepare_adjudication(db[0], manifest=_manifest(), database_identity="db-a")


def test_finalize_projects_only_after_all_participants_prepare(db):
    manifest = _manifest()
    prepare_adjudication(db[0], manifest=manifest, evidence_provider=_provider, database_identity="db-a")
    db[0].commit()
    prepare_adjudication(db[1], manifest=manifest, evidence_provider=_provider, database_identity="db-b")
    db[1].commit()
    finalize_adjudication({"db-a": db[0], "db-b": db[1]}, manifest=manifest, evidence_provider=_provider)
    db[0].commit()
    db[1].commit()
    assert db[0].get(V2ManagedPosition, "position-0").state == "CLOSED"
    assert db[1].get(V2ManagedPosition, "position-1").state == "CLOSED"
    assert len(tuple(db[0].scalars(select(V2ExchangeOrder)))) == 1
    assert len(tuple(db[0].scalars(select(V2ExchangeFill)))) == 1
    audit = db[0].scalar(select(V2AdjudicationFinalization))
    assert audit is not None
    assert audit.allocated_quantity == Decimal("0.02")
    assert audit.aggregate_quantity.quantize(Decimal("0.00000001")) == Decimal("0.04000000")
    assert db[0].get(V2ManagedPosition, "position-0").realized_pnl is None
    assert audit.evidence_hash == _evidence().evidence_hash
    assert audit.exchange_trade_id == "322607950"
    assert len(tuple(db[0].scalars(select(V2ExecutionEvent)))) == 1
    assert "aggregate_exchange_fill_quantity" in tuple(db[0].scalars(select(V2ExecutionEvent)))[0].event_payload
    finalize_adjudication({"db-a": db[0], "db-b": db[1]}, manifest=manifest, evidence_provider=_provider)


def test_evidence_provider_is_read_only_and_verifies_order_trade_relationship():
    class Adapter:
        def query_filled_order_by_id(self, symbol, order_id):
            from services.automated_trading.infrastructure.binance_adapter import ExchangeOrderReceipt

            return ExchangeOrderReceipt(
                exchange_order_id=order_id,
                client_order_id="stop-client",
                symbol=symbol,
                side="buy",
                order_type="stop_market",
                quantity=Decimal("0.04"),
                price=Decimal("2398.05"),
                status="filled",
                acknowledged_at=datetime(2026, 9, 3, 11, 9, 29, tzinfo=UTC),
                reduce_only=True,
            )

        def fetch_fills(self, symbol, order_id):
            from services.automated_trading.infrastructure.binance_adapter import ExchangeFillReceipt

            return (
                ExchangeFillReceipt(
                    exchange_order_id=order_id,
                    trade_id="322607950",
                    filled_quantity=Decimal("0.04"),
                    fill_price=Decimal("2398.05"),
                    fee=Decimal("0"),
                    fill_timestamp=datetime(2026, 9, 3, 11, 9, 29, tzinfo=UTC),
                ),
            )

        def submit_market_order(self, *args, **kwargs):
            raise AssertionError("adjudication must not submit orders")

    evidence = BinanceAggregateExitEvidenceProvider(Adapter())("16782498190", "322607950", "ETH/USDT")
    assert evidence.executed_quantity == Decimal("0.04")
    assert evidence.verification_reference.startswith("binance-read:")


def test_non_quarantined_lifecycle_cannot_prepare(db):
    db[0].get(V2ManagedPosition, "position-0").state = "CLOSED"
    db[0].commit()
    with pytest.raises(ValueError, match="QUARANTINED"):
        prepare_adjudication(db[0], manifest=_manifest(), evidence_provider=_provider, database_identity="db-a")


@pytest.mark.parametrize(
    ("manifest_patch", "expected"),
    [
        ({"symbol": "BTC/USDT"}, "SYMBOL_MISMATCH"),
        ({"exchange_fill_side": "SELL"}, "SIDE_OR_REDUCE_ONLY_MISMATCH"),
        (
            {
                "allocations": (
                    ("db-a", "position-0", Decimal("0.03")),
                    ("db-a", "position-0", Decimal("0.01")),
                )
            },
            "DUPLICATE_ALLOCATION",
        ),
        ({"allocations": (("db-a", "position-0", Decimal("0.01")),)}, "UNALLOCATED_EXCHANGE_QUANTITY"),
    ],
)
def test_invalid_manifest_fails_without_changing_lifecycle(db, manifest_patch, expected):
    manifest = AdjudicationManifest(**{**_manifest().__dict__, **manifest_patch})
    with pytest.raises(ValueError, match=expected):
        prepare_adjudication(db[0], manifest=manifest, evidence_provider=_provider, database_identity="db-a")
    assert db[0].get(V2ManagedPosition, "position-0").state == "QUARANTINED"


def test_prepared_manifest_is_append_only(db):
    prepare_adjudication(db[0], manifest=_manifest(), evidence_provider=_provider, database_identity="db-a")
    db[0].commit()
    case = db[0].scalar(select(V2AdjudicationCase))
    assert case is not None
    case.operator_reason = "rewritten"
    with pytest.raises(ValueError, match="append-only"):
        db[0].flush()
    db[0].rollback()
