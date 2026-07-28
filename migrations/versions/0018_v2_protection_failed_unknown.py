"""Add PROTECTION_FAILED and PROTECTION_UNKNOWN to the V2 protection constraint.

Plan section 7.4 requires an escalation ladder when protection submission fails:
retry once under a new attempt number, then emergency reduce-only close, then
EMERGENCY_CLOSE_PENDING with an account-wide Entry block. That ladder needs a
persisted FAILED state, otherwise a restart cannot resume the escalation and a
live position could sit unprotected while the system reports healthy.

PROTECTION_UNKNOWN covers a submission whose outcome is undetermined; like
EXCHANGE_UNKNOWN it is resolved by Client Order ID lookup, never by resubmission.

SQLite cannot ALTER a CHECK constraint, so the table is rebuilt via batch mode.

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-28
"""

from __future__ import annotations

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

_OLD_STATES = (
    "state IN ('PROTECTION_INTENT', 'PROTECTION_SUBMITTING', 'PROTECTION_ACTIVE', "
    "'PROTECTION_TRIGGERED', 'PROTECTION_FILLED', 'PROTECTION_CANCELLED')"
)

_NEW_STATES = (
    "state IN ('PROTECTION_INTENT', 'PROTECTION_SUBMITTING', 'PROTECTION_ACTIVE', "
    "'PROTECTION_TRIGGERED', 'PROTECTION_FILLED', 'PROTECTION_CANCELLED', "
    "'PROTECTION_FAILED', 'PROTECTION_UNKNOWN')"
)


def upgrade() -> None:
    with op.batch_alter_table("v2_protection_records") as batch:
        batch.drop_constraint("ck_v2_protection_state", type_="check")
        batch.create_check_constraint("ck_v2_protection_state", _NEW_STATES)


def downgrade() -> None:
    connection = op.get_bind()
    unresolved = connection.exec_driver_sql(
        "SELECT COUNT(*) FROM v2_protection_records WHERE state IN ('PROTECTION_FAILED', 'PROTECTION_UNKNOWN')"
    ).scalar()
    if unresolved:
        raise RuntimeError(
            f"cannot downgrade: {unresolved} protection record(s) are FAILED or UNKNOWN. "
            "These represent positions that may be unprotected; resolve the escalation "
            "before downgrading."
        )

    with op.batch_alter_table("v2_protection_records") as batch:
        batch.drop_constraint("ck_v2_protection_state", type_="check")
        batch.create_check_constraint("ck_v2_protection_state", _OLD_STATES)
