from __future__ import annotations

import re

_SYMBOL_ALIASES = {
    "BTCUSDT": "BTC/USDT",
    "ETHUSDT": "ETH/USDT",
    "ZECUSDT": "ZEC/USDT",
    "BCHUSDT": "BCH/USDT",
    "TRBUSDT": "TRB/USDT",
}


def normalize_symbol(value: str) -> str | None:
    raw = value.upper().replace("＄", "").replace("/", "").replace("-", "")
    raw = raw.removesuffix("PERP")
    if raw in _SYMBOL_ALIASES:
        return _SYMBOL_ALIASES[raw]
    if re.fullmatch(r"[A-Z]{2,12}USDT", raw):
        return f"{raw[:-4]}/USDT"
    if re.fullmatch(r"[A-Z]{2,12}", raw):
        return f"{raw}/USDT"
    return None
