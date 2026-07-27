"""Add exchange-first order and fill truth tables.

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _column_names(bind, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "exchange_orders" not in tables:
        op.create_table(
            "exchange_orders",
            sa.Column("exchange_order_record_id", sa.String(36), primary_key=True),
            sa.Column(
                "local_order_execution_id",
                sa.String(36),
                sa.ForeignKey("order_executions.order_execution_id"),
                nullable=False,
            ),
            sa.Column("exchange_account", sa.String(120), nullable=False),
            sa.Column("execution_mode", sa.String(30), nullable=False),
            sa.Column("client_order_id", sa.String(36), nullable=False),
            sa.Column("exchange_order_id", sa.String(120), nullable=True),
            sa.Column("symbol", sa.String(30), nullable=False),
            sa.Column("side", sa.String(10), nullable=False),
            sa.Column("reduce_only", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("state", sa.String(40), nullable=False),
            sa.Column("requested_quantity", sa.Numeric(30, 12), nullable=False),
            sa.Column("acknowledged_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.CheckConstraint(
                "execution_mode IN ('local_paper', 'binance_testnet')",
                name="ck_exchange_orders_execution_mode",
            ),
            sa.UniqueConstraint(
                "exchange_account",
                "client_order_id",
                name="uq_exchange_orders_account_client",
            ),
            sa.UniqueConstraint(
                "exchange_account",
                "exchange_order_id",
                name="uq_exchange_orders_account_order",
            ),
        )
        op.create_index("ix_exchange_orders_local_order_execution_id", "exchange_orders", ["local_order_execution_id"])
        op.create_index("ix_exchange_orders_exchange_account", "exchange_orders", ["exchange_account"])
        op.create_index("ix_exchange_orders_symbol", "exchange_orders", ["symbol"])
        op.create_index("ix_exchange_orders_state", "exchange_orders", ["state"])

    if "exchange_fill_receipts" not in tables:
        op.create_table(
            "exchange_fill_receipts",
            sa.Column("receipt_id", sa.String(36), primary_key=True),
            sa.Column(
                "exchange_order_record_id",
                sa.String(36),
                sa.ForeignKey("exchange_orders.exchange_order_record_id"),
                nullable=False,
            ),
            sa.Column("exchange_account", sa.String(120), nullable=False),
            sa.Column("exchange_order_id", sa.String(120), nullable=False),
            sa.Column("client_order_id", sa.String(36), nullable=False),
            sa.Column("trade_ids", sa.JSON(), nullable=False),
            sa.Column("symbol", sa.String(30), nullable=False),
            sa.Column("side", sa.String(10), nullable=False),
            sa.Column("reduce_only", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("cumulative_filled_quantity", sa.Numeric(30, 12), nullable=False),
            sa.Column("projected_quantity", sa.Numeric(30, 12), nullable=False, server_default="0"),
            sa.Column("average_fill_price", sa.Numeric(30, 12), nullable=False),
            sa.Column("commissions", sa.JSON(), nullable=False),
            sa.Column("event_time", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint(
                "exchange_account",
                "exchange_order_id",
                "cumulative_filled_quantity",
                name="uq_fill_receipt_cumulative_revision",
            ),
        )
        op.create_index(
            "ix_exchange_fill_receipts_exchange_order_record_id",
            "exchange_fill_receipts",
            ["exchange_order_record_id"],
        )
        op.create_index("ix_exchange_fill_receipts_exchange_order_id", "exchange_fill_receipts", ["exchange_order_id"])
        op.create_index("ix_exchange_fill_receipts_client_order_id", "exchange_fill_receipts", ["client_order_id"])
        op.create_index("ix_exchange_fill_receipts_event_time", "exchange_fill_receipts", ["event_time"])

    if "exchange_trade_identities" not in tables:
        op.create_table(
            "exchange_trade_identities",
            sa.Column("exchange_trade_identity_id", sa.String(36), primary_key=True),
            sa.Column(
                "receipt_id",
                sa.String(36),
                sa.ForeignKey("exchange_fill_receipts.receipt_id"),
                nullable=False,
            ),
            sa.Column("exchange_account", sa.String(120), nullable=False),
            sa.Column("trade_id", sa.String(120), nullable=False),
            sa.UniqueConstraint("exchange_account", "trade_id", name="uq_exchange_trade_account_id"),
        )
        op.create_index("ix_exchange_trade_identities_receipt_id", "exchange_trade_identities", ["receipt_id"])

    if "decision_funnel_terminals" not in tables:
        op.create_table(
            "decision_funnel_terminals",
            sa.Column("terminal_id", sa.String(36), primary_key=True),
            sa.Column(
                "paper_run_id",
                sa.String(36),
                sa.ForeignKey("paper_runs.paper_run_id"),
                nullable=False,
            ),
            sa.Column("cycle_id", sa.String(120), nullable=False),
            sa.Column("decision_id", sa.String(120), nullable=False),
            sa.Column("symbol", sa.String(30), nullable=False),
            sa.Column("timeframe", sa.String(20), nullable=False),
            sa.Column("bar_time", sa.DateTime(), nullable=False),
            sa.Column("terminal_stage", sa.String(50), nullable=False),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("reason_code", sa.String(120), nullable=False),
            sa.Column("details", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint(
                "paper_run_id",
                "symbol",
                "timeframe",
                "bar_time",
                name="uq_decision_funnel_terminal_bar",
            ),
        )
        for column in (
            "paper_run_id",
            "cycle_id",
            "decision_id",
            "symbol",
            "timeframe",
            "bar_time",
            "terminal_stage",
            "status",
            "reason_code",
            "created_at",
        ):
            op.create_index(
                f"ix_decision_funnel_terminals_{column}",
                "decision_funnel_terminals",
                [column],
            )

    if "llm_invocations" not in tables:
        op.create_table(
            "llm_invocations",
            sa.Column("invocation_id", sa.String(36), primary_key=True),
            sa.Column("cycle_id", sa.String(120), nullable=True),
            sa.Column("decision_id", sa.String(120), nullable=True),
            sa.Column("symbol", sa.String(30), nullable=True),
            sa.Column("called", sa.Boolean(), nullable=False),
            sa.Column("skip_reason", sa.String(120), nullable=True),
            sa.Column("provider", sa.String(80), nullable=True),
            sa.Column("model", sa.String(160), nullable=True),
            sa.Column("stage", sa.String(30), nullable=False),
            sa.Column("status", sa.String(40), nullable=False),
            sa.Column("input_hash", sa.String(64), nullable=True),
            sa.Column("output_hash", sa.String(64), nullable=True),
            sa.Column("latency_ms", sa.Integer(), nullable=True),
            sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        for column in (
            "cycle_id",
            "decision_id",
            "symbol",
            "skip_reason",
            "provider",
            "stage",
            "status",
            "created_at",
        ):
            op.create_index(f"ix_llm_invocations_{column}", "llm_invocations", [column])

    position_columns = _column_names(bind, "position_records")
    with op.batch_alter_table("position_records") as batch_op:
        if "entry_fill_receipt_id" not in position_columns:
            batch_op.add_column(sa.Column("entry_fill_receipt_id", sa.String(36), nullable=True))
            batch_op.create_foreign_key(
                "fk_position_records_entry_fill_receipt_id",
                "exchange_fill_receipts",
                ["entry_fill_receipt_id"],
                ["receipt_id"],
            )
            batch_op.create_index("ix_position_records_entry_fill_receipt_id", ["entry_fill_receipt_id"])
        if "position_group_id" not in position_columns:
            batch_op.add_column(sa.Column("position_group_id", sa.String(120), nullable=True))
            batch_op.create_index("ix_position_records_position_group_id", ["position_group_id"])
        if "execution_mode" not in position_columns:
            batch_op.add_column(sa.Column("execution_mode", sa.String(30), nullable=True))
            batch_op.create_index("ix_position_records_execution_mode", ["execution_mode"])

    protection_columns = _column_names(bind, "protection_records")
    with op.batch_alter_table("protection_records") as batch_op:
        if "stop_exchange_order_id" not in protection_columns:
            batch_op.add_column(sa.Column("stop_exchange_order_id", sa.String(120), nullable=True))
        if "take_profit_exchange_order_id" not in protection_columns:
            batch_op.add_column(sa.Column("take_profit_exchange_order_id", sa.String(120), nullable=True))

    op.execute(
        sa.text(
            "UPDATE position_records "
            "SET management_status = 'LEGACY_UNVERIFIED' "
            "WHERE management_status = 'MANAGED_STRATEGY' "
            "AND (entry_fill_receipt_id IS NULL OR position_group_id IS NULL)"
        )
    )


def downgrade() -> None:
    # Exchange execution evidence is immutable audit data and is intentionally retained.
    pass
