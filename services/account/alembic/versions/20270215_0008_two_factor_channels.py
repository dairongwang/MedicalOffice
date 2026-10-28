"""two-factor by e-mail and text message: per-method switches, phone number, sent codes

Revision ID: 0008_two_factor_channels
Revises: 0007_two_factor
Create Date: 2027-02-15 09:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0008_two_factor_channels"
down_revision = "0007_two_factor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("accounts") as batch:
        batch.add_column(sa.Column("email_otp_enabled_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("sms_otp_enabled_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("phone_number", sa.String(length=20), nullable=True))
    op.create_table(
        "one_time_codes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("purpose", sa.String(length=16), nullable=False),
        sa.Column("channel", sa.String(length=8), nullable=False),
        sa.Column("destination", sa.String(length=320), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "purpose", name="uq_one_time_code_purpose"),
    )
    op.create_index(op.f("ix_one_time_codes_account_id"), "one_time_codes", ["account_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_one_time_codes_account_id"), table_name="one_time_codes")
    op.drop_table("one_time_codes")
    with op.batch_alter_table("accounts") as batch:
        batch.drop_column("phone_number")
        batch.drop_column("sms_otp_enabled_at")
        batch.drop_column("email_otp_enabled_at")
