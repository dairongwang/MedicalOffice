"""The patient's pharmacy (asked at the visit); referrals written by the provider — signed letters for the
specialist, their hand-overs (e-mail to the specialist / the patient, the office told, PDF)

Revision ID: 0036_referral_letters
Revises: 0035_prescriptions
Create Date: 2028-01-20 09:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0036_referral_letters"
down_revision = "0035_prescriptions"
branch_labels = None
depends_on = None

REFERRAL_COLUMNS = (
    "withdraw_reason",
    "withdrawn_at",
    "verification",
    "signed_at",
    "counterpart_address",
    "counterpart_fax",
    "counterpart_phone",
    "counterpart_email",
    "include_results",
    "clinical_summary",
    "question",
    "diagnosis_id",
    "urgency",
    "worker_id",
)


def upgrade() -> None:
    with op.batch_alter_table("pharmacies") as batch:
        batch.add_column(sa.Column("source", sa.String(length=16), nullable=False, server_default="office"))
        batch.add_column(sa.Column("added_by", sa.String(length=200), nullable=True))
    with op.batch_alter_table("patients") as batch:
        batch.add_column(sa.Column("preferred_pharmacy_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("preferred_pharmacy_set_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("preferred_pharmacy_set_by", sa.String(length=200), nullable=True))
        batch.create_foreign_key(
            "fk_patients_preferred_pharmacy_id", "pharmacies", ["preferred_pharmacy_id"], ["id"], ondelete="SET NULL"
        )
    with op.batch_alter_table("patient_referrals") as batch:
        batch.add_column(sa.Column("worker_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("urgency", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("diagnosis_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("question", sa.Text(), nullable=True))
        batch.add_column(sa.Column("clinical_summary", sa.Text(), nullable=True))
        batch.add_column(sa.Column("include_results", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("counterpart_email", sa.String(length=320), nullable=True))
        batch.add_column(sa.Column("counterpart_phone", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("counterpart_fax", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("counterpart_address", sa.String(length=300), nullable=True))
        batch.add_column(sa.Column("signed_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("verification", sa.String(length=40), nullable=True))
        batch.add_column(sa.Column("withdrawn_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("withdraw_reason", sa.String(length=500), nullable=True))
        batch.create_foreign_key(
            "fk_patient_referrals_worker_id", "workers", ["worker_id"], ["id"], ondelete="SET NULL"
        )
        batch.create_foreign_key(
            "fk_patient_referrals_diagnosis_id", "visit_diagnoses", ["diagnosis_id"], ["id"], ondelete="SET NULL"
        )
    op.create_table(
        "referral_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "referral_id", sa.Integer(), sa.ForeignKey("patient_referrals.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("recipient", sa.String(length=16), nullable=False),
        sa.Column("destination", sa.String(length=320), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("by_account", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_referral_deliveries_referral_id", "referral_deliveries", ["referral_id"])


def downgrade() -> None:
    op.drop_index("ix_referral_deliveries_referral_id", table_name="referral_deliveries")
    op.drop_table("referral_deliveries")
    op.execute("UPDATE patient_referrals SET status = 'closed' WHERE status = 'withdrawn'")
    with op.batch_alter_table("patient_referrals") as batch:
        batch.drop_constraint("fk_patient_referrals_diagnosis_id", type_="foreignkey")
        batch.drop_constraint("fk_patient_referrals_worker_id", type_="foreignkey")
        for column in REFERRAL_COLUMNS:
            batch.drop_column(column)
    with op.batch_alter_table("patients") as batch:
        batch.drop_constraint("fk_patients_preferred_pharmacy_id", type_="foreignkey")
        batch.drop_column("preferred_pharmacy_set_by")
        batch.drop_column("preferred_pharmacy_set_at")
        batch.drop_column("preferred_pharmacy_id")
    with op.batch_alter_table("pharmacies") as batch:
        batch.drop_column("added_by")
        batch.drop_column("source")
