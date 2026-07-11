"""Binance-only network helpers (proxy scoped to this platform, not system-wide)."""

from __future__ import annotations

import json
from typing import Any
from urllib.request import ProxyHandler, build_opener

from shared.config import settings


def binance_proxy_url() -> str | None:
    """Return the HTTPS/HTTP proxy URL used for Binance traffic only."""
    proxy = (settings.binance_https_proxy or settings.binance_http_proxy or "").strip()
    return proxy or None


def binance_ccxt_proxies() -> dict[str, str] | None:
    proxy = binance_proxy_url()
    if not proxy:
        return None
    return {"http": proxy, "https": proxy}


def binance_ccxt_config(base: dict[str, Any] | None = None) -> dict[str, Any]:
    config = dict(base or {})
    proxies = binance_ccxt_proxies()
    if proxies:
        config["proxies"] = proxies
    return config


def binance_urlopen(url: str, *, timeout: float = 5):  # noqa: ANN201
    """Open a Binance REST URL, optionally via BINANCE_HTTPS_PROXY."""
    proxy = binance_proxy_url()
    proxies = {"http": proxy, "https": proxy} if proxy else {}
    opener = build_opener(ProxyHandler(proxies))
    return opener.open(url, timeout=timeout)


def binance_urlopen_json(url: str, *, timeout: float = 5) -> Any:
    with binance_urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))
