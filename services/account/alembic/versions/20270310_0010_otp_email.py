"""two-factor: a separate address for e-mail codes (changeable; the sign-in e-mail is not)

Revision ID: 0010_otp_email
Revises: 0009_oauth_identities
Create Date: 2027-03-10 09:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0010_otp_email"
down_revision = "0009_oauth_identities"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("accounts") as batch:
        batch.add_column(sa.Column("otp_email", sa.String(length=320), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("accounts") as batch:
        batch.drop_column("otp_email")
