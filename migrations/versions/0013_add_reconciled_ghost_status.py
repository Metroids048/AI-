"""Add RECONCILED_GHOST and CANCELLED_GHOST_POSITION status values

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-26
"""

# revision identifiers, used by Alembic.
revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add RECONCILED_GHOST status to position_records and protection_records tables.

    SQLite does not support ALTER TYPE for enums, but our models use VARCHAR(50) for
    management_status and status columns, so new values are inserted directly without
    schema changes. This migration is a documentation marker only.
    """
    # No-op: SQLite VARCHAR columns accept any string value.
    # The new enum values are enforced at the application layer via Pydantic/SQLAlchemy models.
    pass


def downgrade() -> None:
    """Downgrade is no-op: existing RECONCILED_GHOST/CANCELLED_GHOST_POSITION rows
    remain valid strings in VARCHAR columns."""
    pass
