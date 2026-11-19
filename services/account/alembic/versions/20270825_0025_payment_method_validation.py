"""patient payment methods: validation by a small-amount authorization — status on the method,
one row per attempt

Revision ID: 0025_payment_method_validation
Revises: 0024_payment_methods_tokenized
Create Date: 2027-08-25 09:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0025_payment_method_validation"
down_revision = "0024_payment_methods_tokenized"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("patient_payment_methods") as batch:
        batch.add_column(sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"))
        batch.add_column(sa.Column("validated_at", sa.DateTime(), nullable=True))
    op.create_table(
        "patient_payment_validations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("method_id", sa.Integer(), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="USD"),
        sa.Column("result", sa.String(length=16), nullable=False),
        sa.Column("message", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("reference", sa.String(length=120), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("triggered_by", sa.String(length=16), nullable=False),
        sa.Column("actor_account_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["method_id"], ["patient_payment_methods.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_patient_payment_validations_method_id", "patient_payment_validations", ["method_id"])


def downgrade() -> None:
    op.drop_index("ix_patient_payment_validations_method_id", table_name="patient_payment_validations")
    op.drop_table("patient_payment_validations")
    with op.batch_alter_table("patient_payment_methods") as batch:
        batch.drop_column("validated_at")
        batch.drop_column("status")
