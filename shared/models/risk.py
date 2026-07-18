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

MEDIUM_RISK_PROFILE_KEY = "medium_binance_top20"


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


def medium_risk_profile() -> RiskProfile:
    """Conservative promote sizing (2026-07-18): BTC/ETH verified-edge promote.

    Tightened from ultra-aggressive Paper sampling profile. These values must stay
    in sync with AUTO_PAPER_TECHNICAL_RULES["position_rules"] in bootstrap.py.
    """
    return RiskProfile(
        risk_profile_id=MEDIUM_RISK_PROFILE_KEY,
        single_trade_risk_limit=0.01,       # Conservative: was 0.05
        max_symbol_exposure=0.12,           # Conservative: was 0.35
        max_total_exposure=0.90,
        max_open_positions=10,
        max_leverage=8.0,                   # Conservative: was 40.0
        daily_loss_limit=0.20,
        weekly_loss_limit=0.25,
        drawdown_limit=0.25,
        hard_stop_drawdown_limit=0.40,
        consecutive_loss_limit=10,
        api_failure_limit=5,
        api_failure_window_minutes=10,
        market_scope="Binance USDT-M Top20",
        config_source="conservative promote sizing (2026-07-18, BTC/ETH only)",
    )


class RiskProfileUpdate(PlatformModel):
    """PUT /risk/profiles/{id} body — all optional (partial update)."""

    single_trade_risk_limit: float | None = None
    max_symbol_exposure: float | None = None
    max_total_exposure: float | None = None
    max_open_positions: int | None = None
    max_leverage: float | None = None
    daily_loss_limit: float | None = None
    weekly_loss_limit: float | None = None
    drawdown_limit: float | None = None
    hard_stop_drawdown_limit: float | None = None
    consecutive_loss_limit: int | None = None
    api_failure_limit: int | None = None
    api_failure_window_minutes: int | None = None
    market_scope: str | None = None
    config_source: str | None = None


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
