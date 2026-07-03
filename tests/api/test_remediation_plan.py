from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from services.data import DataRepository
from services.strategy_library import StrategyRepository
from shared.models import StrategyCreate, StrategyRules


def _bar(symbol: str, at: datetime, close: str) -> dict:
    return {
        "symbol": symbol,
        "exchange": "binance",
        "timeframe": "1h",
        "time": at,
        "open": Decimal(close),
        "high": Decimal(close),
        "low": Decimal(close),
        "close": Decimal(close),
        "volume": Decimal("100"),
    }


def test_walk_forward_report_and_stress_gate(api_client, db_session) -> None:
    strategy = StrategyRepository(db_session).create_strategy(
        StrategyCreate(
            strategy_key="carry_walk_forward",
            source="manual",
            core_thesis="funding carry must survive OOS and stress checks",
            rules=StrategyRules(
                entry_rules={"funding_threshold_bps": 1},
                exit_rules={"hold_hours": 8},
                stoploss_rules={"basis_bps": 20},
                position_rules={"notional_usdt": 1000, "trials_count": 3},
            ),
        )
    )
    start = datetime(2024, 1, 1, tzinfo=UTC)
    repo = DataRepository(db_session)
    repo.store_ohlcv_bars(
        [
            _bar("BTC/USDT", start, "100"),
            _bar("BTC/USDT", start + timedelta(hours=8), "101"),
            _bar("BTC/USDT", start + timedelta(hours=16), "102"),
            _bar("BTC/USDT", start + timedelta(hours=24), "103"),
            _bar("BTC/USDT:USDT", start, "100"),
            _bar("BTC/USDT:USDT", start + timedelta(hours=8), "100"),
            _bar("BTC/USDT:USDT", start + timedelta(hours=16), "101"),
            _bar("BTC/USDT:USDT", start + timedelta(hours=24), "102"),
        ]
    )
    repo.store_market_extras(
        [
            {"symbol": "BTC/USDT:USDT", "time": start, "funding_rate": Decimal("0.0010")},
            {
                "symbol": "BTC/USDT:USDT",
                "time": start + timedelta(hours=8),
                "funding_rate": Decimal("0.0012"),
            },
            {
                "symbol": "BTC/USDT:USDT",
                "time": start + timedelta(hours=16),
                "funding_rate": Decimal("0.0011"),
            },
        ]
    )

    response = api_client.post(
        "/api/v1/backtests/carry/walk-forward",
        json={
            "strategy_id": strategy.strategy_id,
            "spot_symbol": "BTC/USDT",
            "perp_symbol": "BTC/USDT:USDT",
            "timeframe": "1h",
            "start_at": start.isoformat(),
            "end_at": (start + timedelta(hours=24)).isoformat(),
        },
    )
    assert response.status_code == 202
    run_id = response.json()["resource_id"]

    report = api_client.get(f"/api/v1/validation/reports/{run_id}")
    assert report.status_code == 200
    payload = report.json()
    assert payload["oos_summary"]["window_count"] >= 1
    assert payload["stress_test_results"]
    assert payload["lookahead_check"]["lookahead_bias_detected"] is False
    assert "stress_" in payload["gate"]["reason"]


def test_operational_visibility_endpoints(api_client, db_session) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    risk_resp = api_client.post(
        "/api/v1/risk/events",
        json={
            "event_type": "exchange_incident",
            "severity": "high",
            "source": "binance_status",
            "description": "exchange degradation",
            "affected_scope": ["BTC/USDT"],
            "resolution_status": "detected",
            "occurred_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=1)).isoformat(),
        },
    )
    assert risk_resp.status_code == 201

    capabilities = api_client.get("/api/v1/market/capabilities")
    assert capabilities.status_code == 200
    assert capabilities.json()["total"] >= 2
    assert capabilities.json()["items"][0]["supports_order_placement"] is False

    health = api_client.get("/api/v1/system/health/dependencies")
    assert health.status_code == 200
    assert health.json()["dependencies"]["database"]["status"] == "ok"

    outbox = api_client.get("/api/v1/notifications/outbox")
    assert outbox.status_code == 200
    assert outbox.json()["total"] == 1
    assert outbox.json()["items"][0]["delivery_status"] == "pending_adapter"


def test_unregistered_agent_executor_is_not_marked_completed(api_client) -> None:
    response = api_client.post(
        "/api/v1/agents/tasks",
        json={"agent_type": "telegram_agent", "task_type": "place_order"},
    )
    assert response.status_code == 202
    task = api_client.get(f"/api/v1/agents/tasks/{response.json()['resource_id']}")
    assert task.status_code == 200
    assert task.json()["task_status"] == "failed"
    assert "no executor" in task.json()["error_summary"]
