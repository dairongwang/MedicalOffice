"""appointments: the patient's link (review · add to calendar · cancel) and how it was delivered

Revision ID: 0017_appointment_links
Revises: 0016_appointments
Create Date: 2027-06-10 09:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0017_appointment_links"
down_revision = "0016_appointments"
branch_labels = None
depends_on = None

COLUMNS = (
    sa.Column("token_hash", sa.String(length=64), nullable=True),
    sa.Column("link", sa.String(length=2048), nullable=True),
    sa.Column("link_channels", sa.String(length=64), nullable=False, server_default=""),
    sa.Column("link_sent_to", sa.String(length=400), nullable=False, server_default=""),
    sa.Column("link_delivery_note", sa.String(length=500), nullable=True),
    sa.Column("link_sent_at", sa.DateTime(), nullable=True),
    sa.Column("link_opens", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("link_last_opened_at", sa.DateTime(), nullable=True),
)


def upgrade() -> None:
    with op.batch_alter_table("appointments") as batch:
        for col in COLUMNS:
            batch.add_column(col)
        batch.create_unique_constraint("uq_appointments_token_hash", ["token_hash"])


def downgrade() -> None:
    with op.batch_alter_table("appointments") as batch:
        batch.drop_constraint("uq_appointments_token_hash", type_="unique")
        for col in reversed(COLUMNS):
            batch.drop_column(col.name)
