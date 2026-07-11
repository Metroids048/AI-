"""Workflow and lifecycle contracts beyond the core strategy asset."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field, model_validator

from .backtest import BacktestReport, GateDecision
from .base import PlatformModel
from .enums import Exchange, OrderType, TradeSide
from .signal import DecisionVetoResult


class BacktestRun(PlatformModel):
    backtest_run_id: str | None = None
    strategy_id: str
    version_id: str | None = None
    dataset_scope: str | None = None
    execution_engine: str = Field(examples=["freqtrade", "vectorbt"])
    parameter_set: dict[str, Any] = Field(default_factory=dict)
    market_regime_coverage: list[str] = Field(default_factory=list)
    sample_split_plan: dict[str, Any] = Field(default_factory=dict)
    cost_model_ref: str | None = None
    validation_methodology: dict[str, Any] = Field(default_factory=dict)
    stress_test_scenarios: list[str] = Field(default_factory=list)
    metrics_summary: BacktestReport | None = None
    run_status: str = "queued"
    eligibility_result: GateDecision | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CarryBacktestRequest(PlatformModel):
    strategy_id: str
    version_id: str | None = None
    spot_symbol: str
    perp_symbol: str
    timeframe: str = Field(default="1h")
    start_at: datetime
    end_at: datetime


class BacktestSubmissionRequest(PlatformModel):
    strategy_id: str
    version_id: str | None = None
    execution_engine: str = "freqtrade"
    parameter_set: dict[str, Any] = Field(default_factory=dict)
    market_regime_coverage: list[str] = Field(default_factory=list)
    sample_split_plan: dict[str, Any] = Field(default_factory=dict)
    cost_model_ref: str | None = None
    validation_methodology: dict[str, Any] = Field(default_factory=dict)
    stress_test_scenarios: list[str] = Field(default_factory=list)
    idempotency_key: str | None = None


class OptimizationRun(PlatformModel):
    optimization_run_id: str | None = None
    strategy_id: str
    version_id: str | None = None
    search_space_ref: str | None = None
    optimization_method: str = Field(default="hyperopt")
    best_candidate_summary: dict[str, Any] = Field(default_factory=dict)
    run_status: str = "queued"
    created_at: datetime | None = None


class OptimizationSubmissionRequest(PlatformModel):
    strategy_id: str
    version_id: str | None = None
    search_space_ref: str | None = None
    optimization_method: str = Field(default="hyperopt")
    idempotency_key: str | None = None


class PaperRun(PlatformModel):
    paper_run_id: str | None = None
    strategy_id: str
    version_id: str | None = None
    exchange: Exchange = Exchange.BINANCE
    symbol_scope: list[str] = Field(default_factory=lambda: ["BTC/USDT"])
    candidate_symbols: list[str] = Field(default_factory=lambda: ["BTC/USDT", "ETH/USDT"])
    selection_basis: str = Field(default="binance_top20_quote_volume")
    run_window: dict[str, Any] = Field(default_factory=dict)
    execution_profile: dict[str, Any] = Field(default_factory=dict)
    gate_decision_ref: str | None = None
    paper_metrics_summary: dict[str, Any] = Field(default_factory=dict)
    paper_status: str = "queued"
    created_at: datetime | None = None


class PaperRunRequest(PlatformModel):
    strategy_id: str
    version_id: str | None = None
    exchange: Exchange = Exchange.BINANCE
    symbol_scope: list[str] = Field(default_factory=list)
    candidate_symbols: list[str] = Field(default_factory=list)
    selection_basis: str | None = None
    run_window: dict[str, Any] = Field(default_factory=dict)
    execution_profile: dict[str, Any] = Field(default_factory=dict)
    gate_decision_ref: str | None = None
    idempotency_key: str | None = None


class PaperRunStatusUpdate(PlatformModel):
    paper_status: str


class PaperRunStepRequest(PlatformModel):
    symbol: str | None = None
    timeframe: str = "1h"
    perp_symbol: str | None = None
    enable_decision_veto: bool = True
    idempotency_key: str | None = None


class PaperRuntimeCycleRequest(PlatformModel):
    symbols: list[str] = Field(default_factory=list)
    timeframe: str = "1h"
    max_symbols: int = Field(default=20, ge=1, le=50)
    close_on_opposite_signal: bool = True
    enable_decision_veto: bool = True


class AssetRiskTierSettings(PlatformModel):
    tier: str
    symbols: list[str] = Field(default_factory=list)
    leverage: float = Field(ge=1, le=125)
    max_position_fraction: float = Field(gt=0, le=1)


class AutoTradingSettings(PlatformModel):
    """Typed operator-editable automatic trading controls for a PaperRun."""

    execution_mode: str = Field(default="binance_simulation_first", pattern="^(paper_only|binance_simulation_first)$")
    max_leverage: float = Field(default=10.0, ge=1, le=125)
    risk_per_trade: float = Field(default=0.01, ge=0, le=0.10)
    order_notional_usdt: float | None = Field(default=None, gt=0)
    max_open_positions: int = Field(default=5, ge=1, le=20)
    max_symbols: int = Field(default=20, ge=1, le=20)
    max_symbol_exposure: float = Field(default=0.15, ge=0, le=1)
    max_total_exposure: float = Field(default=0.50, ge=0, le=1)
    daily_loss_limit: float = Field(default=0.04, ge=0, le=1)
    weekly_loss_limit: float = Field(default=0.08, ge=0, le=1)
    hard_stop_drawdown_limit: float = Field(default=0.15, ge=0, le=1)
    asset_risk_tiers: dict[str, AssetRiskTierSettings] = Field(
        default_factory=lambda: {
            "core": AssetRiskTierSettings(
                tier="core",
                symbols=["BTC/USDT", "ETH/USDT", "SOL/USDT"],
                leverage=10,
                max_position_fraction=0.15,
            ),
            "standard": AssetRiskTierSettings(
                tier="standard",
                leverage=5,
                max_position_fraction=0.06,
            ),
        }
    )
    strategy_lanes: list[str] = Field(default_factory=lambda: ["carry", "trend_breakout", "mean_reversion"])
    stoploss: dict[str, Any] = Field(default_factory=lambda: {"atr_multiple": 2.0, "fixed_bps": 250})
    takeprofit: dict[str, Any] = Field(default_factory=lambda: {"risk_reward": 2.5, "trail_after_r": 1.5})
    llm_veto_enabled: bool = True
    market_intelligence_enabled: bool = True


class TestnetAcceptanceRunRequest(PlatformModel):
    symbols: list[str] = Field(default_factory=list, max_length=20)
    stoploss_bps: float = Field(default=250, gt=0, le=1_000)
    asset_risk_tiers: dict[str, AssetRiskTierSettings] = Field(default_factory=dict)
    idempotency_key: str | None = None


class TestnetAcceptanceOrderEvidence(PlatformModel):
    gateway_order_id: str
    gateway_status: str
    symbol: str
    side: str
    action: str
    quantity: float
    requested_notional: float
    leverage: float
    reduce_only: bool = False


class TestnetAcceptanceRunResult(PlatformModel):
    run_status: str
    requested_symbols: list[str] = Field(default_factory=list)
    completed_symbols: list[str] = Field(default_factory=list)
    failed_symbol: str | None = None
    filled_order_count: int = 0
    orders: list[TestnetAcceptanceOrderEvidence] = Field(default_factory=list)
    compensation_attempted: bool = False
    final_open_position_count: int = 0
    final_open_order_count: int = 0
    error_summary: str | None = None


class TestnetAcceptanceRunStatus(PlatformModel):
    run_id: str
    run_status: str
    result: TestnetAcceptanceRunResult | None = None
    error_summary: str | None = None


class CarryExecutionRequest(PlatformModel):
    symbol: str = "BTC/USDT"
    perp_symbol: str = "BTC/USDT:USDT"
    notional_usdt: float = Field(default=1_000, gt=0)
    timeframe: str = "1h"
    min_net_edge_bps: float = Field(default=10, ge=0)
    close_immediately: bool = True
    idempotency_key: str | None = None


class CarryExecutionLegResult(PlatformModel):
    venue: str
    gateway_order_id: str
    gateway_status: str
    symbol: str
    side: str
    quantity: float
    notional_usdt: float
    reduce_only: bool = False


class CarryExecutionStatus(PlatformModel):
    run_id: str
    run_status: str
    carry_state: str
    state_history: list[str] = Field(default_factory=list)
    signal: Any | None = None
    legs: list[CarryExecutionLegResult] = Field(default_factory=list)
    final_net_exposure_usdt: float = 0.0
    error_summary: str | None = None


class PaperRuntimeAction(PlatformModel):
    symbol: str
    action: str
    direction: TradeSide | None = None
    reason: str | None = None
    order_execution_id: str | None = None
    reference_price: float | None = None
    close_only: bool = False
    idempotency_key: str | None = None
    decision_trace: dict[str, Any] = Field(default_factory=dict)


class PaperRuntimeCycleResult(PlatformModel):
    paper_run_id: str
    paper_status: str
    cycle_time: datetime
    scanned_symbols: list[str] = Field(default_factory=list)
    actions: list[PaperRuntimeAction] = Field(default_factory=list)
    opened_positions: int = 0
    closed_positions: int = 0
    rejected_orders: int = 0
    skipped_symbols: int = 0
    open_position_symbols: list[str] = Field(default_factory=list)
    account_equity: float = 0.0


class PaperRuntimeStatus(PlatformModel):
    paper_run_id: str
    paper_status: str
    candidate_symbols: list[str] = Field(default_factory=list)
    open_position_symbols: list[str] = Field(default_factory=list)
    account_equity: float = 0.0
    last_cycle_at: datetime | None = None
    last_scanned_symbols: list[str] = Field(default_factory=list)
    last_action_counts: dict[str, int] = Field(default_factory=dict)
    last_cycle_decisions: list[dict[str, Any]] = Field(default_factory=list)


class LiveRun(PlatformModel):
    live_run_id: str | None = None
    strategy_id: str
    version_id: str | None = None
    exchange: Exchange = Exchange.BINANCE
    capital_tier: str = Field(default="micro")
    live_status: str = "queued"
    validation_backtest_run_id: str | None = None
    risk_profile_ref: str | None = None
    live_metrics_summary: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None


class LiveRunRequest(PlatformModel):
    strategy_id: str
    version_id: str | None = None
    exchange: Exchange = Exchange.BINANCE
    capital_tier: str = Field(default="micro")
    validation_backtest_run_id: str | None = None
    risk_profile_ref: str | None = None
    idempotency_key: str | None = None


class ExecutionSignal(PlatformModel):
    signal_id: str | None = None
    strategy_id: str
    version_id: str | None = None
    signal_time: datetime | None = None
    symbol: str
    direction: TradeSide
    entry_context: dict[str, Any] = Field(default_factory=dict)
    stoploss_plan: dict[str, Any] = Field(default_factory=dict)
    takeprofit_plan: dict[str, Any] = Field(default_factory=dict)
    signal_ensemble_id: str | None = None
    meta_label_id: str | None = None
    veto_result: DecisionVetoResult | None = None
    stoploss_present: bool = False
    signal_status: str = "pending_prechecks"


class ExecutionRiskState(PlatformModel):
    """Runtime risk snapshot supplied to the gatekeeper at order-admission time."""

    account_equity: float
    equity_peak: float
    daily_realized_pnl: float = 0.0
    weekly_realized_pnl: float = 0.0
    consecutive_losses: int = 0
    api_failures_window: int = 0
    open_positions: int = 0
    symbol_exposure: float = 0.0
    total_exposure: float = 0.0
    requested_notional: float = 0.0
    requested_leverage: float = 1.0


class ExecutionOrderRequest(PlatformModel):
    strategy_id: str = Field(min_length=1)
    version_id: str | None = None
    symbol: str
    direction: TradeSide
    entry_context: dict[str, Any] = Field(default_factory=dict)
    stoploss_plan: dict[str, Any] = Field(default_factory=dict)
    takeprofit_plan: dict[str, Any] = Field(default_factory=dict)
    signal_ensemble_id: str | None = None
    meta_label_id: str | None = None
    validation_backtest_run_id: str | None = None
    risk_profile_id: str | None = None
    paper_run_id: str | None = None
    live_run_id: str | None = None
    veto_result: DecisionVetoResult | None = None
    risk_state: ExecutionRiskState | None = None
    idempotency_key: str | None = None


class ManualOrderRequest(PlatformModel):
    mode: str = Field(default="paper", pattern="^(paper|testnet)$")
    strategy_id: str = Field(min_length=1)
    version_id: str | None = None
    validation_backtest_run_id: str = Field(min_length=1)
    risk_profile_id: str | None = None
    live_run_id: str | None = None
    paper_run_id: str | None = None
    symbol: str
    direction: TradeSide
    quantity: float = Field(gt=0)
    reference_price: float = Field(gt=0)
    leverage: float = Field(default=1.0, ge=1)
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    time_in_force: str = "GTC"
    timeframe: str = "1h"
    stoploss_price: float | None = None
    takeprofit_price: float | None = None
    account_equity: float = Field(default=10_000.0, gt=0)
    idempotency_key: str | None = None


class ManualTradingContext(PlatformModel):
    """Paper-only audit evidence used by the exchange-style manual ticket."""

    mode: str = "paper"
    context_key: str = "manual_paper_sandbox"
    strategy_id: str
    validation_backtest_run_id: str
    paper_run_id: str | None = None
    evidence_status: str = "ready"
    warning: str = "Paper-only sandbox evidence; not eligible for Testnet or Live promotion."


class ClosePositionRequest(PlatformModel):
    mode: str = Field(default="paper", pattern="^(paper|testnet)$")
    strategy_id: str = Field(min_length=1)
    version_id: str | None = None
    validation_backtest_run_id: str = Field(min_length=1)
    risk_profile_id: str | None = None
    live_run_id: str | None = None
    paper_run_id: str | None = None
    symbol: str
    reference_price: float = Field(gt=0)
    timeframe: str = "1h"
    account_equity: float = Field(default=10_000.0, gt=0)
    idempotency_key: str | None = None


class AdjustLeverageRequest(PlatformModel):
    mode: str = Field(default="paper", pattern="^(paper|testnet)$")
    strategy_id: str = Field(min_length=1)
    live_run_id: str | None = None
    symbol: str
    leverage: float = Field(ge=1)


class CancelOrderRequest(PlatformModel):
    mode: str = Field(default="paper", pattern="^(paper|testnet)$")
    order_execution_id: str


class LeverageAdjustmentResult(PlatformModel):
    mode: str
    symbol: str
    leverage: float
    gateway_name: str
    gateway_status: str
    detail: dict[str, Any] = Field(default_factory=dict)


class OrderExecution(PlatformModel):
    order_execution_id: str | None = None
    strategy_id: str
    version_id: str | None = None
    symbol: str
    direction: TradeSide
    execution_status: str = "queued"
    stoploss_present: bool = False
    close_only_mode: bool = False
    rejection_reason: str | None = None
    rejection_codes: list[str] = Field(default_factory=list)
    entry_context: dict[str, Any] = Field(default_factory=dict)
    stoploss_plan: dict[str, Any] = Field(default_factory=dict)
    takeprofit_plan: dict[str, Any] = Field(default_factory=dict)
    risk_profile_ref: str | None = None
    validation_backtest_run_id: str | None = None
    paper_run_id: str | None = None
    live_run_id: str | None = None
    signal_ensemble_id: str | None = None
    meta_label_id: str | None = None
    veto_result: dict[str, Any] = Field(default_factory=dict)
    evaluated_risk_state: ExecutionRiskState | None = None
    gateway_name: str | None = None
    gateway_order_id: str | None = None
    gateway_status: str | None = None
    lifecycle_history: list[dict[str, Any]] = Field(default_factory=list)
    reconciliation_status: str | None = None
    last_gateway_update_at: datetime | None = None
    created_at: datetime | None = None


class PositionSnapshot(PlatformModel):
    position_snapshot_id: str | None = None
    run_type: str = Field(examples=["paper", "live"])
    run_id: str
    symbol: str
    side: TradeSide
    quantity: float
    entry_price: float
    mark_price: float
    unrealized_pnl: float = 0.0
    snapshot_time: datetime


class ReviewReport(PlatformModel):
    review_report_id: str | None = None
    report_date: str = Field(examples=["2026-07-02"])
    scope_type: str = Field(default="daily")
    strategy_refs: list[str] = Field(default_factory=list)
    worst_performer_refs: list[str] = Field(default_factory=list)
    failure_patterns: list[str] = Field(default_factory=list)
    deviation_analysis: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    report_status: str = "draft"
    created_at: datetime | None = None


class FailureRecord(PlatformModel):
    failure_record_id: str | None = None
    strategy_id: str | None = None
    idea_id: str | None = None
    version_id: str | None = None
    origin_run_type: str
    origin_run_id: str
    failure_type: str
    failure_summary: str
    evidence_refs: list[str] = Field(default_factory=list)
    recommended_change: str | None = None
    created_at: datetime | None = None

    @model_validator(mode="after")
    def validate_subject_ref(self) -> FailureRecord:
        if not self.strategy_id and not self.idea_id:
            raise ValueError("failure record requires strategy_id or idea_id")
        return self


class IngestionJob(PlatformModel):
    ingestion_job_id: str | None = None
    source_family: str = Field(examples=["A", "B", "C", "D", "E"])
    source_name: str
    job_type: str
    schedule_mode: str
    job_status: str = "pending"
    input_window: dict[str, Any] = Field(default_factory=dict)
    target_symbols: list[str] = Field(default_factory=list)
    output_ref: str | None = None
    error_summary: str | None = None
    execution_summary: dict[str, Any] = Field(default_factory=dict)
    data_quality_summary: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class IngestionJobRequest(PlatformModel):
    source_family: str = Field(examples=["A", "B", "C", "D", "E"])
    source_name: str
    job_type: str
    schedule_mode: str
    input_window: dict[str, Any] = Field(default_factory=dict)
    target_symbols: list[str] = Field(default_factory=list)
    idempotency_key: str | None = None


class AgentTask(PlatformModel):
    agent_task_id: str | None = None
    agent_type: str = Field(examples=["strategy_agent", "decision_veto_agent"])
    task_type: str
    input_ref: str | None = None
    output_ref: str | None = None
    input_payload: dict[str, Any] = Field(default_factory=dict)
    output_payload: dict[str, Any] = Field(default_factory=dict)
    priority: int = 5
    task_status: str = "queued"
    error_summary: str | None = None
    executor_name: str | None = None
    attempt_history: list[dict[str, Any]] = Field(default_factory=list)
    provider_trace: dict[str, Any] = Field(default_factory=dict)
    schema_validation_status: str | None = None
    scheduled_at: datetime | None = None
    created_at: datetime | None = None


class AgentTaskRequest(PlatformModel):
    agent_type: str
    task_type: str
    input_ref: str | None = None
    input_payload: dict[str, Any] = Field(default_factory=dict)
    priority: int = 5
    idempotency_key: str | None = None


class NotificationOutboxItem(PlatformModel):
    """Structured notification intent plus persisted adapter-delivery state."""

    notification_id: str
    event_type: str
    severity: str
    channel_group: str = "ops"
    delivery_channels: list[str] = Field(default_factory=lambda: ["telegram", "webhook"])
    subject: str
    body: str
    source_ref: str | None = None
    delivery_status: str = "pending_adapter"
    delivery_attempts: int = Field(default=0, ge=0)
    next_attempt_at: datetime | None = None
    last_attempt_at: datetime | None = None
    attempt_history: list[dict[str, Any]] = Field(default_factory=list)
    last_error: str | None = None
    delivered_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class NotificationDeliveryUpdate(PlatformModel):
    """Record delivery-adapter results without performing external side effects."""

    delivery_status: str
    last_error: str | None = None
    next_attempt_at: datetime | None = None
