"""Trusted process-level switch for the non-promotable Testnet sampling lane."""

from __future__ import annotations

import os
from collections.abc import Mapping

NATURAL_TESTNET_ENV = "V2_NATURAL_E2E_ENABLED"


def _is_true(value: str | None) -> bool:
    return (value or "").strip().lower() == "true"


def natural_testnet_mode_requested(environ: Mapping[str, str] | None = None) -> bool:
    """Return whether this trusted process may arm natural Testnet sampling.

    The request is intentionally process-scoped. API/Celery payloads are not
    consulted, and the mode fails closed unless Testnet is explicit and live
    trading is explicitly disabled.
    """

    env = os.environ if environ is None else environ
    return (
        _is_true(env.get(NATURAL_TESTNET_ENV))
        and _is_true(env.get("BINANCE_USE_TESTNET"))
        and not _is_true(env.get("LIVE_TRADING_ENABLED"))
    )
