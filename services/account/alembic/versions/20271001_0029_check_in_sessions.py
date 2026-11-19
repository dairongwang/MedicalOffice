"""appointment_check_ins: the check-in page — questionnaire + signature, the estimated cost not
covered by insurance + the financial-responsibility signature, its pre-authorization

Revision ID: 0029_check_in_sessions
Revises: 0028_appointment_check_in
Create Date: 2027-10-01 09:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0029_check_in_sessions"
down_revision = "0028_appointment_check_in"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "appointment_check_ins",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("appointment_id", sa.Integer(), sa.ForeignKey("appointments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("via", sa.String(length=16), nullable=False),
        sa.Column("started_by", sa.String(length=36), nullable=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("link", sa.String(length=2048), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("link_channels", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("link_sent_to", sa.String(length=400), nullable=False, server_default=""),
        sa.Column("link_delivery_note", sa.String(length=500), nullable=True),
        sa.Column("link_sent_at", sa.DateTime(), nullable=True),
        sa.Column("opened_at", sa.DateTime(), nullable=True),
        sa.Column("questionnaire_version", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("answers", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("answers_signer_name", sa.String(length=200), nullable=True),
        sa.Column("answers_signature_png", sa.Text(), nullable=True),
        sa.Column("answers_signed_at", sa.DateTime(), nullable=True),
        sa.Column("answers_esign_reference", sa.String(length=120), nullable=True),
        sa.Column("estimate_cents", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="USD"),
        sa.Column("estimate", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("financial_signer_name", sa.String(length=200), nullable=True),
        sa.Column("financial_signature_png", sa.Text(), nullable=True),
        sa.Column("financial_signed_at", sa.DateTime(), nullable=True),
        sa.Column("financial_esign_reference", sa.String(length=120), nullable=True),
        sa.Column("esign_provider", sa.String(length=32), nullable=True),
        sa.Column("payment_id", sa.Integer(), sa.ForeignKey("patient_payments.id", ondelete="SET NULL"), nullable=True),
        sa.Column("failure_code", sa.String(length=32), nullable=True),
        sa.Column("failure_reason", sa.String(length=500), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("failed_at", sa.DateTime(), nullable=True),
        sa.Column("canceled_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("token_hash", name="uq_appointment_check_in_token"),
    )
    op.create_index("ix_appointment_check_ins_appointment_id", "appointment_check_ins", ["appointment_id"])


def downgrade() -> None:
    op.drop_index("ix_appointment_check_ins_appointment_id", table_name="appointment_check_ins")
    op.drop_table("appointment_check_ins")
