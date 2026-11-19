"""The assistant's review of a visit: the patients' medication and allergy lists
(patient_health_items), the items an assistant clears one by one with a note each
(assignment_review_items), and the vital signs / ticked parts on the task

Revision ID: 0033_assistant_review
Revises: 0032_medical_assistants
Create Date: 2027-12-01 09:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0033_assistant_review"
down_revision = "0032_medical_assistants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "patient_health_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("patient_id", sa.Integer(), sa.ForeignKey("patients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("detail", sa.String(length=300), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="office"),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.Column("last_reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("last_reviewed_by", sa.Integer(), sa.ForeignKey("workers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_patient_health_items_patient_id", "patient_health_items", ["patient_id"])
    op.create_table(
        "assignment_review_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "assignment_id",
            sa.Integer(),
            sa.ForeignKey("appointment_assignments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column(
            "health_item_id",
            sa.Integer(),
            sa.ForeignKey("patient_health_items.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("flag", sa.String(length=16), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("detail", sa.String(length=300), nullable=True),
        sa.Column("record_detail", sa.String(length=300), nullable=True),
        sa.Column("check_in_text", sa.String(length=300), nullable=True),
        sa.Column("decision", sa.String(length=16), nullable=True),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_assignment_review_items_assignment_id", "assignment_review_items", ["assignment_id"])
    with op.batch_alter_table("appointment_assignments") as batch:
        batch.add_column(sa.Column("vitals", sa.Text(), nullable=True))
        batch.add_column(sa.Column("checklist", sa.Text(), nullable=False, server_default="{}"))
        batch.add_column(sa.Column("review_check_in_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("appointment_assignments") as batch:
        batch.drop_column("review_check_in_id")
        batch.drop_column("checklist")
        batch.drop_column("vitals")
    op.drop_index("ix_assignment_review_items_assignment_id", table_name="assignment_review_items")
    op.drop_table("assignment_review_items")
    op.drop_index("ix_patient_health_items_patient_id", table_name="patient_health_items")
    op.drop_table("patient_health_items")
