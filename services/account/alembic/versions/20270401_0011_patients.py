"""patients: the medical office's patient roster, filled through the public intake page

Revision ID: 0011_patients
Revises: 0010_otp_email
Create Date: 2027-04-01 09:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0011_patients"
down_revision = "0010_otp_email"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "patients",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("first_name", sa.String(length=100), nullable=False),
        sa.Column("last_name", sa.String(length=100), nullable=False),
        sa.Column("date_of_birth", sa.Date(), nullable=False),
        sa.Column("sex", sa.String(length=16), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("address_line", sa.String(length=200), nullable=True),
        sa.Column("city", sa.String(length=100), nullable=True),
        sa.Column("state", sa.String(length=100), nullable=True),
        sa.Column("postal_code", sa.String(length=20), nullable=True),
        sa.Column("country", sa.String(length=100), nullable=True),
        sa.Column("insurance_provider", sa.String(length=120), nullable=True),
        sa.Column("insurance_member_id", sa.String(length=64), nullable=True),
        sa.Column("emergency_contact_name", sa.String(length=120), nullable=True),
        sa.Column("emergency_contact_phone", sa.String(length=32), nullable=True),
        sa.Column("reason_for_visit", sa.Text(), nullable=True),
        sa.Column("first_name_norm", sa.String(length=100), nullable=False),
        sa.Column("last_name_norm", sa.String(length=100), nullable=False),
        sa.Column("email_norm", sa.String(length=320), nullable=True),
        sa.Column("phone_norm", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_patients_email_norm", "patients", ["email_norm"])
    op.create_index("ix_patients_phone_norm", "patients", ["phone_norm"])
    op.create_index("ix_patients_identity", "patients", ["last_name_norm", "first_name_norm", "date_of_birth"])


def downgrade() -> None:
    op.drop_index("ix_patients_identity", table_name="patients")
    op.drop_index("ix_patients_phone_norm", table_name="patients")
    op.drop_index("ix_patients_email_norm", table_name="patients")
    op.drop_table("patients")
