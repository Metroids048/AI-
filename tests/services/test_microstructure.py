from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from services.automated_trading.infrastructure.models import Base
from services.microstructure.health import refresh_health
from services.microstructure.readiness import evaluate_readiness
from services.microstructure.replay import ReplayWindow, replay_market_control, replay_window
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
    assert replay_market_control(window).mode == "market_control"

    fallback = replay_window(window.__class__(**{**window.__dict__, "future_touch": False, "timeout": True}))
    assert fallback.status == "fallback_market"


def test_readiness_requires_btc_eth_and_coverage() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        report = evaluate_readiness(session)
        assert report.ready is False
        assert report.state == "MICROSTRUCTURE_COLLECTING_NOT_READY"


def test_readiness_excludes_candidates_before_collection_start() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        persist_snapshot(
            session,
            {
                "symbol": "BTC/USDT",
                "exchange_timestamp_ms": 2,
                "received_at": datetime(2026, 8, 16, 10, 0, tzinfo=UTC),
                "last_price": "100",
                "best_bid": "99",
                "best_ask": "101",
                "spread_bps": "200",
                "bids": [[99, 1]],
                "asks": [[101, 1]],
            },
        )
        from services.automated_trading.infrastructure.models import V2DecisionSnapshot

        session.add(
            V2DecisionSnapshot(
                snapshot_id="s1",
                cycle_id="c1",
                decision_id="d1",
                symbol="BTC/USDT",
                decision_time=datetime(2026, 8, 1, tzinfo=UTC),
                snapshot_hash="h1",
                payload={"candidate": {"side": "LONG"}},
            )
        )
        session.commit()
        assert evaluate_readiness(session).total_candidate_windows == 0
