"""Execution-runtime contracts for gateways, account sync, and reconciliation."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from .base import PlatformModel


class ExchangeGatewayCapability(PlatformModel):
    gateway_name: str
    exchange: str
    market_type: str
    supports_account_sync: bool = True
    supports_positions_sync: bool = True
    supports_order_submit: bool = True
    supports_order_cancel: bool = True
    supports_reconciliation: bool = True


class TradingRuntimeStatus(PlatformModel):
    """Safe-to-display trading mode and gateway readiness; never includes secrets."""

    exchange: str = "binance"
    mode: str = "paper"
    app_env: str
    binance_use_testnet: bool
    live_trading_enabled: bool
    credentials_configured: bool
    gateway_available: bool
    supported_modes: list[str] = Field(default_factory=lambda: ["paper", "testnet"])
    notes: list[str] = Field(default_factory=list)


class ExchangeAccountSnapshot(PlatformModel):
    snapshot_id: str | None = None
    live_run_id: str
    exchange: str
    wallet_balance: float
    available_balance: float
    margin_balance: float
    unrealized_pnl: float = 0.0
    open_position_count: int = 0
    source_ref: str | None = None
    snapshot_time: datetime | None = None


class ReconciliationRecord(PlatformModel):
    reconciliation_id: str | None = None
    live_run_id: str
    reconciliation_status: str = Field(description="ok / warning / mismatch / recovered")
    open_order_count: int = 0
    position_mismatches: list[dict[str, Any]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
