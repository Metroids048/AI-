"""Add EXCHANGE_UNKNOWN to the V2 intent state constraint.

A submission whose request left the process but whose outcome is undetermined
(timeout, connection reset) is neither submitting nor rejected. Without a
dedicated state the runtime is pushed toward the exact failure this rebuild
exists to remove: resubmitting under a fresh Client Order ID and creating a
duplicate exchange position.

EXCHANGE_UNKNOWN is resolved only by Client Order ID lookup against the
exchange (plan section 6.3).

SQLite cannot ALTER a CHECK constraint, so the table is rebuilt via Alembic's
batch mode. V2 tables were introduced empty in 0016.

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-28
"""

from __future__ import annotations

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

_OLD_STATES = (
    "state IN ('INTENT_CREATED', 'EXCHANGE_SUBMITTING', 'EXCHANGE_ACKNOWLEDGED', "
    "'FILLED', 'REJECTED', 'CANCELLED', 'EXPIRED')"
)

_NEW_STATES = (
    "state IN ('INTENT_CREATED', 'EXCHANGE_SUBMITTING', 'EXCHANGE_UNKNOWN', "
    "'EXCHANGE_ACKNOWLEDGED', 'FILLED', 'REJECTED', 'CANCELLED', 'EXPIRED')"
)


def upgrade() -> None:
    with op.batch_alter_table("v2_execution_intents") as batch:
        batch.drop_constraint("ck_v2_intent_state", type_="check")
        batch.create_check_constraint("ck_v2_intent_state", _NEW_STATES)


def downgrade() -> None:
    # Any row parked in EXCHANGE_UNKNOWN would violate the old constraint. Such a
    # row means an unresolved exchange submission, which must not be silently
    # rewritten to a state that asserts a known outcome.
    connection = op.get_bind()
    unresolved = connection.exec_driver_sql(
        "SELECT COUNT(*) FROM v2_execution_intents WHERE state = 'EXCHANGE_UNKNOWN'"
    ).scalar()
    if unresolved:
        raise RuntimeError(
            f"cannot downgrade: {unresolved} intent(s) are in EXCHANGE_UNKNOWN. "
            "Resolve them by Client Order ID lookup before downgrading."
        )

    with op.batch_alter_table("v2_execution_intents") as batch:
        batch.drop_constraint("ck_v2_intent_state", type_="check")
        batch.create_check_constraint("ck_v2_intent_state", _OLD_STATES)
