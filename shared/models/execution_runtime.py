"""Execution-runtime contracts for gateways, account sync, and reconciliation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import Field, model_validator

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
    auto_execute_enabled: bool = False
    auto_execution_state: str = "monitoring_only"
    execution_ready: bool = False
    execution_blockers: list[str] = Field(default_factory=list)
    testnet_acceptance_verified: bool = False
    fixed_top20_count: int = 20
    simulation_catalog_count: int = 0
    active_execution_symbols: list[str] = Field(default_factory=list)
    active_execution_count: int = 0
    market_data_coverage_count: int = 0
    acceptance_symbols: list[str] = Field(default_factory=list)
    acceptance_scope_hash: str | None = None
    unmanaged_external_symbols: list[str] = Field(default_factory=list)
    last_strategy_gateway_order_at: datetime | None = None
    last_strategy_gateway_order_id: str | None = None
    backend_build_id: str = "development"
    supported_modes: list[str] = Field(default_factory=lambda: ["paper", "testnet"])
    scheduler_mode: str = "disabled"
    scheduler_running: bool = False
    last_auto_cycle_at: datetime | None = None
    next_cycle_eta_seconds: int | None = None
    scheduler_error: str | None = None
    task_run_counts: dict[str, int] = Field(default_factory=dict)
    task_failure_counts: dict[str, int] = Field(default_factory=dict)
    task_last_results: dict[str, Any] = Field(default_factory=dict)
    task_last_success_at: dict[str, datetime] = Field(default_factory=dict)
    task_last_failure_at: dict[str, datetime] = Field(default_factory=dict)
    top20_coverage_count: int = 0
    queue_backlog_status: str = "not_probed"
    live_feed_status: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class BinanceTestnetPositionView(PlatformModel):
    symbol: str
    side: str
    quantity: float
    entry_price: float
    mark_price: float = 0.0
    notional_usdt: float = 0.0
    margin_usdt: float = 0.0
    leverage: float = 0.0
    unrealized_pnl: float = 0.0
    liquidation_price: float | None = None


class BinanceTestnetOrderView(PlatformModel):
    order_id: str
    symbol: str
    side: str
    order_type: str
    status: str
    quantity: float
    avg_price: float | None = None
    reduce_only: bool = False
    update_time: int | None = None
    updated_at: datetime | None = None

    @model_validator(mode="after")
    def normalize_exchange_update_time(self) -> BinanceTestnetOrderView:
        if self.updated_at is None and self.update_time is not None:
            self.updated_at = datetime.fromtimestamp(self.update_time / 1000, tz=UTC)
        return self


class BinanceTestnetAccountStatus(PlatformModel):
    """Live probe of Binance paper trading via API (not the geo-blocked web UI)."""

    connected: bool = False
    trading_mode: str = "demo"
    api_base: str = "https://demo-fapi.binance.com"
    wallet_balance: float | None = None
    available_balance: float | None = None
    unrealized_pnl: float | None = None
    open_position_count: int = 0
    positions: list[BinanceTestnetPositionView] = Field(default_factory=list)
    open_orders: list[BinanceTestnetOrderView] = Field(default_factory=list)
    recent_orders: list[BinanceTestnetOrderView] = Field(default_factory=list)
    web_ui_url: str = "https://demo.binance.com/en/futures/BTCUSDT"
    api_backend: str | None = None
    synced_at: datetime | None = None
    warning: str | None = None
    error: str | None = None


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
