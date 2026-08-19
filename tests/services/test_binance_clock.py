from __future__ import annotations

from datetime import UTC, datetime

import pytest

from services.data import binance_clock


def test_fetch_binance_server_time_parses_milliseconds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(binance_clock.settings, "binance_usdm_rest_base", "https://demo.example")
    monkeypatch.setattr(binance_clock.settings, "binance_use_testnet", True)
    monkeypatch.setattr(
        binance_clock,
        "binance_urlopen_json",
        lambda url, *, timeout: {"serverTime": 1_787_000_000_123},
    )

    assert binance_clock.fetch_binance_server_time() == datetime.fromtimestamp(1_787_000_000.123, tz=UTC)


def test_fetch_binance_server_time_tries_legacy_testnet_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr(binance_clock.settings, "binance_usdm_rest_base", "https://demo.example")
    monkeypatch.setattr(binance_clock.settings, "binance_use_testnet", True)

    def _fetch(url: str, *, timeout: float):
        del timeout
        calls.append(url)
        if "demo.example" in url:
            raise TimeoutError("demo unavailable")
        return {"serverTime": 1_787_000_000_000}

    monkeypatch.setattr(binance_clock, "binance_urlopen_json", _fetch)

    assert binance_clock.fetch_binance_server_time() == datetime.fromtimestamp(1_787_000_000, tz=UTC)
    assert calls == [
        "https://demo.example/fapi/v1/time",
        "https://testnet.binancefuture.com/fapi/v1/time",
    ]


def test_fetch_binance_server_time_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(binance_clock.settings, "binance_usdm_rest_base", "https://demo.example")
    monkeypatch.setattr(binance_clock.settings, "binance_use_testnet", False)
    monkeypatch.setattr(
        binance_clock,
        "binance_urlopen_json",
        lambda url, *, timeout: (_ for _ in ()).throw(TimeoutError(url)),
    )

    with pytest.raises(binance_clock.BinanceClockUnavailable, match="BINANCE_SERVER_TIME_UNAVAILABLE"):
        binance_clock.fetch_binance_server_time()
