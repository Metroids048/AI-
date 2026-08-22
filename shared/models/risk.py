"""Risk-control contracts: RiskEvent (superset of domain doc §3.14).

Storage note: the DB `risk_events` table (infra/timescale/init.sql) keeps the
PDF subset (source/level/description/affected_symbols/expires_at). This Pydantic
model is the richer domain superset; the storage layer persists a projection.
PDF `level` == this model's `severity`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from .base import PlatformModel
from .enums import RiskEventType, RiskResolutionStatus, RiskSeverity

MEDIUM_RISK_PROFILE_KEY = "medium_binance_top20"
PAPER_RUNTIME_CONFIG_VERSION = "paper-testnet-canary-sampling-v2"
TESTNET_CANARY_SYMBOLS: tuple[str, ...] = (
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "XRP/USDT",
    "BNB/USDT",
)

# One explicit sizing contract for the Binance Testnet Canary sampling lane.
# Canary is a connectivity probe, not a portfolio sampler: the fixed notional cap
# is deliberately tiny and the exchange market-rules snapshot remains the final
# legality check for min-notional, step-size and tick-size constraints.
TESTNET_CANARY_RUNTIME_CONTRACT: dict[str, Any] = {
    "execution_mode": "BINANCE_TESTNET",
    "entry_authority": "TESTNET_CANARY",
    "candidate_lane": "TESTNET_SAMPLING",
    "strategy_id": "testnet_sampling_v2",
    "symbols": TESTNET_CANARY_SYMBOLS,
    "risk_per_trade": 0.01,
    "max_leverage": 30,
    "target_margin_fraction": 0.005,
    "max_margin_fraction": 0.005,
    "target_notional_fraction": 0.01,
    "max_symbol_exposure": 0.01,
    "max_open_positions": 1,
    "max_total_exposure": 0.01,
    "max_notional_usdt": 50.0,
}

PAPER_RUNTIME_LIMITS: dict[str, int | float] = {
    # Directional Testnet Canary baseline: 10% diagnostic stop-risk budget, 30x
    # leverage and 5% equity margin per symbol (1.50x equity notional at 30x).
    "risk_per_trade": 0.10,
    "max_margin_fraction": 0.05,
    "max_symbol_exposure": 1.50,
    "max_total_exposure": 7.50,
    "max_open_positions": 5,
    "max_leverage": 30.0,
    "max_portfolio_initial_risk_fraction": 0.25,
    "daily_loss_limit": 0.20,
    "weekly_loss_limit": 0.25,
    "drawdown_limit": 0.25,
    "hard_stop_drawdown_limit": 0.40,
    "consecutive_loss_limit": 10,
    # Raised from 20.0 to 36.0 (2026-08-06): sampling lane safety buffer.
    # ETH/USDT exchange min_notional=20 + step_size=0.001 means a 20 USDT
    # request floors to ~19.2 USDT actual after quantization, triggering
    # "normalized notional is below exchange minimum" in 78% of sampling
    # attempts. 36.0 provides 1.5x headroom for all BTC/ETH/SOL step sizes.
    "min_notional_usdt": 36.0,
}


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
    """Current five-symbol Binance Testnet Canary limits; never use for mainnet."""
    return RiskProfile(
        risk_profile_id=MEDIUM_RISK_PROFILE_KEY,
        single_trade_risk_limit=PAPER_RUNTIME_LIMITS["risk_per_trade"],
        max_symbol_exposure=PAPER_RUNTIME_LIMITS["max_symbol_exposure"],
        max_total_exposure=PAPER_RUNTIME_LIMITS["max_total_exposure"],
        max_open_positions=int(PAPER_RUNTIME_LIMITS["max_open_positions"]),
        max_leverage=PAPER_RUNTIME_LIMITS["max_leverage"],
        daily_loss_limit=PAPER_RUNTIME_LIMITS["daily_loss_limit"],
        weekly_loss_limit=PAPER_RUNTIME_LIMITS["weekly_loss_limit"],
        drawdown_limit=PAPER_RUNTIME_LIMITS["drawdown_limit"],
        hard_stop_drawdown_limit=PAPER_RUNTIME_LIMITS["hard_stop_drawdown_limit"],
        consecutive_loss_limit=int(PAPER_RUNTIME_LIMITS["consecutive_loss_limit"]),
        api_failure_limit=5,
        api_failure_window_minutes=10,
        market_scope="Binance USDT-M BTC/ETH/SOL/XRP/BNB Testnet Canary only",
        config_source=PAPER_RUNTIME_CONFIG_VERSION,
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
