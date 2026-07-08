"""Unified structured logging with sensitive-field redaction.

Every module should obtain its logger via ``get_logger(__name__)`` instead of
configuring handlers independently. Logs are written to stdout as
``LEVEL | name | message [key=value ...]`` which is easy to grep and ship to
a log aggregator.

Sensitive values (API keys, secrets, tokens, passwords) referenced in the
``extra`` mapping are redacted automatically so that no credential ever lands
in the log stream.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

from shared.config import settings

_SENSITIVE_KEY_FRAGMENTS = (
    "api_key",
    "api_secret",
    "secret",
    "token",
    "password",
    "bearer",
    "credential",
    "private_key",
)

_REDACTED = "***REDACTED***"


def _is_sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(fragment in lowered for fragment in _SENSITIVE_KEY_FRAGMENTS)


def _redact_value(value: Any) -> Any:
    if isinstance(value, str) and len(value) > 8:
        return value[:4] + "***"
    return _REDACTED


class RedactingFormatter(logging.Formatter):
    """Formatter that redacts sensitive keys present in ``record.args``/extra."""

    def format(self, record: logging.LogRecord) -> str:
        # Redact extra fields attached via logger.info(msg, extra={...})
        for key in list(getattr(record, "__dict__", {}).keys()):
            if _is_sensitive(key):
                setattr(record, key, _REDACTED)
        # Redact sensitive values inside the message args mapping
        if isinstance(record.args, dict):
            record.args = {
                k: (_REDACTED if _is_sensitive(k) else v) for k, v in record.args.items()
            }
        return super().format(record)


_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def _resolve_level() -> int:
    env_level = os.getenv("LOG_LEVEL", "").upper()
    if env_level in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        return getattr(logging, env_level)
    return logging.DEBUG if settings.app_env == "development" else logging.INFO


def get_logger(name: str) -> logging.Logger:
    """Return a configured logger.

    The root configuration is installed once; subsequent calls reuse the same
    handler set. Sensitive keys in ``extra`` are redacted before emission.
    """
    logger = logging.getLogger(name)
    if not logging.getLogger().handlers:
        root = logging.getLogger()
        root.setLevel(_resolve_level())
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(RedactingFormatter(_FORMAT, datefmt=_DATEFMT))
        root.addHandler(handler)
    logger.setLevel(_resolve_level())
    return logger


def log_with_extras(logger: logging.Logger, level: int, message: str, **extras: Any) -> None:
    """Log a message with structured extras, redacting sensitive fields.

    Example::

        log_with_extras(logger, logging.INFO, "order submitted",
                        symbol="BTC/USDT", api_key="sk-xxx")
        # → INFO | ... | order submitted [api_key=***REDACTED*** symbol=BTC/USDT]
    """
    safe = {k: (_REDACTED if _is_sensitive(k) else v) for k, v in extras.items()}
    kv = " ".join(f"{k}={v}" for k, v in safe.items())
    logger.log(level, f"{message} [{kv}]" if kv else message)
