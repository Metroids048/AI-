from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from apps.api.routers import notifications as notifications_router
from services.data import DataRepository
from services.notifications import NotificationDispatcherService, NotificationDispatchResult
from services.strategy_library import NotificationRepository, StrategyRepository
from shared.models import NotificationOutboxItem, StrategyCreate, StrategyRules


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

    update = api_client.patch(
        f"/api/v1/notifications/outbox/{outbox.json()['items'][0]['notification_id']}/delivery",
        json={"delivery_status": "sent"},
    )
    assert update.status_code == 200
    assert update.json()["delivery_status"] == "sent"
    assert update.json()["delivery_attempts"] == 1
    assert update.json()["delivered_at"] is not None

    resolved = api_client.patch(
        f"/api/v1/risk/events/{risk_resp.json()['risk_event_id']}/resolution",
        json={"resolution_status": "resolved"},
    )
    assert resolved.status_code == 200

    persisted_outbox = api_client.get("/api/v1/notifications/outbox?delivery_status=sent&severity=high")
    assert persisted_outbox.status_code == 200
    assert persisted_outbox.json()["total"] == 1
    assert persisted_outbox.json()["items"][0]["source_ref"] == risk_resp.json()["risk_event_id"]


def test_notification_outbox_manual_create_and_filters(api_client) -> None:
    low_risk = api_client.post(
        "/api/v1/risk/events",
        json={
            "event_type": "data_gap",
            "severity": "mid",
            "source": "collector",
            "description": "minor ingestion lag",
            "affected_scope": ["ETH/USDT"],
            "resolution_status": "detected",
        },
    )
    assert low_risk.status_code == 201
    assert api_client.get("/api/v1/notifications/outbox").json()["total"] == 0

    manual = api_client.post(
        "/api/v1/notifications/outbox",
        json={
            "notification_id": "manual:ops-check",
            "event_type": "ops_manual",
            "severity": "info",
            "channel_group": "ops",
            "subject": "manual operator note",
            "body": "check paper collector after deploy",
            "source_ref": "runbook:paper",
        },
    )
    assert manual.status_code == 201

    filtered = api_client.get("/api/v1/notifications/outbox?event_type=ops_manual&delivery_status=pending_adapter")
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["notification_id"] == "manual:ops-check"

    failed = api_client.patch(
        "/api/v1/notifications/outbox/manual:ops-check/delivery",
        json={"delivery_status": "failed", "last_error": "adapter not configured"},
    )
    assert failed.status_code == 200
    assert failed.json()["delivery_status"] == "failed"
    assert failed.json()["delivery_attempts"] == 1
    assert failed.json()["last_error"] == "adapter not configured"


def test_notification_dispatch_endpoint_replays_specific_item(api_client, db_session, monkeypatch) -> None:
    class StubAdapter:
        def send(self, item: NotificationOutboxItem) -> NotificationDispatchResult:
            return NotificationDispatchResult(channel="stub", success=True, external_ref=f"ok:{item.notification_id}")

    repo = NotificationRepository(db_session)
    repo.create_notification(
        NotificationOutboxItem(
            notification_id="manual:dispatch",
            event_type="ops_manual",
            severity="info",
            channel_group="ops",
            delivery_channels=["stub"],
            subject="dispatch me",
            body="manual replay target",
        )
    )
    dispatcher = NotificationDispatcherService(
        repository=repo,
        adapters={"stub": StubAdapter()},
        now_factory=lambda: datetime(2026, 7, 4, tzinfo=UTC),
    )
    monkeypatch.setattr(notifications_router, "_dispatcher", lambda db: dispatcher)

    response = api_client.post("/api/v1/notifications/outbox/dispatch?notification_id=manual:dispatch")

    assert response.status_code == 202
    assert response.json() == {"dispatched": 1}
    dispatched = repo.get_notification("manual:dispatch")
    assert dispatched is not None
    assert dispatched.delivery_status == "sent"


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
