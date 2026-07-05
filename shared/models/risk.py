"""Risk-control contracts: RiskEvent (superset of domain doc §3.14).

Storage note: the DB `risk_events` table (infra/timescale/init.sql) keeps the
PDF subset (source/level/description/affected_symbols/expires_at). This Pydantic
model is the richer domain superset; the storage layer persists a projection.
PDF `level` == this model's `severity`.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from .base import PlatformModel
from .enums import RiskEventType, RiskResolutionStatus, RiskSeverity


class RiskProfile(PlatformModel):
    """Runtime risk constraints with BTC/USDT perpetual-safe defaults."""

    risk_profile_id: str | None = None
    single_trade_risk_limit: float = 0.01
    max_symbol_exposure: float = 0.10
    max_total_exposure: float = 0.50
    max_open_positions: int = 3
    max_leverage: float = 3.0
    daily_loss_limit: float = 0.03
    weekly_loss_limit: float = 0.08
    drawdown_limit: float = 0.10
    hard_stop_drawdown_limit: float = 0.20
    consecutive_loss_limit: int = 4
    api_failure_limit: int = 3
    api_failure_window_minutes: int = 10
    market_scope: str = "BTC/USDT perpetual"
    config_source: str = "risk-control-and-safeguards-plan.md §04"


class RiskEvent(PlatformModel):
    """Unified risk event across macro / news / social / market / exec sources."""

    risk_event_id: str | None = None
    event_type: RiskEventType
    severity: RiskSeverity
    source: str = Field(examples=["jinshi", "twitter", "macro_calendar"])
    description: str
    affected_scope: list[str] | None = Field(default=None, description="Affected symbols; None == whole market")
    recommended_action: str | None = None
    resolution_status: RiskResolutionStatus = RiskResolutionStatus.DETECTED
    occurred_at: datetime | None = None
    expires_at: datetime | None = None


class RiskEventResolutionUpdate(PlatformModel):
    resolution_status: RiskResolutionStatus
