"""Ported WorldQuant operator vocabulary (methodology, not raw expressions).

These are the factor-construction primitives observed in the local alpha mining
pipeline, re-expressed as pure pandas/numpy over crypto series (no TA-Lib, no
equity fundamentals). Bodies are stubs — implemented in Phase 1 (P1-03).

Per AGENTS.md Non-Negotiable #5, WorldQuant is a research *source*, never the
platform trunk. Nothing here is imported by apps/api.
"""

from __future__ import annotations

import pandas as pd


def rank(series: pd.Series) -> pd.Series:
    """Cross-sectional / rolling rank, normalized to [0, 1]."""
    ...


def ts_delta(series: pd.Series, window: int) -> pd.Series:
    """Value now minus value `window` bars ago."""
    ...


def ts_mean(series: pd.Series, window: int) -> pd.Series:
    """Rolling mean over `window` bars."""
    ...


def ts_std(series: pd.Series, window: int) -> pd.Series:
    """Rolling standard deviation over `window` bars."""
    ...


def delay(series: pd.Series, window: int) -> pd.Series:
    """Series shifted by `window` bars."""
    ...


def correlation(a: pd.Series, b: pd.Series, window: int) -> pd.Series:
    """Rolling correlation between two series."""
    ...


def group_rank(series: pd.Series, group: pd.Series) -> pd.Series:
    """Rank within group buckets (crypto sector/regime instead of industry)."""
    ...


def scale(series: pd.Series, target: float = 1.0) -> pd.Series:
    """Scale so the sum of absolute values equals `target`."""
    ...


def decay_linear(series: pd.Series, window: int) -> pd.Series:
    """Linearly-weighted moving average over `window` bars."""
    ...
