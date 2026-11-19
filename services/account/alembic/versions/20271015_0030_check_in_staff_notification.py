"""appointment_check_ins: the notification to the appointment's provider once the patient checked in

Revision ID: 0030_check_in_staff_notification
Revises: 0029_check_in_sessions
Create Date: 2027-10-15 09:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0030_check_in_staff_notification"
down_revision = "0029_check_in_sessions"
branch_labels = None
depends_on = None

COLUMNS = (
    sa.Column("staff_notified_at", sa.DateTime(), nullable=True),
    sa.Column("staff_notify_channels", sa.String(length=64), nullable=False, server_default=""),
    sa.Column("staff_notified_to", sa.String(length=400), nullable=False, server_default=""),
    sa.Column("staff_notify_note", sa.String(length=500), nullable=True),
)


def upgrade() -> None:
    with op.batch_alter_table("appointment_check_ins") as batch:
        for col in COLUMNS:
            batch.add_column(col)


def downgrade() -> None:
    with op.batch_alter_table("appointment_check_ins") as batch:
        for col in reversed(COLUMNS):
            batch.drop_column(col.name)
