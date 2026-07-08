"""Validation admission policy — canonical thresholds sourced from settings.

Previously the admission thresholds (Sharpe ≥ 1.0, PF ≥ 1.3, MaxDD ≤ 25%,
Expectancy > 0) were hardcoded in both ``admission.py`` and ``carry.py``,
duplicated across files. This module centralises them so they can be tuned
via environment variables without code changes.
"""

from __future__ import annotations

from dataclasses import dataclass

from shared.config import settings


@dataclass(frozen=True)
class ValidationPolicy:
    """Promotion thresholds for Paper/Live admission."""

    min_sharpe: float = 1.0
    min_profit_factor: float = 1.3
    max_drawdown: float = 0.25
    min_expectancy: float = 0.0

    @classmethod
    def from_settings(cls) -> ValidationPolicy:
        return cls(
            min_sharpe=settings.validation_min_sharpe,
            min_profit_factor=settings.validation_min_profit_factor,
            max_drawdown=settings.validation_max_drawdown,
            min_expectancy=settings.validation_min_expectancy,
        )


# Module-level default — constructed once from settings.
default_policy = ValidationPolicy.from_settings()
