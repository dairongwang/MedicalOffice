"""appointments: a patient books a time slot with a provider (appointments.py)

Revision ID: 0016_appointments
Revises: 0015_onboarding_approval
Create Date: 2027-06-01 09:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0016_appointments"
down_revision = "0015_onboarding_approval"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "appointments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("patient_id", sa.Integer(), nullable=False),
        sa.Column("provider_id", sa.Integer(), nullable=False),
        sa.Column("start_at", sa.DateTime(), nullable=False),
        sa.Column("end_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="scheduled"),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("canceled_at", sa.DateTime(), nullable=True),
        sa.Column("canceled_by", sa.String(length=16), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["provider_id"], ["workers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_appointments_patient_id", "appointments", ["patient_id"])
    op.create_index("ix_appointments_provider_id", "appointments", ["provider_id"])
    op.create_index("ix_appointments_start", "appointments", ["start_at"])
    # One *scheduled* appointment per provider and start time; canceled rows free the slot.
    op.create_index(
        "uq_appointment_slot",
        "appointments",
        ["provider_id", "start_at"],
        unique=True,
        sqlite_where=sa.text("status = 'scheduled'"),
        postgresql_where=sa.text("status = 'scheduled'"),
    )


def downgrade() -> None:
    op.drop_index("uq_appointment_slot", table_name="appointments")
    op.drop_index("ix_appointments_start", table_name="appointments")
    op.drop_index("ix_appointments_provider_id", table_name="appointments")
    op.drop_index("ix_appointments_patient_id", table_name="appointments")
    op.drop_table("appointments")
