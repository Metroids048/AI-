from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from services.data import DataRepository
from services.data.market_intelligence import CoinGlassProvider, CryptoQuantProvider, MarketIntelligenceService
from shared.models import MarketExtras, RiskEvent, RiskEventType, RiskSeverity


def test_market_intelligence_providers_are_disabled_without_credentials(db_session, monkeypatch) -> None:
    monkeypatch.setattr("services.data.market_intelligence.settings.coinglass_api_key", "")
    monkeypatch.setattr("services.data.market_intelligence.settings.cryptoquant_api_key", "")

    status = MarketIntelligenceService(data_repo=DataRepository(db_session)).provider_status()

    assert status["coinglass"]["status"] == "missing_credentials"
    assert status["cryptoquant"]["status"] == "missing_credentials"
    assert status["defillama"]["status"] == "ok"


def test_provider_fixture_payloads_normalize_to_features() -> None:
    derivatives = CoinGlassProvider.normalize_derivatives_payload(
        "BTC/USDT",
        {
            "openInterest": "1000000",
            "fundingRate": "-0.0002",
            "longRatio": "0.42",
            "shortRatio": "0.58",
            "liquidationUsd": "2500000",
        },
    )
    flows = CryptoQuantProvider.normalize_exchange_flow_payload(
        {"inflow": "5000", "outflow": "12000", "stablecoinReserve": "15000000"}
    )

    assert derivatives["funding_rate"] == -0.0002
    assert derivatives["short_ratio"] == 0.58
    assert flows["exchange_outflow_score"] is not None


def test_market_intelligence_signal_uses_market_extras(db_session) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    DataRepository(db_session).store_market_extras(
        [
            MarketExtras(
                symbol="BTC/USDT:USDT",
                time=now,
                funding_rate=Decimal("-0.0004"),
                long_ratio=Decimal("0.40"),
                short_ratio=Decimal("0.60"),
            )
        ]
    )

    signal = MarketIntelligenceService(data_repo=DataRepository(db_session)).build_signal(symbol="BTC/USDT")

    assert signal.should_participate is True
    assert signal.direction == "long"
    assert 0 < signal.vote_weight <= 0.30
    assert "funding_contrarian" in signal.component_scores


def test_high_severity_event_enters_cooldown_and_disables_vote(db_session) -> None:
    repo = DataRepository(db_session)
    repo.store_risk_event(
        RiskEvent(
            event_type=RiskEventType.NEWS_RISK,
            severity=RiskSeverity.HIGH,
            source="jinshi",
            description="major macro shock",
            affected_scope=["BTC/USDT"],
            recommended_action="pause_strategy",
            occurred_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
        )
    )

    signal = MarketIntelligenceService(data_repo=repo).build_signal(symbol="BTC/USDT")

    assert signal.active_event_cooldown is True
    assert signal.should_participate is False
    assert signal.vote_weight == 0
