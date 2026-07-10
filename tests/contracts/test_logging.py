from __future__ import annotations

import logging

from shared.logging import configure_external_library_loggers


def test_ccxt_request_logging_is_never_debug_level() -> None:
    configure_external_library_loggers()

    assert logging.getLogger("ccxt").level >= logging.WARNING
