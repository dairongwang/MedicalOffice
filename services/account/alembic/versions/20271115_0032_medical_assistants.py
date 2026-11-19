"""appointment_assignments: medical assistants assigned by the office to appointments — take the
task, complete it (every duty confirmed) or report they cannot do it (the office reassigns)

Workers get a new role, ``medical_assistant`` (workers.role is free text validated by the API —
no schema change needed for it).

Revision ID: 0032_medical_assistants
Revises: 0031_staff_accounts
Create Date: 2027-11-15 09:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0032_medical_assistants"
down_revision = "0031_staff_accounts"
branch_labels = None
depends_on = None

OPEN = "status IN ('assigned', 'accepted')"


def upgrade() -> None:
    op.create_table(
        "appointment_assignments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("appointment_id", sa.Integer(), sa.ForeignKey("appointments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("worker_id", sa.Integer(), sa.ForeignKey("workers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="assigned"),
        sa.Column("instructions", sa.String(length=1000), nullable=True),
        sa.Column("assigned_by", sa.String(length=36), nullable=True),
        sa.Column("assigned_at", sa.DateTime(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("duties", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("completion_note", sa.String(length=2000), nullable=True),
        sa.Column("reported_at", sa.DateTime(), nullable=True),
        sa.Column("report_reason", sa.String(length=32), nullable=True),
        sa.Column("report_note", sa.String(length=1000), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_by", sa.String(length=36), nullable=True),
        sa.Column(
            "replaced_by_id",
            sa.Integer(),
            sa.ForeignKey("appointment_assignments.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("canceled_at", sa.DateTime(), nullable=True),
        sa.Column("cancel_reason", sa.String(length=300), nullable=True),
        sa.Column("notified_at", sa.DateTime(), nullable=True),
        sa.Column("notify_channels", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("notified_to", sa.String(length=400), nullable=False, server_default=""),
        sa.Column("notify_note", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_appointment_assignments_appointment_id", "appointment_assignments", ["appointment_id"])
    op.create_index("ix_appointment_assignments_worker_status", "appointment_assignments", ["worker_id", "status"])
    op.create_index(
        "uq_assignment_open",
        "appointment_assignments",
        ["appointment_id"],
        unique=True,
        sqlite_where=sa.text(OPEN),
        postgresql_where=sa.text(OPEN),
    )


def downgrade() -> None:
    op.drop_index("uq_assignment_open", table_name="appointment_assignments")
    op.drop_index("ix_appointment_assignments_worker_status", table_name="appointment_assignments")
    op.drop_index("ix_appointment_assignments_appointment_id", table_name="appointment_assignments")
    op.drop_table("appointment_assignments")
