"""expand research loop persistence for risk, review, agent, and execution

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("ingestion_jobs") as batch_op:
        batch_op.add_column(sa.Column("execution_summary", sa.JSON(), nullable=False, server_default="{}"))

    op.create_table(
        "optimization_runs",
        sa.Column("optimization_run_id", sa.String(length=36), primary_key=True),
        sa.Column("strategy_id", sa.String(length=36), sa.ForeignKey("strategies.id"), nullable=False),
        sa.Column("version_id", sa.String(length=36), sa.ForeignKey("strategy_versions.version_id"), nullable=True),
        sa.Column("search_space_ref", sa.String(length=255), nullable=True),
        sa.Column("optimization_method", sa.String(length=40), nullable=False, server_default="hyperopt"),
        sa.Column("best_candidate_summary", sa.JSON(), nullable=False),
        sa.Column("run_status", sa.String(length=30), nullable=False, server_default="queued"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_optimization_runs_strategy_id", "optimization_runs", ["strategy_id"], unique=False)
    op.create_index("ix_optimization_runs_version_id", "optimization_runs", ["version_id"], unique=False)

    op.create_table(
        "risk_profiles",
        sa.Column("risk_profile_id", sa.String(length=36), primary_key=True),
        sa.Column("single_trade_risk_limit", sa.Float(), nullable=False, server_default="0.01"),
        sa.Column("max_symbol_exposure", sa.Float(), nullable=False, server_default="0.20"),
        sa.Column("max_total_exposure", sa.Float(), nullable=False, server_default="0.60"),
        sa.Column("max_open_positions", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("max_leverage", sa.Float(), nullable=False, server_default="3.0"),
        sa.Column("daily_loss_limit", sa.Float(), nullable=False, server_default="0.03"),
        sa.Column("weekly_loss_limit", sa.Float(), nullable=False, server_default="0.08"),
        sa.Column("drawdown_limit", sa.Float(), nullable=False, server_default="0.10"),
        sa.Column("hard_stop_drawdown_limit", sa.Float(), nullable=False, server_default="0.20"),
        sa.Column("market_scope", sa.String(length=120), nullable=False, server_default="BTC/USDT perpetual"),
        sa.Column(
            "config_source",
            sa.String(length=255),
            nullable=False,
            server_default="risk-control-and-safeguards-plan.md section 4",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "review_reports",
        sa.Column("review_report_id", sa.String(length=36), primary_key=True),
        sa.Column("report_date", sa.String(length=20), nullable=False),
        sa.Column("scope_type", sa.String(length=40), nullable=False, server_default="daily"),
        sa.Column("strategy_refs", sa.JSON(), nullable=False),
        sa.Column("worst_performer_refs", sa.JSON(), nullable=False),
        sa.Column("failure_patterns", sa.JSON(), nullable=False),
        sa.Column("deviation_analysis", sa.JSON(), nullable=False),
        sa.Column("recommendations", sa.JSON(), nullable=False),
        sa.Column("report_status", sa.String(length=30), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "failure_records",
        sa.Column("failure_record_id", sa.String(length=36), primary_key=True),
        sa.Column("strategy_id", sa.String(length=36), sa.ForeignKey("strategies.id"), nullable=False),
        sa.Column("version_id", sa.String(length=36), sa.ForeignKey("strategy_versions.version_id"), nullable=True),
        sa.Column("origin_run_type", sa.String(length=40), nullable=False),
        sa.Column("origin_run_id", sa.String(length=36), nullable=False),
        sa.Column("failure_type", sa.String(length=60), nullable=False),
        sa.Column("failure_summary", sa.Text(), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("recommended_change", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_failure_records_strategy_id", "failure_records", ["strategy_id"], unique=False)
    op.create_index("ix_failure_records_version_id", "failure_records", ["version_id"], unique=False)

    op.create_table(
        "agent_tasks",
        sa.Column("agent_task_id", sa.String(length=36), primary_key=True),
        sa.Column("agent_type", sa.String(length=60), nullable=False),
        sa.Column("task_type", sa.String(length=80), nullable=False),
        sa.Column("input_ref", sa.String(length=255), nullable=True),
        sa.Column("output_ref", sa.String(length=255), nullable=True),
        sa.Column("input_payload", sa.JSON(), nullable=False),
        sa.Column("output_payload", sa.JSON(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("task_status", sa.String(length=30), nullable=False, server_default="queued"),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_agent_tasks_agent_type", "agent_tasks", ["agent_type"], unique=False)

    op.create_table(
        "live_runs",
        sa.Column("live_run_id", sa.String(length=36), primary_key=True),
        sa.Column("strategy_id", sa.String(length=36), sa.ForeignKey("strategies.id"), nullable=False),
        sa.Column("version_id", sa.String(length=36), sa.ForeignKey("strategy_versions.version_id"), nullable=True),
        sa.Column("exchange", sa.String(length=20), nullable=False, server_default="binance"),
        sa.Column("capital_tier", sa.String(length=30), nullable=False, server_default="micro"),
        sa.Column("live_status", sa.String(length=30), nullable=False, server_default="queued"),
        sa.Column(
            "risk_profile_ref",
            sa.String(length=36),
            sa.ForeignKey("risk_profiles.risk_profile_id"),
            nullable=True,
        ),
        sa.Column("live_metrics_summary", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_live_runs_strategy_id", "live_runs", ["strategy_id"], unique=False)
    op.create_index("ix_live_runs_version_id", "live_runs", ["version_id"], unique=False)

    op.create_table(
        "signal_ensembles",
        sa.Column("ensemble_id", sa.String(length=36), primary_key=True),
        sa.Column("strategy_refs", sa.JSON(), nullable=False),
        sa.Column("fusion_method", sa.String(length=40), nullable=False, server_default="weighted_vote"),
        sa.Column("correlation_matrix_ref", sa.String(length=255), nullable=True),
        sa.Column("raw_votes", sa.JSON(), nullable=False),
        sa.Column("fused_direction", sa.String(length=20), nullable=True),
        sa.Column("fused_confidence", sa.Float(), nullable=True),
        sa.Column("ensemble_status", sa.String(length=40), nullable=False, server_default="formed"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "meta_labels",
        sa.Column("meta_label_id", sa.String(length=36), primary_key=True),
        sa.Column("ensemble_id", sa.String(length=36), sa.ForeignKey("signal_ensembles.ensemble_id"), nullable=False),
        sa.Column("triple_barrier_result", sa.String(length=20), nullable=True),
        sa.Column("bet_decision", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("position_size_fraction", sa.Float(), nullable=True),
        sa.Column("model_ref", sa.String(length=255), nullable=True),
        sa.Column("training_window_ref", sa.String(length=255), nullable=True),
    )
    op.create_index("ix_meta_labels_ensemble_id", "meta_labels", ["ensemble_id"], unique=False)

    op.create_table(
        "order_executions",
        sa.Column("order_execution_id", sa.String(length=36), primary_key=True),
        sa.Column("strategy_id", sa.String(length=36), sa.ForeignKey("strategies.id"), nullable=False),
        sa.Column("version_id", sa.String(length=36), sa.ForeignKey("strategy_versions.version_id"), nullable=True),
        sa.Column("symbol", sa.String(length=30), nullable=False),
        sa.Column("direction", sa.String(length=20), nullable=False),
        sa.Column("execution_status", sa.String(length=30), nullable=False, server_default="queued"),
        sa.Column("stoploss_present", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("close_only_mode", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("entry_context", sa.JSON(), nullable=False),
        sa.Column("stoploss_plan", sa.JSON(), nullable=False),
        sa.Column("takeprofit_plan", sa.JSON(), nullable=False),
        sa.Column(
            "risk_profile_ref",
            sa.String(length=36),
            sa.ForeignKey("risk_profiles.risk_profile_id"),
            nullable=True,
        ),
        sa.Column(
            "validation_backtest_run_id",
            sa.String(length=36),
            sa.ForeignKey("backtest_runs.backtest_run_id"),
            nullable=True,
        ),
        sa.Column("paper_run_id", sa.String(length=36), sa.ForeignKey("paper_runs.paper_run_id"), nullable=True),
        sa.Column("live_run_id", sa.String(length=36), sa.ForeignKey("live_runs.live_run_id"), nullable=True),
        sa.Column(
            "signal_ensemble_id",
            sa.String(length=36),
            sa.ForeignKey("signal_ensembles.ensemble_id"),
            nullable=True,
        ),
        sa.Column("meta_label_id", sa.String(length=36), sa.ForeignKey("meta_labels.meta_label_id"), nullable=True),
        sa.Column("veto_result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_order_executions_strategy_id", "order_executions", ["strategy_id"], unique=False)
    op.create_index("ix_order_executions_version_id", "order_executions", ["version_id"], unique=False)
    op.create_index("ix_order_executions_symbol", "order_executions", ["symbol"], unique=False)

    op.create_table(
        "position_snapshots",
        sa.Column("position_snapshot_id", sa.String(length=36), primary_key=True),
        sa.Column("run_type", sa.String(length=20), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("symbol", sa.String(length=30), nullable=False),
        sa.Column("side", sa.String(length=20), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("entry_price", sa.Float(), nullable=False),
        sa.Column("mark_price", sa.Float(), nullable=False),
        sa.Column("unrealized_pnl", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("snapshot_time", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_position_snapshots_run_id", "position_snapshots", ["run_id"], unique=False)
    op.create_index("ix_position_snapshots_symbol", "position_snapshots", ["symbol"], unique=False)
    op.create_index("ix_position_snapshots_snapshot_time", "position_snapshots", ["snapshot_time"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_position_snapshots_snapshot_time", table_name="position_snapshots")
    op.drop_index("ix_position_snapshots_symbol", table_name="position_snapshots")
    op.drop_index("ix_position_snapshots_run_id", table_name="position_snapshots")
    op.drop_table("position_snapshots")

    op.drop_index("ix_order_executions_symbol", table_name="order_executions")
    op.drop_index("ix_order_executions_version_id", table_name="order_executions")
    op.drop_index("ix_order_executions_strategy_id", table_name="order_executions")
    op.drop_table("order_executions")

    op.drop_index("ix_meta_labels_ensemble_id", table_name="meta_labels")
    op.drop_table("meta_labels")
    op.drop_table("signal_ensembles")

    op.drop_index("ix_live_runs_version_id", table_name="live_runs")
    op.drop_index("ix_live_runs_strategy_id", table_name="live_runs")
    op.drop_table("live_runs")

    op.drop_index("ix_agent_tasks_agent_type", table_name="agent_tasks")
    op.drop_table("agent_tasks")

    op.drop_index("ix_failure_records_version_id", table_name="failure_records")
    op.drop_index("ix_failure_records_strategy_id", table_name="failure_records")
    op.drop_table("failure_records")
    op.drop_table("review_reports")
    op.drop_table("risk_profiles")

    op.drop_index("ix_optimization_runs_version_id", table_name="optimization_runs")
    op.drop_index("ix_optimization_runs_strategy_id", table_name="optimization_runs")
    op.drop_table("optimization_runs")

    with op.batch_alter_table("ingestion_jobs") as batch_op:
        batch_op.drop_column("execution_summary")
