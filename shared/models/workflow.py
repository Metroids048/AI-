"""Workflow and lifecycle contracts beyond the core strategy asset."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field, model_validator

from .backtest import BacktestReport, GateDecision
from .base import PlatformModel
from .enums import Exchange, TradeSide
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
    strategy_id: str
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
