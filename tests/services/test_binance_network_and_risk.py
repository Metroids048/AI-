from __future__ import annotations

from shared.binance_network import binance_ccxt_config, binance_proxy_url
from shared.config import settings
from shared.models.risk import MEDIUM_RISK_PROFILE_KEY, medium_risk_profile


def test_binance_ccxt_config_applies_proxy(monkeypatch) -> None:
    monkeypatch.setattr(settings, "binance_https_proxy", "http://127.0.0.1:7890")
    monkeypatch.setattr(settings, "binance_http_proxy", "")
    config = binance_ccxt_config({"apiKey": "k"})
    assert config["proxies"] == {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}


def test_binance_proxy_url_prefers_https(monkeypatch) -> None:
    monkeypatch.setattr(settings, "binance_https_proxy", "http://127.0.0.1:7890")
    monkeypatch.setattr(settings, "binance_http_proxy", "http://127.0.0.1:1080")
    assert binance_proxy_url() == "http://127.0.0.1:7890"


def test_medium_risk_profile_supports_top20_auto_trading() -> None:
    profile = medium_risk_profile()
    assert profile.risk_profile_id == MEDIUM_RISK_PROFILE_KEY
    assert profile.max_open_positions == 5
    assert profile.max_total_exposure == 0.50
    assert profile.max_leverage == 5
    assert profile.daily_loss_limit == 0.04


def test_bootstrap_medium_risk_profile_is_idempotent(db_session) -> None:
    from services.execution.bootstrap import bootstrap_medium_risk_profile
    from services.strategy_library import RiskProfileRepository

    first = bootstrap_medium_risk_profile()
    second = bootstrap_medium_risk_profile()
    assert first == second == MEDIUM_RISK_PROFILE_KEY
    stored = RiskProfileRepository(db_session).get_profile(MEDIUM_RISK_PROFILE_KEY)
    assert stored is not None
    assert stored.max_leverage == medium_risk_profile().max_leverage
