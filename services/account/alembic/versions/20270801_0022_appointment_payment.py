"""appointments: who pays — the coverage check at booking, or the patient's self-pay agreement

Revision ID: 0022_appointment_payment
Revises: 0021_insurance_verification
Create Date: 2027-08-01 09:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0022_appointment_payment"
down_revision = "0021_insurance_verification"
branch_labels = None
depends_on = None

COLUMNS = (
    sa.Column("payment", sa.String(length=16), nullable=False, server_default="insurance"),
    sa.Column("coverage_status", sa.String(length=16), nullable=True),
    sa.Column("coverage_verification_id", sa.Integer(), nullable=True),
    sa.Column("self_pay_agreed_at", sa.DateTime(), nullable=True),
)


def upgrade() -> None:
    with op.batch_alter_table("appointments") as batch:
        for col in COLUMNS:
            batch.add_column(col)
        batch.create_foreign_key(
            "fk_appointments_coverage_verification",
            "patient_insurance_verifications",
            ["coverage_verification_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("appointments") as batch:
        batch.drop_constraint("fk_appointments_coverage_verification", type_="foreignkey")
        for col in reversed(COLUMNS):
            batch.drop_column(col.name)
