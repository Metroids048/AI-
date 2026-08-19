"""Authoritative Binance USD-M server clock for exchange-backed freshness checks."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from shared.binance_network import binance_urlopen_json
from shared.config import settings

LEGACY_TESTNET_USDM_REST_BASE = "https://testnet.binancefuture.com"


class BinanceClockUnavailable(RuntimeError):
    """Raised when the exchange clock cannot be read safely."""


def _parse_server_time(payload: Any) -> datetime:
    if not isinstance(payload, Mapping):
        raise BinanceClockUnavailable("BINANCE_SERVER_TIME_INVALID_PAYLOAD")
    raw = payload.get("serverTime")
    if raw is None:
        raise BinanceClockUnavailable("BINANCE_SERVER_TIME_MISSING")
    try:
        milliseconds = int(raw)
    except (TypeError, ValueError) as exc:
        raise BinanceClockUnavailable("BINANCE_SERVER_TIME_MISSING") from exc
    if milliseconds <= 0:
        raise BinanceClockUnavailable("BINANCE_SERVER_TIME_INVALID")
    return datetime.fromtimestamp(milliseconds / 1000, tz=UTC)


def fetch_binance_server_time(*, timeout: float = 5.0) -> datetime:
    """Read Binance's USD-M server clock, failing closed when unavailable.

    The configured Demo/Testnet endpoint is tried first.  The legacy Testnet
    endpoint is retained as the existing public-data fallback, never as a
    silent local-clock substitute.
    """

    primary = settings.binance_usdm_rest_base.rstrip("/")
    bases = [primary]
    if settings.binance_use_testnet and primary != LEGACY_TESTNET_USDM_REST_BASE:
        bases.append(LEGACY_TESTNET_USDM_REST_BASE)
    errors: list[str] = []
    for base in bases:
        try:
            return _parse_server_time(binance_urlopen_json(f"{base}/fapi/v1/time", timeout=timeout))
        except Exception as exc:  # noqa: BLE001 - preserve fail-closed contract
            errors.append(f"{base}:{type(exc).__name__}")
    raise BinanceClockUnavailable("BINANCE_SERVER_TIME_UNAVAILABLE:" + ",".join(errors))
