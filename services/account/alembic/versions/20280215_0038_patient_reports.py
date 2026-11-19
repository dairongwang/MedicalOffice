"""Immunizations in the patient's record; reports the office prepares on request and a doctor signs
(the immunization record, the annual health examination report)

Revision ID: 0038_patient_reports
Revises: 0037_visit_summaries
Create Date: 2028-02-15 09:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0038_patient_reports"
down_revision = "0037_visit_summaries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "patient_immunizations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("patient_id", sa.Integer(), sa.ForeignKey("patients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("vaccine", sa.String(length=200), nullable=False),
        sa.Column("cvx_code", sa.String(length=8), nullable=True),
        sa.Column("date_given", sa.Date(), nullable=False),
        sa.Column("dose_number", sa.Integer(), nullable=True),
        sa.Column("series_doses", sa.Integer(), nullable=True),
        sa.Column("manufacturer", sa.String(length=120), nullable=True),
        sa.Column("lot_number", sa.String(length=60), nullable=True),
        sa.Column("site", sa.String(length=60), nullable=True),
        sa.Column("route", sa.String(length=16), nullable=True),
        sa.Column("administered_by", sa.String(length=200), nullable=True),
        sa.Column("worker_id", sa.Integer(), sa.ForeignKey("workers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="office"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.Column("entered_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_patient_immunizations_patient_id", "patient_immunizations", ["patient_id"])
    op.create_table(
        "patient_reports",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("patient_id", sa.Integer(), sa.ForeignKey("patients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="requested"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("replaces_id", sa.Integer(), sa.ForeignKey("patient_reports.id", ondelete="SET NULL"), nullable=True),
        sa.Column("purpose", sa.String(length=16), nullable=False, server_default="personal"),
        sa.Column("purpose_detail", sa.String(length=200), nullable=True),
        sa.Column("needed_by", sa.Date(), nullable=True),
        sa.Column("request_note", sa.String(length=1000), nullable=True),
        sa.Column("requested_via", sa.String(length=16), nullable=False, server_default="office"),
        sa.Column("requested_by", sa.String(length=36), nullable=True),
        sa.Column("requested_at", sa.DateTime(), nullable=False),
        sa.Column("office_notify_note", sa.String(length=500), nullable=True),
        sa.Column("worker_id", sa.Integer(), sa.ForeignKey("workers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("appointment_id", sa.Integer(), sa.ForeignKey("appointments.id", ondelete="SET NULL"), nullable=True),
        sa.Column("content", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("generated_at", sa.DateTime(), nullable=True),
        sa.Column("generated_by", sa.String(length=36), nullable=True),
        sa.Column("findings", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("doctor_note", sa.Text(), nullable=True),
        sa.Column("signer_name", sa.String(length=200), nullable=True),
        sa.Column("signature_png", sa.Text(), nullable=True),
        sa.Column("signed_at", sa.DateTime(), nullable=True),
        sa.Column("signed_by", sa.String(length=36), nullable=True),
        sa.Column("esign_reference", sa.String(length=120), nullable=True),
        sa.Column("esign_provider", sa.String(length=32), nullable=True),
        sa.Column("verification", sa.String(length=40), nullable=True),
        sa.Column("decline_reason", sa.String(length=500), nullable=True),
        sa.Column("declined_at", sa.DateTime(), nullable=True),
        sa.Column("cancel_reason", sa.String(length=500), nullable=True),
        sa.Column("canceled_at", sa.DateTime(), nullable=True),
        sa.Column("canceled_by", sa.String(length=16), nullable=True),
        sa.Column("amendment_reason", sa.String(length=500), nullable=True),
        sa.Column("superseded_at", sa.DateTime(), nullable=True),
        sa.Column(
            "superseded_by_id", sa.Integer(), sa.ForeignKey("patient_reports.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("doctor_notified_at", sa.DateTime(), nullable=True),
        sa.Column("doctor_notify_channels", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("doctor_notify_note", sa.String(length=500), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("sent_channels", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("sent_to", sa.String(length=400), nullable=False, server_default=""),
        sa.Column("delivery_note", sa.String(length=500), nullable=True),
        sa.Column("patient_viewed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_patient_reports_patient_id", "patient_reports", ["patient_id"])
    op.create_index("ix_patient_reports_status", "patient_reports", ["status"])


def downgrade() -> None:
    op.drop_index("ix_patient_reports_status", table_name="patient_reports")
    op.drop_index("ix_patient_reports_patient_id", table_name="patient_reports")
    op.drop_table("patient_reports")
    op.drop_index("ix_patient_immunizations_patient_id", table_name="patient_immunizations")
    op.drop_table("patient_immunizations")
