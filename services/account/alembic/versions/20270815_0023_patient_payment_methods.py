"""patient payment methods: cards, Stripe, PayPal and bank accounts a patient keeps on their account

Revision ID: 0023_patient_payment_methods
Revises: 0022_appointment_payment
Create Date: 2027-08-15 09:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0023_patient_payment_methods"
down_revision = "0022_appointment_payment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "patient_payment_methods",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("label", sa.String(length=60), nullable=True),
        sa.Column("holder_name", sa.String(length=120), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("card_brand", sa.String(length=16), nullable=True),
        sa.Column("card_last4", sa.String(length=4), nullable=True),
        sa.Column("card_exp_month", sa.Integer(), nullable=True),
        sa.Column("card_exp_year", sa.Integer(), nullable=True),
        sa.Column("billing_postal_code", sa.String(length=20), nullable=True),
        sa.Column("wallet_email", sa.String(length=320), nullable=True),
        sa.Column("bank_name", sa.String(length=120), nullable=True),
        sa.Column("bank_account_type", sa.String(length=16), nullable=True),
        sa.Column("bank_routing_number", sa.String(length=34), nullable=True),
        sa.Column("bank_account_last4", sa.String(length=4), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("provider_token", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_patient_payment_methods_account_id", "patient_payment_methods", ["account_id"])
    op.create_index("ix_patient_payment_methods_account_kind", "patient_payment_methods", ["account_id", "kind"])


def downgrade() -> None:
    op.drop_index("ix_patient_payment_methods_account_kind", table_name="patient_payment_methods")
    op.drop_index("ix_patient_payment_methods_account_id", table_name="patient_payment_methods")
    op.drop_table("patient_payment_methods")
