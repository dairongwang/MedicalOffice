"""patients: insurance eligibility & benefits checks (coverage, effective dates, copay,
deductible, coinsurance, referral / prior authorization) — one row per check

Revision ID: 0021_insurance_verification
Revises: 0020_insurance_cards
Create Date: 2027-07-20 09:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0021_insurance_verification"
down_revision = "0020_insurance_cards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "patient_insurance_verifications",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("patient_id", sa.Integer(), sa.ForeignKey("patients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("message", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("triggered_by", sa.String(length=16), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("reference", sa.String(length=120), nullable=True),
        sa.Column("payer_name", sa.String(length=120), nullable=True),
        sa.Column("plan_name", sa.String(length=120), nullable=True),
        sa.Column("plan_type", sa.String(length=16), nullable=True),
        sa.Column("member_id", sa.String(length=64), nullable=True),
        sa.Column("group_number", sa.String(length=64), nullable=True),
        sa.Column("coverage_start", sa.Date(), nullable=True),
        sa.Column("coverage_end", sa.Date(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="USD"),
        sa.Column("copay_cents", sa.Integer(), nullable=True),
        sa.Column("specialist_copay_cents", sa.Integer(), nullable=True),
        sa.Column("deductible_cents", sa.Integer(), nullable=True),
        sa.Column("deductible_met_cents", sa.Integer(), nullable=True),
        sa.Column("deductible_status", sa.String(length=16), nullable=True),
        sa.Column("coinsurance_percent", sa.Integer(), nullable=True),
        sa.Column("out_of_pocket_max_cents", sa.Integer(), nullable=True),
        sa.Column("out_of_pocket_met_cents", sa.Integer(), nullable=True),
        sa.Column("referral_required", sa.Boolean(), nullable=True),
        sa.Column("referral_notes", sa.String(length=500), nullable=True),
        sa.Column("prior_auth_required", sa.Boolean(), nullable=True),
        sa.Column("prior_auth_notes", sa.String(length=500), nullable=True),
        sa.Column("raw", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("checked_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_patient_insurance_verifications_patient_id", "patient_insurance_verifications", ["patient_id"])


def downgrade() -> None:
    op.drop_index("ix_patient_insurance_verifications_patient_id", table_name="patient_insurance_verifications")
    op.drop_table("patient_insurance_verifications")
