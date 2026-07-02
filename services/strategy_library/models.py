"""SQLAlchemy ORM for the first core vertical slice.

These models persist the strategy-intake chain plus the first validation/data
bookkeeping objects:

    StrategyIdea -> StrategyDraft -> Strategy -> StrategyVersion -> BacktestRun
                                                 \-> IngestionJob

Cross-layer contracts still live in `shared.models`. The ORM here remains a
storage detail owned by Alembic for relational tables only.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, JSON, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for all relational (Alembic-owned) tables."""


def _uuid_str() -> str:
    return str(uuid.uuid4())


class _RulesColumns:
    """Shared structured rule blocks stored as JSON."""

    entry_rules: Mapped[dict] = mapped_column(JSON, default=dict)
    exit_rules: Mapped[dict] = mapped_column(JSON, default=dict)
    stoploss_rules: Mapped[dict] = mapped_column(JSON, default=dict)
    takeprofit_rules: Mapped[dict] = mapped_column(JSON, default=dict)
    position_rules: Mapped[dict] = mapped_column(JSON, default=dict)


class StrategyIdea(Base):
    __tablename__ = "strategy_ideas"

    idea_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    title: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(60))
    market: Mapped[str] = mapped_column(String(30), default="crypto_perp")
    symbol_scope: Mapped[list[str]] = mapped_column(JSON, default=list)
    hypothesis_summary: Mapped[str] = mapped_column(Text)
    source_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    intake_bucket: Mapped[str] = mapped_column(String(40), default="rule_candidate")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class StrategyDraft(Base, _RulesColumns):
    __tablename__ = "strategy_drafts"

    draft_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    idea_id: Mapped[str | None] = mapped_column(
        ForeignKey("strategy_ideas.idea_id"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(60))
    core_thesis: Mapped[str] = mapped_column(Text)
    market: Mapped[str] = mapped_column(String(30), default="crypto_perp")
    symbol_scope: Mapped[list[str]] = mapped_column(JSON, default=list)
    timeframe: Mapped[str] = mapped_column(String(10), default="1h")
    market_regime: Mapped[str | None] = mapped_column(String(60), nullable=True)
    risk_level: Mapped[str] = mapped_column(String(20), default="medium")
    draft_status: Mapped[str] = mapped_column(String(30), default="drafting")
    review_notes: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )


class Strategy(Base):
    __tablename__ = "strategies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    strategy_key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    source: Mapped[str] = mapped_column(String(60))

    core_thesis: Mapped[str] = mapped_column(Text, default="")
    market: Mapped[str] = mapped_column(String(30), default="crypto_perp")
    symbol_scope: Mapped[list[str]] = mapped_column(JSON, default=list)
    timeframe: Mapped[str] = mapped_column(String(10), default="1h")
    market_regime: Mapped[str | None] = mapped_column(String(60), nullable=True)
    risk_level: Mapped[str] = mapped_column(String(20), default="medium")

    entry_rules: Mapped[dict] = mapped_column(JSON, default=dict)
    exit_rules: Mapped[dict] = mapped_column(JSON, default=dict)
    stoploss_rules: Mapped[dict] = mapped_column(JSON, default=dict)
    takeprofit_rules: Mapped[dict] = mapped_column(JSON, default=dict)
    position_rules: Mapped[dict] = mapped_column(JSON, default=dict)

    strategy_status: Mapped[str] = mapped_column(String(30), default="drafting")
    backtest_status: Mapped[str] = mapped_column(String(20), default="not_started")
    paper_status: Mapped[str] = mapped_column(String(20), default="not_started")
    live_status: Mapped[str] = mapped_column(String(20), default="not_started")

    failure_reasons: Mapped[list] = mapped_column(JSON, default=list)
    iteration_history: Mapped[list] = mapped_column(JSON, default=list)

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )


class StrategyVersion(Base):
    __tablename__ = "strategy_versions"

    version_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategies.id"), index=True)
    version_label: Mapped[str] = mapped_column(String(40))
    change_summary: Mapped[str] = mapped_column(Text)
    code_artifact_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    backtest_run_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategies.id"), index=True)
    version_id: Mapped[str | None] = mapped_column(
        ForeignKey("strategy_versions.version_id"), nullable=True, index=True
    )
    dataset_scope: Mapped[str | None] = mapped_column(String(255), nullable=True)
    execution_engine: Mapped[str] = mapped_column(String(30))
    parameter_set: Mapped[dict] = mapped_column(JSON, default=dict)
    market_regime_coverage: Mapped[list[str]] = mapped_column(JSON, default=list)
    sample_split_plan: Mapped[dict] = mapped_column(JSON, default=dict)
    cost_model_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    validation_methodology: Mapped[dict] = mapped_column(JSON, default=dict)
    stress_test_scenarios: Mapped[list[str]] = mapped_column(JSON, default=list)
    metrics_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    run_status: Mapped[str] = mapped_column(String(30), default="queued")
    eligibility_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"

    ingestion_job_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_uuid_str
    )
    source_family: Mapped[str] = mapped_column(String(10))
    source_name: Mapped[str] = mapped_column(String(80))
    job_type: Mapped[str] = mapped_column(String(80))
    schedule_mode: Mapped[str] = mapped_column(String(40))
    job_status: Mapped[str] = mapped_column(String(30), default="pending")
    input_window: Mapped[dict] = mapped_column(JSON, default=dict)
    target_symbols: Mapped[list[str]] = mapped_column(JSON, default=list)
    output_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )


class PaperRun(Base):
    __tablename__ = "paper_runs"

    paper_run_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategies.id"), index=True)
    version_id: Mapped[str | None] = mapped_column(
        ForeignKey("strategy_versions.version_id"), nullable=True, index=True
    )
    exchange: Mapped[str] = mapped_column(String(20), default="binance")
    symbol_scope: Mapped[list[str]] = mapped_column(JSON, default=list)
    candidate_symbols: Mapped[list[str]] = mapped_column(JSON, default=list)
    selection_basis: Mapped[str] = mapped_column(
        String(80), default="binance_top20_quote_volume"
    )
    run_window: Mapped[dict] = mapped_column(JSON, default=dict)
    execution_profile: Mapped[dict] = mapped_column(JSON, default=dict)
    gate_decision_ref: Mapped[str | None] = mapped_column(String(36), nullable=True)
    paper_metrics_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    paper_status: Mapped[str] = mapped_column(String(30), default="queued")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
