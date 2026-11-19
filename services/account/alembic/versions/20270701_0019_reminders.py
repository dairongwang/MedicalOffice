"""appointments: the reminder sent the day before, and the patient's confirmation

Revision ID: 0019_reminders
Revises: 0018_patient_accounts
Create Date: 2027-07-01 09:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0019_reminders"
down_revision = "0018_patient_accounts"
branch_labels = None
depends_on = None

COLUMNS = (
    sa.Column("reminder_sent_at", sa.DateTime(), nullable=True),
    sa.Column("reminder_channels", sa.String(length=64), nullable=False, server_default=""),
    sa.Column("reminder_note", sa.String(length=500), nullable=True),
    sa.Column("confirmed_at", sa.DateTime(), nullable=True),
    sa.Column("confirmed_via", sa.String(length=16), nullable=True),
)


def upgrade() -> None:
    with op.batch_alter_table("appointments") as batch:
        for col in COLUMNS:
            batch.add_column(col)


def downgrade() -> None:
    with op.batch_alter_table("appointments") as batch:
        for col in reversed(COLUMNS):
            batch.drop_column(col.name)
