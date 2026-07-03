"""create first persisted vertical-slice relational tables

Revision ID: 0001
Revises:
Create Date: 2026-06-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "strategy_ideas",
        sa.Column("idea_id", sa.String(length=36), primary_key=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("source", sa.String(length=60), nullable=False),
        sa.Column("market", sa.String(length=30), nullable=False, server_default="crypto_perp"),
        sa.Column("symbol_scope", sa.JSON(), nullable=False),
        sa.Column("hypothesis_summary", sa.Text(), nullable=False),
        sa.Column("source_ref", sa.String(length=255), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("intake_bucket", sa.String(length=40), nullable=False, server_default="rule_candidate"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "strategy_drafts",
        sa.Column("draft_id", sa.String(length=36), primary_key=True),
        sa.Column("idea_id", sa.String(length=36), sa.ForeignKey("strategy_ideas.idea_id"), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("source", sa.String(length=60), nullable=False),
        sa.Column("core_thesis", sa.Text(), nullable=False),
        sa.Column("market", sa.String(length=30), nullable=False, server_default="crypto_perp"),
        sa.Column("symbol_scope", sa.JSON(), nullable=False),
        sa.Column("timeframe", sa.String(length=10), nullable=False, server_default="1h"),
        sa.Column("market_regime", sa.String(length=60), nullable=True),
        sa.Column("risk_level", sa.String(length=20), nullable=False, server_default="medium"),
        sa.Column("draft_status", sa.String(length=30), nullable=False, server_default="drafting"),
        sa.Column("review_notes", sa.JSON(), nullable=False),
        sa.Column("entry_rules", sa.JSON(), nullable=False),
        sa.Column("exit_rules", sa.JSON(), nullable=False),
        sa.Column("stoploss_rules", sa.JSON(), nullable=False),
        sa.Column("takeprofit_rules", sa.JSON(), nullable=False),
        sa.Column("position_rules", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_strategy_drafts_idea_id", "strategy_drafts", ["idea_id"], unique=False)

    op.create_table(
        "strategies",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("strategy_key", sa.String(length=120), nullable=False),
        sa.Column("source", sa.String(length=60), nullable=False),
        sa.Column("core_thesis", sa.Text(), nullable=False, server_default=""),
        sa.Column("market", sa.String(length=30), nullable=False, server_default="crypto_perp"),
        sa.Column("symbol_scope", sa.JSON(), nullable=False),
        sa.Column("timeframe", sa.String(length=10), nullable=False, server_default="1h"),
        sa.Column("market_regime", sa.String(length=60), nullable=True),
        sa.Column("risk_level", sa.String(length=20), nullable=False, server_default="medium"),
        sa.Column("entry_rules", sa.JSON(), nullable=False),
        sa.Column("exit_rules", sa.JSON(), nullable=False),
        sa.Column("stoploss_rules", sa.JSON(), nullable=False),
        sa.Column("takeprofit_rules", sa.JSON(), nullable=False),
        sa.Column("position_rules", sa.JSON(), nullable=False),
        sa.Column("strategy_status", sa.String(length=30), nullable=False, server_default="drafting"),
        sa.Column("backtest_status", sa.String(length=20), nullable=False, server_default="not_started"),
        sa.Column("paper_status", sa.String(length=20), nullable=False, server_default="not_started"),
        sa.Column("live_status", sa.String(length=20), nullable=False, server_default="not_started"),
        sa.Column("failure_reasons", sa.JSON(), nullable=False),
        sa.Column("iteration_history", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_strategies_strategy_key", "strategies", ["strategy_key"], unique=True)

    op.create_table(
        "strategy_versions",
        sa.Column("version_id", sa.String(length=36), primary_key=True),
        sa.Column("strategy_id", sa.String(length=36), sa.ForeignKey("strategies.id"), nullable=False),
        sa.Column("version_label", sa.String(length=40), nullable=False),
        sa.Column("change_summary", sa.Text(), nullable=False),
        sa.Column("code_artifact_ref", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_strategy_versions_strategy_id", "strategy_versions", ["strategy_id"], unique=False)

    op.create_table(
        "backtest_runs",
        sa.Column("backtest_run_id", sa.String(length=36), primary_key=True),
        sa.Column("strategy_id", sa.String(length=36), sa.ForeignKey("strategies.id"), nullable=False),
        sa.Column("version_id", sa.String(length=36), sa.ForeignKey("strategy_versions.version_id"), nullable=True),
        sa.Column("dataset_scope", sa.String(length=255), nullable=True),
        sa.Column("execution_engine", sa.String(length=30), nullable=False),
        sa.Column("parameter_set", sa.JSON(), nullable=False),
        sa.Column("market_regime_coverage", sa.JSON(), nullable=False),
        sa.Column("sample_split_plan", sa.JSON(), nullable=False),
        sa.Column("cost_model_ref", sa.String(length=255), nullable=True),
        sa.Column("validation_methodology", sa.JSON(), nullable=False),
        sa.Column("stress_test_scenarios", sa.JSON(), nullable=False),
        sa.Column("metrics_summary", sa.JSON(), nullable=True),
        sa.Column("run_status", sa.String(length=30), nullable=False, server_default="queued"),
        sa.Column("eligibility_result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_backtest_runs_strategy_id", "backtest_runs", ["strategy_id"], unique=False)
    op.create_index("ix_backtest_runs_version_id", "backtest_runs", ["version_id"], unique=False)

    op.create_table(
        "ingestion_jobs",
        sa.Column("ingestion_job_id", sa.String(length=36), primary_key=True),
        sa.Column("source_family", sa.String(length=10), nullable=False),
        sa.Column("source_name", sa.String(length=80), nullable=False),
        sa.Column("job_type", sa.String(length=80), nullable=False),
        sa.Column("schedule_mode", sa.String(length=40), nullable=False),
        sa.Column("job_status", sa.String(length=30), nullable=False, server_default="pending"),
        sa.Column("input_window", sa.JSON(), nullable=False),
        sa.Column("target_symbols", sa.JSON(), nullable=False),
        sa.Column("output_ref", sa.String(length=255), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "paper_runs",
        sa.Column("paper_run_id", sa.String(length=36), primary_key=True),
        sa.Column("strategy_id", sa.String(length=36), sa.ForeignKey("strategies.id"), nullable=False),
        sa.Column("version_id", sa.String(length=36), sa.ForeignKey("strategy_versions.version_id"), nullable=True),
        sa.Column("exchange", sa.String(length=20), nullable=False, server_default="binance"),
        sa.Column("symbol_scope", sa.JSON(), nullable=False),
        sa.Column("candidate_symbols", sa.JSON(), nullable=False),
        sa.Column("selection_basis", sa.String(length=80), nullable=False, server_default="binance_top20_quote_volume"),
        sa.Column("run_window", sa.JSON(), nullable=False),
        sa.Column("execution_profile", sa.JSON(), nullable=False),
        sa.Column("gate_decision_ref", sa.String(length=36), nullable=True),
        sa.Column("paper_metrics_summary", sa.JSON(), nullable=False),
        sa.Column("paper_status", sa.String(length=30), nullable=False, server_default="queued"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_paper_runs_strategy_id", "paper_runs", ["strategy_id"], unique=False)
    op.create_index("ix_paper_runs_version_id", "paper_runs", ["version_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_paper_runs_version_id", table_name="paper_runs")
    op.drop_index("ix_paper_runs_strategy_id", table_name="paper_runs")
    op.drop_table("paper_runs")
    op.drop_table("ingestion_jobs")
    op.drop_index("ix_backtest_runs_version_id", table_name="backtest_runs")
    op.drop_index("ix_backtest_runs_strategy_id", table_name="backtest_runs")
    op.drop_table("backtest_runs")
    op.drop_index("ix_strategy_versions_strategy_id", table_name="strategy_versions")
    op.drop_table("strategy_versions")
    op.drop_index("ix_strategies_strategy_key", table_name="strategies")
    op.drop_table("strategies")
    op.drop_index("ix_strategy_drafts_idea_id", table_name="strategy_drafts")
    op.drop_table("strategy_drafts")
    op.drop_table("strategy_ideas")
