from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from scripts.audit_startup_v2_facts import audit
from services.automated_trading.domain.enums import V2CandidateType, V2ExecutionMode, V2IntentState
from services.automated_trading.infrastructure.binance_adapter import BinanceTestnetAdapter
from services.automated_trading.infrastructure.market_snapshot_provider import AuthoritativeAccountSnapshot
from services.automated_trading.infrastructure.models import Base
from services.automated_trading.infrastructure.repository import AutomatedTradingRepository


def test_audit_filters_v2_orders_through_their_intent_execution_mode(tmp_path, monkeypatch) -> None:
    """The Gate 0 snapshot must run against the current V2 schema."""
    database_url = f"sqlite:///{tmp_path / 'gate0-audit.db'}"
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        repo = AutomatedTradingRepository(session)
        repo.create_cycle(
            cycle_id="cycle-gate0-audit",
            symbol="BTC/USDT",
            timeframe="15m",
            bar_timestamp=datetime.now(UTC),
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            fencing_token="test-fence",
        )
        repo.create_intent(
            intent_id="intent-gate0-audit",
            cycle_id="cycle-gate0-audit",
            symbol="BTC/USDT",
            direction="long",
            candidate_key="testnet_sampling_v2",
            candidate_type=V2CandidateType.SAMPLING,
            execution_mode=V2ExecutionMode.BINANCE_TESTNET,
            decision_bar_timestamp=datetime.now(UTC),
            decision_funnel_id=None,
            state=V2IntentState.EXCHANGE_ACKNOWLEDGED,
        )
        order_record_id = repo.save_order_submission(
            intent_id="intent-gate0-audit",
            client_order_id="A2E-gate0-audit",
            quantity=0.01,
            leverage=30,
            submitted_at=datetime.now(UTC),
        )
        repo.save_exchange_fill_receipt(
            intent_id="intent-gate0-audit",
            exchange_order_record_id=order_record_id,
            account_id="binance_testnet",
            exchange_order_id="order-gate0-audit",
            trade_id="fill-gate0-audit",
            symbol="BTC/USDT",
            side="BUY",
            reduce_only=False,
            filled_quantity=Decimal("0.01"),
            fill_price=Decimal("65000"),
            commission=Decimal("0.01"),
            commission_asset="USDT",
            exchange_event_time=datetime.now(UTC),
            received_at=datetime.now(UTC),
            raw_hash="gate0-audit-fill",
        )
        session.commit()
    engine.dispose()

    monkeypatch.setattr(
        BinanceTestnetAdapter,
        "fetch_authoritative_snapshot",
        lambda _self: AuthoritativeAccountSnapshot(
            balance=Decimal("0"),
            equity=Decimal("0"),
            positions=[],
            pending_orders=[],
            snapshot_timestamp=datetime.now(UTC),
        ),
    )

    result = audit(database_url)

    assert [item["intent_id"] for item in result["unresolved_intents"]] == ["intent-gate0-audit"]
    assert [item["intent_id"] for item in result["related_orders"]] == ["intent-gate0-audit"]
    assert [item["intent_id"] for item in result["related_fills"]] == ["intent-gate0-audit"]
    assert len(result["lifecycle_gaps"]) == 1
    gap = result["lifecycle_gaps"][0]
    assert gap["kind"] == "CONFIRMED_ENTRY_FILL_UNPROJECTED"
    assert gap["intent_id"] == "intent-gate0-audit"
    assert gap["symbol"] == "BTC/USDT"
    assert gap["direction"] == "long"
    assert gap["state"] == "EXCHANGE_ACKNOWLEDGED"
    assert gap["client_order_id"] == "A2E-gate0-audit"
    assert gap["entry_fills"] == [
        {
            "exchange_order_id": "order-gate0-audit",
            "trade_id": "fill-gate0-audit",
            "filled_quantity": "0.010000000000000000",
            "fill_price": "65000.000000000000000000",
            "exchange_event_time": gap["entry_fills"][0]["exchange_event_time"],
        }
    ]
    assert result["safe_for_manual_baseline_ack"] is False
