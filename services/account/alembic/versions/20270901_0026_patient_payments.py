"""patient payments: the ledger behind the payment history — holds at booking, charges, refunds

Revision ID: 0026_patient_payments
Revises: 0025_payment_method_validation
Create Date: 2027-09-01 09:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0026_patient_payments"
down_revision = "0025_payment_method_validation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "patient_payments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("patient_id", sa.Integer(), nullable=True),
        sa.Column("method_id", sa.Integer(), nullable=True),
        sa.Column("method_kind", sa.String(length=16), nullable=False),
        sa.Column("method_display", sa.String(length=120), nullable=False),
        sa.Column("appointment_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="USD"),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("refunded_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("description", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("message", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("reference", sa.String(length=120), nullable=True),
        sa.Column("refund_reference", sa.String(length=120), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.String(length=16), nullable=False),
        sa.Column("actor_account_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("captured_at", sa.DateTime(), nullable=True),
        sa.Column("released_at", sa.DateTime(), nullable=True),
        sa.Column("refunded_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["method_id"], ["patient_payment_methods.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["appointment_id"], ["appointments.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_patient_payments_account_id", "patient_payments", ["account_id"])
    op.create_index("ix_patient_payments_patient_id", "patient_payments", ["patient_id"])
    op.create_index("ix_patient_payments_appointment_id", "patient_payments", ["appointment_id"])


def downgrade() -> None:
    op.drop_index("ix_patient_payments_appointment_id", table_name="patient_payments")
    op.drop_index("ix_patient_payments_patient_id", table_name="patient_payments")
    op.drop_index("ix_patient_payments_account_id", table_name="patient_payments")
    op.drop_table("patient_payments")
