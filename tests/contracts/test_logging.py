from __future__ import annotations

import logging
import sys

from shared.logging import (
    _WEBSOCKET_TRANSPORT_TRACEBACK_FILTER,
    configure_external_library_loggers,
)


def test_exchange_and_wire_loggers_are_never_debug_level() -> None:
    configure_external_library_loggers()

    for name in ("ccxt", "urllib3", "websockets", "httpx", "httpcore"):
        assert logging.getLogger(name).level >= logging.WARNING


def _exception_record(*, filename: str) -> logging.LogRecord:
    try:
        exec(compile("raise RuntimeError('transport failed')", filename, "exec"))
    except RuntimeError:
        return logging.getLogger("asyncio").makeRecord(
            "asyncio",
            logging.ERROR,
            filename,
            1,
            "event loop callback failed",
            (),
            exc_info=sys.exc_info(),
        )
    raise AssertionError("synthetic exception was not raised")


def test_asyncio_filter_drops_only_third_party_websocket_tracebacks() -> None:
    configure_external_library_loggers()

    websocket_record = _exception_record(
        filename="C:/runtime/site-packages/websockets/asyncio/client.py"
    )
    application_record = _exception_record(filename="C:/app/services/execution/runtime.py")

    assert _WEBSOCKET_TRANSPORT_TRACEBACK_FILTER in logging.getLogger("asyncio").filters
    assert _WEBSOCKET_TRANSPORT_TRACEBACK_FILTER.filter(websocket_record) is False
    assert _WEBSOCKET_TRANSPORT_TRACEBACK_FILTER.filter(application_record) is True
