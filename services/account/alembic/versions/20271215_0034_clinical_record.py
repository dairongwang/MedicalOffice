"""The clinical record the provider reads in the patient's chart: lab / imaging results, referral notes,
the provider's visit notes — and, on the assistant's task, whether the provider was told the patient is
ready. (The medical history — conditions, surgeries, family / social history — uses the new kinds of
patient_health_items; no schema change for it.)

Revision ID: 0034_clinical_record
Revises: 0033_assistant_review
Create Date: 2027-12-15 09:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0034_clinical_record"
down_revision = "0033_assistant_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "patient_results",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("patient_id", sa.Integer(), sa.ForeignKey("patients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="final"),
        sa.Column("ordered_on", sa.Date(), nullable=True),
        sa.Column("resulted_on", sa.Date(), nullable=True),
        sa.Column("performed_by", sa.String(length=200), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("lab_values", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("appointment_id", sa.Integer(), sa.ForeignKey("appointments.id", ondelete="SET NULL"), nullable=True),
        sa.Column("ordered_by", sa.Integer(), sa.ForeignKey("workers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("entered_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_patient_results_patient_id", "patient_results", ["patient_id"])
    op.create_table(
        "patient_referrals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("patient_id", sa.Integer(), sa.ForeignKey("patients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("counterpart", sa.String(length=200), nullable=False),
        sa.Column("specialty", sa.String(length=120), nullable=True),
        sa.Column("referral_date", sa.Date(), nullable=False),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("appointment_id", sa.Integer(), sa.ForeignKey("appointments.id", ondelete="SET NULL"), nullable=True),
        sa.Column("entered_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_patient_referrals_patient_id", "patient_referrals", ["patient_id"])
    op.create_table(
        "visit_notes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("appointment_id", sa.Integer(), sa.ForeignKey("appointments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("worker_id", sa.Integer(), sa.ForeignKey("workers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("seen_at", sa.DateTime(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("assessment", sa.Text(), nullable=True),
        sa.Column("plan", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("appointment_id", name="uq_visit_notes_appointment_id"),
    )
    with op.batch_alter_table("appointment_assignments") as batch:
        batch.add_column(sa.Column("provider_notified_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("provider_notify_channels", sa.String(length=64), nullable=False, server_default=""))
        batch.add_column(sa.Column("provider_notified_to", sa.String(length=400), nullable=False, server_default=""))
        batch.add_column(sa.Column("provider_notify_note", sa.String(length=500), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("appointment_assignments") as batch:
        batch.drop_column("provider_notify_note")
        batch.drop_column("provider_notified_to")
        batch.drop_column("provider_notify_channels")
        batch.drop_column("provider_notified_at")
    op.drop_table("visit_notes")
    op.drop_index("ix_patient_referrals_patient_id", table_name="patient_referrals")
    op.drop_table("patient_referrals")
    op.drop_index("ix_patient_results_patient_id", table_name="patient_results")
    op.drop_table("patient_results")
