"""accounts: optional second factor (TOTP secret, pending secret, replay guard, recovery codes)

Revision ID: 0007_two_factor
Revises: 0006_payment_refunded_at
Create Date: 2027-02-01 09:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0007_two_factor"
down_revision = "0006_payment_refunded_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("accounts") as batch:  # batch: SQLite cannot ALTER in place
        batch.add_column(sa.Column("totp_secret", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("totp_enabled_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("totp_pending_secret", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("totp_last_step", sa.BigInteger(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("recovery_codes", sa.Text(), nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("accounts") as batch:
        batch.drop_column("recovery_codes")
        batch.drop_column("totp_last_step")
        batch.drop_column("totp_pending_secret")
        batch.drop_column("totp_enabled_at")
        batch.drop_column("totp_secret")
