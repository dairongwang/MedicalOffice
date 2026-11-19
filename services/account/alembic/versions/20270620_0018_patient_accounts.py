"""patients: the patient account a record belongs to (role = patient, the patients' web app)

Revision ID: 0018_patient_accounts
Revises: 0017_appointment_links
Create Date: 2027-06-20 09:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0018_patient_accounts"
down_revision = "0017_appointment_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("patients") as batch:
        batch.add_column(sa.Column("account_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key("fk_patients_account", "accounts", ["account_id"], ["id"], ondelete="SET NULL")
        batch.create_index("ix_patients_account_id", ["account_id"])


def downgrade() -> None:
    with op.batch_alter_table("patients") as batch:
        batch.drop_index("ix_patients_account_id")
        batch.drop_constraint("fk_patients_account", type_="foreignkey")
        batch.drop_column("account_id")
