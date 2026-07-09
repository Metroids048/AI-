from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from services.data import DataRepository
from shared.models import MarketExtras


def test_market_intelligence_apis_return_feature_signal_and_provider_status(api_client, db_session) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    DataRepository(db_session).store_market_extras(
        [
            MarketExtras(
                symbol="BTC/USDT:USDT",
                time=now,
                funding_rate=Decimal("-0.0003"),
                long_ratio=Decimal("0.45"),
                short_ratio=Decimal("0.55"),
            )
        ]
    )

    features = api_client.get("/api/v1/market-intelligence/features", params={"symbol": "BTC/USDT"})
    signal = api_client.get("/api/v1/market-intelligence/signals", params={"symbol": "BTC/USDT"})
    events = api_client.get("/api/v1/market-intelligence/events", params={"symbol": "BTC/USDT"})
    refresh = api_client.post("/api/v1/market-intelligence/refresh", params={"symbol": "BTC/USDT"})

    assert features.status_code == 200
    assert features.json()["component_scores"]["funding_contrarian"] > 0
    assert signal.status_code == 200
    assert signal.json()["vote_weight"] <= 0.30
    assert signal.json()["provider_status"]["coinglass"]["status"] in {"ok", "missing_credentials"}
    assert events.status_code == 200
    assert "items" in events.json()
    assert refresh.status_code == 200
    assert refresh.json()["provider_status"]["defillama"]["status"] == "ok"
