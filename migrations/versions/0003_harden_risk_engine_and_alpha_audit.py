"""harden risk engine defaults and order audit payloads

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("risk_profiles") as batch_op:
        batch_op.alter_column("max_symbol_exposure", existing_type=sa.Float(), server_default="0.10")
        batch_op.alter_column("max_total_exposure", existing_type=sa.Float(), server_default="0.50")
        batch_op.add_column(sa.Column("consecutive_loss_limit", sa.Integer(), nullable=False, server_default="4"))
        batch_op.add_column(sa.Column("api_failure_limit", sa.Integer(), nullable=False, server_default="3"))
        batch_op.add_column(sa.Column("api_failure_window_minutes", sa.Integer(), nullable=False, server_default="10"))

    op.execute(sa.text("UPDATE risk_profiles SET max_symbol_exposure = 0.10 WHERE max_symbol_exposure = 0.20"))
    op.execute(sa.text("UPDATE risk_profiles SET max_total_exposure = 0.50 WHERE max_total_exposure = 0.60"))

    with op.batch_alter_table("strategy_ideas") as batch_op:
        batch_op.add_column(sa.Column("intake_metadata", sa.JSON(), nullable=False, server_default="{}"))

    with op.batch_alter_table("failure_records") as batch_op:
        batch_op.alter_column("strategy_id", existing_type=sa.String(length=36), nullable=True)
        batch_op.add_column(sa.Column("idea_id", sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            "fk_failure_records_idea_id_strategy_ideas",
            "strategy_ideas",
            ["idea_id"],
            ["idea_id"],
        )
        batch_op.create_index("ix_failure_records_idea_id", ["idea_id"])

    with op.batch_alter_table("order_executions") as batch_op:
        batch_op.add_column(sa.Column("rejection_codes", sa.JSON(), nullable=False, server_default="[]"))
        batch_op.add_column(sa.Column("evaluated_risk_state", sa.JSON(), nullable=False, server_default="{}"))


def downgrade() -> None:
    with op.batch_alter_table("order_executions") as batch_op:
        batch_op.drop_column("evaluated_risk_state")
        batch_op.drop_column("rejection_codes")

    with op.batch_alter_table("failure_records") as batch_op:
        batch_op.drop_index("ix_failure_records_idea_id")
        batch_op.drop_constraint("fk_failure_records_idea_id_strategy_ideas", type_="foreignkey")
        batch_op.drop_column("idea_id")
        batch_op.alter_column("strategy_id", existing_type=sa.String(length=36), nullable=False)

    with op.batch_alter_table("strategy_ideas") as batch_op:
        batch_op.drop_column("intake_metadata")

    with op.batch_alter_table("risk_profiles") as batch_op:
        batch_op.drop_column("api_failure_window_minutes")
        batch_op.drop_column("api_failure_limit")
        batch_op.drop_column("consecutive_loss_limit")
        batch_op.alter_column("max_total_exposure", existing_type=sa.Float(), server_default="0.60")
        batch_op.alter_column("max_symbol_exposure", existing_type=sa.Float(), server_default="0.20")

    op.execute(sa.text("UPDATE risk_profiles SET max_symbol_exposure = 0.20 WHERE max_symbol_exposure = 0.10"))
    op.execute(sa.text("UPDATE risk_profiles SET max_total_exposure = 0.60 WHERE max_total_exposure = 0.50"))
