"""Backward-compatible re-export.

The canonical settings live in ``shared.config`` so that every layer can
import configuration without a reverse dependency into ``apps/api``.
This module re-exports them for existing callers within ``apps/`` and tests.
"""

from __future__ import annotations

from shared.config import Settings, settings, validate_trading_environment

__all__ = ["Settings", "settings", "validate_trading_environment"]
