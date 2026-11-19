"""patients: the insurance section of the intake page — plan / group / policyholder details on
the record, and the photos of the insurance card (front / back) in their own table

Revision ID: 0020_insurance_cards
Revises: 0019_reminders
Create Date: 2027-07-15 09:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0020_insurance_cards"
down_revision = "0019_reminders"
branch_labels = None
depends_on = None

COLUMNS = (
    sa.Column("insurance_plan_name", sa.String(length=120), nullable=True),
    sa.Column("insurance_group_number", sa.String(length=64), nullable=True),
    sa.Column("insurance_policyholder_name", sa.String(length=120), nullable=True),
    sa.Column("insurance_policyholder_relationship", sa.String(length=16), nullable=True),
)


def upgrade() -> None:
    with op.batch_alter_table("patients") as batch:
        for col in COLUMNS:
            batch.add_column(col)
    op.create_table(
        "patient_insurance_cards",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("patient_id", sa.Integer(), sa.ForeignKey("patients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("content_type", sa.String(length=32), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("patient_id", "side", name="uq_patient_insurance_card_side"),
    )
    op.create_index("ix_patient_insurance_cards_patient_id", "patient_insurance_cards", ["patient_id"])


def downgrade() -> None:
    op.drop_index("ix_patient_insurance_cards_patient_id", table_name="patient_insurance_cards")
    op.drop_table("patient_insurance_cards")
    with op.batch_alter_table("patients") as batch:
        for col in reversed(COLUMNS):
            batch.drop_column(col.name)
