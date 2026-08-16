from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from services.automated_trading.infrastructure.models import Base
from services.microstructure.health import refresh_health
from services.microstructure.readiness import evaluate_readiness
from services.microstructure.replay import ReplayWindow, replay_window
from services.microstructure.storage import persist_snapshot


def test_microstructure_snapshot_validation_and_deduplication() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    payload = {
        "symbol": "BTC/USDT",
        "exchange_timestamp_ms": 1,
        "received_at": datetime.now(UTC),
        "last_price": "100",
        "best_bid": "99",
        "best_ask": "101",
        "spread_bps": "200",
        "bids": [[99, 1]],
        "asks": [[101, 1]],
    }
    with Session(engine) as session:
        assert persist_snapshot(session, payload)
        assert not persist_snapshot(session, payload)
        assert refresh_health(session, "BTC/USDT")["rows_count"] == 1


def test_replay_models_partial_fill_and_fallback() -> None:
    window = ReplayWindow(
        side="long",
        quantity=Decimal("2"),
        reference_price=Decimal("100"),
        target_price=Decimal("110"),
        stop_price=Decimal("95"),
        best_bid=Decimal("99.5"),
        best_ask=Decimal("100.5"),
        bid_liquidity=Decimal("1"),
        ask_liquidity=Decimal("1"),
        future_touch=True,
        timeout=False,
    )
    result = replay_window(window)
    assert result.status == "partial"
    assert result.filled_quantity == Decimal("1")

    fallback = replay_window(window.__class__(**{**window.__dict__, "future_touch": False, "timeout": True}))
    assert fallback.status == "fallback_market"


def test_readiness_requires_btc_eth_and_coverage() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        report = evaluate_readiness(session)
        assert report.ready is False
        assert report.state == "MICROSTRUCTURE_COLLECTING_NOT_READY"
