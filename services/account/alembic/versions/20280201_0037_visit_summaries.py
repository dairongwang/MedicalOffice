"""Visit summaries: the after-visit summary the patient gets, prepared from the chart and signed by the
doctor (drafts, signed versions, amendments; the hand-over to the patient)

Revision ID: 0037_visit_summaries
Revises: 0036_referral_letters
Create Date: 2028-02-01 09:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0037_visit_summaries"
down_revision = "0036_referral_letters"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "visit_summaries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("appointment_id", sa.Integer(), sa.ForeignKey("appointments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("patient_id", sa.Integer(), sa.ForeignKey("patients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("worker_id", sa.Integer(), sa.ForeignKey("workers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("content", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("generated_at", sa.DateTime(), nullable=False),
        sa.Column("generated_by", sa.String(length=16), nullable=False, server_default="provider"),
        sa.Column("assessment", sa.Text(), nullable=True),
        sa.Column("plan", sa.Text(), nullable=True),
        sa.Column("instructions", sa.Text(), nullable=True),
        sa.Column("follow_up", sa.Text(), nullable=True),
        sa.Column("signer_name", sa.String(length=200), nullable=True),
        sa.Column("signature_png", sa.Text(), nullable=True),
        sa.Column("signed_at", sa.DateTime(), nullable=True),
        sa.Column("signed_by", sa.String(length=36), nullable=True),
        sa.Column("esign_reference", sa.String(length=120), nullable=True),
        sa.Column("esign_provider", sa.String(length=32), nullable=True),
        sa.Column("verification", sa.String(length=40), nullable=True),
        sa.Column("amendment_reason", sa.String(length=500), nullable=True),
        sa.Column("superseded_at", sa.DateTime(), nullable=True),
        sa.Column(
            "superseded_by_id", sa.Integer(), sa.ForeignKey("visit_summaries.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("provider_notified_at", sa.DateTime(), nullable=True),
        sa.Column("provider_notify_channels", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("provider_notified_to", sa.String(length=400), nullable=False, server_default=""),
        sa.Column("provider_notify_note", sa.String(length=500), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("sent_channels", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("sent_to", sa.String(length=400), nullable=False, server_default=""),
        sa.Column("delivery_note", sa.String(length=500), nullable=True),
        sa.Column("patient_viewed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("appointment_id", "version", name="uq_visit_summary_version"),
    )
    op.create_index("ix_visit_summaries_appointment_id", "visit_summaries", ["appointment_id"])
    op.create_index("ix_visit_summaries_patient_id", "visit_summaries", ["patient_id"])
    op.create_index(
        "uq_visit_summary_draft",
        "visit_summaries",
        ["appointment_id"],
        unique=True,
        sqlite_where=sa.text("status = 'draft'"),
        postgresql_where=sa.text("status = 'draft'"),
    )


def downgrade() -> None:
    op.drop_index("uq_visit_summary_draft", table_name="visit_summaries")
    op.drop_index("ix_visit_summaries_patient_id", table_name="visit_summaries")
    op.drop_index("ix_visit_summaries_appointment_id", table_name="visit_summaries")
    op.drop_table("visit_summaries")
