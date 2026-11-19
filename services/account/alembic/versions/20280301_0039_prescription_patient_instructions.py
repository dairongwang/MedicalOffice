"""Plain-language instructions for the patient on each prescribed medication (shown on the patient
dashboard's "My medicines" tabs and printed on the prescription)

Revision ID: 0039_rx_patient_instructions
Revises: 0038_patient_reports
Create Date: 2028-03-01 09:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0039_rx_patient_instructions"
down_revision = "0038_patient_reports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("prescription_items") as batch:
        batch.add_column(sa.Column("patient_instructions", sa.String(length=1000), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("prescription_items") as batch:
        batch.drop_column("patient_instructions")
