"""appointments: check-in — by the patient from the app (within CHECK_IN_WINDOW_HOURS of the
visit) or by the office from the dashboard

Revision ID: 0028_appointment_check_in
Revises: 0027_validations_outlive_methods
Create Date: 2027-09-15 09:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0028_appointment_check_in"
down_revision = "0027_validations_outlive_methods"
branch_labels = None
depends_on = None

COLUMNS = (
    sa.Column("checked_in_at", sa.DateTime(), nullable=True),
    sa.Column("checked_in_via", sa.String(length=16), nullable=True),
    sa.Column("checked_in_by", sa.String(length=36), nullable=True),
)


def upgrade() -> None:
    with op.batch_alter_table("appointments") as batch:
        for col in COLUMNS:
            batch.add_column(col)


def downgrade() -> None:
    with op.batch_alter_table("appointments") as batch:
        for col in reversed(COLUMNS):
            batch.drop_column(col.name)
