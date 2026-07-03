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
    ranked = series.rank(method="average", pct=True)
    return ranked.fillna(0.0)


def ts_delta(series: pd.Series, window: int) -> pd.Series:
    """Value now minus value `window` bars ago."""
    return series - series.shift(window)


def ts_mean(series: pd.Series, window: int) -> pd.Series:
    """Rolling mean over `window` bars."""
    return series.rolling(window=window, min_periods=window).mean()


def ts_std(series: pd.Series, window: int) -> pd.Series:
    """Rolling standard deviation over `window` bars."""
    return series.rolling(window=window, min_periods=window).std()


def delay(series: pd.Series, window: int) -> pd.Series:
    """Series shifted by `window` bars."""
    return series.shift(window)


def correlation(a: pd.Series, b: pd.Series, window: int) -> pd.Series:
    """Rolling correlation between two series."""
    return a.rolling(window=window, min_periods=window).corr(b)


def group_rank(series: pd.Series, group: pd.Series) -> pd.Series:
    """Rank within group buckets (crypto sector/regime instead of industry)."""
    if len(series) != len(group):
        raise ValueError("series and group must have the same length")
    ranked = series.groupby(group).rank(method="average", pct=True)
    return ranked.fillna(0.0)


def scale(series: pd.Series, target: float = 1.0) -> pd.Series:
    """Scale so the sum of absolute values equals `target`."""
    denominator = series.abs().sum()
    if denominator == 0:
        return series * 0.0
    return series * (target / denominator)


def decay_linear(series: pd.Series, window: int) -> pd.Series:
    """Linearly-weighted moving average over `window` bars."""
    weights = pd.Series(range(1, window + 1), dtype="float64")

    def _weighted(values: pd.Series) -> float:
        return float((values * weights).sum() / weights.sum())

    return series.rolling(window=window, min_periods=window).apply(_weighted, raw=False)
