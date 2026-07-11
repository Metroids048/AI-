from __future__ import annotations

import logging

from shared.logging import configure_external_library_loggers


def test_exchange_and_wire_loggers_are_never_debug_level() -> None:
    configure_external_library_loggers()

    for name in ("ccxt", "urllib3", "websockets", "httpx", "httpcore"):
        assert logging.getLogger(name).level >= logging.WARNING
