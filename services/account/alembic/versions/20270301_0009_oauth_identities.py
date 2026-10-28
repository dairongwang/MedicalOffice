"""sign in with Google / Facebook: linked provider identities

Revision ID: 0009_oauth_identities
Revises: 0008_two_factor_channels
Create Date: 2027-03-01 09:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0009_oauth_identities"
down_revision = "0008_two_factor_channels"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "oauth_identities",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("subject", sa.String(length=128), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "subject", name="uq_oauth_identity"),
    )
    op.create_index(op.f("ix_oauth_identities_account_id"), "oauth_identities", ["account_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_oauth_identities_account_id"), table_name="oauth_identities")
    op.drop_table("oauth_identities")
