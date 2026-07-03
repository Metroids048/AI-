"""B-level macro event contract (PDF §3.3 -> macro_events table)."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from .base import PlatformModel
from .enums import RiskSeverity


class MacroEvent(PlatformModel):
    event_name: str = Field(examples=["FOMC", "CPI", "NFP"])
    source: str = Field(examples=["trading_economics", "forex_factory"])
    impact: RiskSeverity = RiskSeverity.LOW
    scheduled_at: datetime
    affected_symbols: list[str] | None = None
    notes: str | None = None
