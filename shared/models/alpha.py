"""Alpha methodology contracts.

Per the project decision (移植方法论到加密): the local WorldQuant USA-equity
alpha library is NOT ingested as raw expressions. Instead its *methodology* —
the operator vocabulary and factor-construction patterns — is ported to
BTC/USDT crypto. `AlphaPlan` is the structured intermediate the
research_source/worldquant_adapter produces; it always targets a crypto market
by default. See research_source/worldquant_adapter/README.md.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field

from .base import PlatformModel
from .enums import Market


class AlphaOperator(StrEnum):
    """Ported WorldQuant operator vocabulary (pure pandas/numpy, no TA-Lib)."""

    RANK = "rank"
    TS_DELTA = "ts_delta"
    TS_MEAN = "ts_mean"
    TS_STD = "ts_std"
    DELAY = "delay"
    CORRELATION = "correlation"
    GROUP_RANK = "group_rank"
    SCALE = "scale"
    DECAY_LINEAR = "decay_linear"


class AlphaPlan(PlatformModel):
    """Parsed, structured representation of an alpha expression."""

    raw_expression: str = Field(examples=["rank(close/delay(close,5))"])
    operators: list[AlphaOperator] = Field(default_factory=list)
    inputs: list[str] = Field(default_factory=list, description="Referenced fields/series")
    parameters: dict[str, Any] = Field(default_factory=dict)
    target_market: Market = Market.CRYPTO_PERP
