from __future__ import annotations

from shared.binance_network import binance_ccxt_config, binance_proxy_url, binance_urlopen
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


def test_binance_urlopen_disables_ambient_proxy_when_no_scoped_proxy(monkeypatch) -> None:
    from shared import binance_network

    captured = {}

    class StubOpener:
        def open(self, url, timeout):
            captured.update(url=url, timeout=timeout)
            return "response"

    monkeypatch.setattr(settings, "binance_https_proxy", "")
    monkeypatch.setattr(settings, "binance_http_proxy", "")
    monkeypatch.setattr(binance_network, "ProxyHandler", lambda proxies: captured.update(proxies=proxies) or proxies)
    monkeypatch.setattr(binance_network, "build_opener", lambda handler: StubOpener())

    assert binance_urlopen("https://demo-fapi.binance.com/ping", timeout=3) == "response"
    assert captured["proxies"] == {}


def test_medium_risk_profile_supports_top20_auto_trading() -> None:
    profile = medium_risk_profile()
    assert profile.risk_profile_id == MEDIUM_RISK_PROFILE_KEY
    # Bumped moderately more aggressive per operator request (2026-07),
    # alongside the paper-sizing floor fix in paper_signal.py/paper_runtime.py.
    assert profile.max_open_positions == 6
    assert profile.max_total_exposure == 0.60
    assert profile.max_leverage == 25
    assert profile.daily_loss_limit == 0.06
    assert profile.hard_stop_drawdown_limit == 0.22


def test_bootstrap_medium_risk_profile_is_idempotent(db_session) -> None:
    from services.execution.bootstrap import bootstrap_medium_risk_profile
    from services.strategy_library import RiskProfileRepository

    first = bootstrap_medium_risk_profile()
    second = bootstrap_medium_risk_profile()
    assert first == second == MEDIUM_RISK_PROFILE_KEY
    stored = RiskProfileRepository(db_session).get_profile(MEDIUM_RISK_PROFILE_KEY)
    assert stored is not None
    assert stored.max_leverage == medium_risk_profile().max_leverage
