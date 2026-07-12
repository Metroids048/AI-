from __future__ import annotations

from scripts import testnet_preflight
from shared.config import settings


def test_preflight_fails_closed_without_credentials_or_with_mainnet(monkeypatch) -> None:
    monkeypatch.setattr(settings, "binance_api_key", "")
    monkeypatch.setattr(settings, "binance_api_secret", "")
    monkeypatch.setattr(settings, "binance_use_testnet", False)
    monkeypatch.setattr(settings, "live_trading_enabled", True)
    monkeypatch.setattr(settings, "binance_https_proxy", "")
    monkeypatch.setattr(settings, "binance_http_proxy", "")

    report = testnet_preflight.build_preflight_report()

    assert report["safety_boundary"]["mainnet_disabled"] is False
    assert report["credentials"]["futures_configured"] is False
    assert report["futures_account"]["probed"] is False
    assert report["ready_for_futures_acceptance"] is False


def test_preflight_returns_only_sanitized_account_evidence(monkeypatch) -> None:
    monkeypatch.setattr(settings, "binance_api_key", "secret-key")
    monkeypatch.setattr(settings, "binance_api_secret", "secret-value")
    monkeypatch.setattr(settings, "spot_testnet_api_key", "")
    monkeypatch.setattr(settings, "spot_testnet_api_secret", "")
    monkeypatch.setattr(settings, "binance_use_testnet", True)
    monkeypatch.setattr(settings, "live_trading_enabled", False)
    monkeypatch.setattr(settings, "binance_https_proxy", "")
    monkeypatch.setattr(settings, "binance_http_proxy", "")
    monkeypatch.setattr(
        testnet_preflight,
        "probe_testnet_account",
        lambda order_limit: type(
            "Status",
            (),
            {
                "connected": True,
                "trading_mode": "demo",
                "api_backend": "testnet",
                "open_position_count": 0,
                "warning": None,
                "error": None,
            },
        )(),
    )

    report = testnet_preflight.build_preflight_report()

    assert report["ready_for_futures_acceptance"] is True
    assert report["ready_for_spot_carry"] is False
    assert "secret-key" not in str(report)
    assert "secret-value" not in str(report)
