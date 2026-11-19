"""workers.account_id: the staff dashboard account of a worker (role = staff)

Revision ID: 0031_staff_accounts
Revises: 0030_check_in_staff_notification
Create Date: 2027-11-01 09:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0031_staff_accounts"
down_revision = "0030_check_in_staff_notification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("workers") as batch:
        batch.add_column(sa.Column("account_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key("fk_workers_account_id", "accounts", ["account_id"], ["id"], ondelete="SET NULL")
        batch.create_unique_constraint("uq_workers_account_id", ["account_id"])


def downgrade() -> None:
    with op.batch_alter_table("workers") as batch:
        batch.drop_constraint("uq_workers_account_id", type_="unique")
        batch.drop_constraint("fk_workers_account_id", type_="foreignkey")
        batch.drop_column("account_id")
