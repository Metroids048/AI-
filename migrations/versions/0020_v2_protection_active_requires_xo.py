"""Gate 1 fix: PROTECTION_ACTIVE requires stop_exchange_order_id.

Revision ID: 0020
Revises: 0019
Create Date: 2026-07-29
"""

from __future__ import annotations

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite: rebuild table to attach CHECK that ACTIVE requires stop XO.
    op.execute("PRAGMA foreign_keys=OFF")
    op.execute(
        """
        CREATE TABLE v2_protection_records__new (
            protection_id VARCHAR(36) NOT NULL,
            position_id VARCHAR(36) NOT NULL,
            stop_loss_price NUMERIC(20, 4) NOT NULL,
            take_profit_price NUMERIC(20, 4),
            stop_client_order_id VARCHAR(100) NOT NULL,
            tp_client_order_id VARCHAR(100),
            stop_exchange_order_id VARCHAR(100),
            tp_exchange_order_id VARCHAR(100),
            state VARCHAR(30) NOT NULL,
            version INTEGER DEFAULT 0 NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
            activated_at DATETIME,
            PRIMARY KEY (protection_id),
            FOREIGN KEY(position_id) REFERENCES v2_managed_positions (position_id),
            CONSTRAINT ck_v2_protection_stop_price_positive CHECK (stop_loss_price > 0),
            CONSTRAINT ck_v2_protection_tp_price_positive CHECK (
                take_profit_price IS NULL OR take_profit_price > 0
            ),
            CONSTRAINT ck_v2_protection_state CHECK (
                state IN (
                    'PROTECTION_INTENT', 'PROTECTION_SUBMITTING', 'PROTECTION_ACTIVE',
                    'PROTECTION_TRIGGERED', 'PROTECTION_FILLED', 'PROTECTION_CANCELLED',
                    'PROTECTION_FAILED', 'PROTECTION_UNKNOWN'
                )
            ),
            CONSTRAINT ck_v2_protection_active_requires_stop_xo CHECK (
                state != 'PROTECTION_ACTIVE'
                OR (stop_exchange_order_id IS NOT NULL AND length(stop_exchange_order_id) > 0)
            ),
            UNIQUE (stop_client_order_id),
            UNIQUE (tp_client_order_id)
        )
        """
    )
    op.execute(
        """
        INSERT INTO v2_protection_records__new (
            protection_id, position_id, stop_loss_price, take_profit_price,
            stop_client_order_id, tp_client_order_id,
            stop_exchange_order_id, tp_exchange_order_id,
            state, version, created_at, activated_at
        )
        SELECT
            protection_id, position_id, stop_loss_price, take_profit_price,
            stop_client_order_id, tp_client_order_id,
            stop_exchange_order_id, tp_exchange_order_id,
            state, version, created_at, activated_at
        FROM v2_protection_records
        WHERE state != 'PROTECTION_ACTIVE'
           OR (stop_exchange_order_id IS NOT NULL AND length(stop_exchange_order_id) > 0)
        """
    )
    op.execute("DROP TABLE v2_protection_records")
    op.execute("ALTER TABLE v2_protection_records__new RENAME TO v2_protection_records")
    op.create_index("ix_v2_protection_records_position_id", "v2_protection_records", ["position_id"])
    op.create_index("ix_v2_protection_records_state", "v2_protection_records", ["state"])
    op.execute("PRAGMA foreign_keys=ON")


def downgrade() -> None:
    op.execute("PRAGMA foreign_keys=OFF")
    op.execute(
        """
        CREATE TABLE v2_protection_records__old (
            protection_id VARCHAR(36) NOT NULL,
            position_id VARCHAR(36) NOT NULL,
            stop_loss_price NUMERIC(20, 4) NOT NULL,
            take_profit_price NUMERIC(20, 4),
            stop_client_order_id VARCHAR(100) NOT NULL,
            tp_client_order_id VARCHAR(100),
            stop_exchange_order_id VARCHAR(100),
            tp_exchange_order_id VARCHAR(100),
            state VARCHAR(30) NOT NULL,
            version INTEGER DEFAULT 0 NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
            activated_at DATETIME,
            PRIMARY KEY (protection_id),
            FOREIGN KEY(position_id) REFERENCES v2_managed_positions (position_id),
            CONSTRAINT ck_v2_protection_stop_price_positive CHECK (stop_loss_price > 0),
            CONSTRAINT ck_v2_protection_tp_price_positive CHECK (
                take_profit_price IS NULL OR take_profit_price > 0
            ),
            CONSTRAINT ck_v2_protection_state CHECK (
                state IN (
                    'PROTECTION_INTENT', 'PROTECTION_SUBMITTING', 'PROTECTION_ACTIVE',
                    'PROTECTION_TRIGGERED', 'PROTECTION_FILLED', 'PROTECTION_CANCELLED',
                    'PROTECTION_FAILED', 'PROTECTION_UNKNOWN'
                )
            ),
            UNIQUE (stop_client_order_id),
            UNIQUE (tp_client_order_id)
        )
        """
    )
    op.execute(
        """
        INSERT INTO v2_protection_records__old
        SELECT
            protection_id, position_id, stop_loss_price, take_profit_price,
            stop_client_order_id, tp_client_order_id,
            stop_exchange_order_id, tp_exchange_order_id,
            state, version, created_at, activated_at
        FROM v2_protection_records
        """
    )
    op.execute("DROP TABLE v2_protection_records")
    op.execute("ALTER TABLE v2_protection_records__old RENAME TO v2_protection_records")
    op.create_index("ix_v2_protection_records_position_id", "v2_protection_records", ["position_id"])
    op.create_index("ix_v2_protection_records_state", "v2_protection_records", ["state"])
    op.execute("PRAGMA foreign_keys=ON")
