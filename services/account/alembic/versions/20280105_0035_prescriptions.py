"""Diagnoses and prescriptions written by the provider at a visit; the pharmacies prescriptions are
e-mailed to; every hand-over of a prescription (e-mail / PDF)

Revision ID: 0035_prescriptions
Revises: 0034_clinical_record
Create Date: 2028-01-05 09:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0035_prescriptions"
down_revision = "0034_clinical_record"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pharmacies",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("fax", sa.String(length=32), nullable=True),
        sa.Column("address", sa.String(length=300), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "visit_diagnoses",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("appointment_id", sa.Integer(), sa.ForeignKey("appointments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("patient_id", sa.Integer(), sa.ForeignKey("patients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("worker_id", sa.Integer(), sa.ForeignKey("workers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("description", sa.String(length=300), nullable=False),
        sa.Column("code", sa.String(length=10), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.Column(
            "health_item_id",
            sa.Integer(),
            sa.ForeignKey("patient_health_items.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_visit_diagnoses_appointment_id", "visit_diagnoses", ["appointment_id"])
    op.create_index("ix_visit_diagnoses_patient_id", "visit_diagnoses", ["patient_id"])
    op.create_table(
        "prescriptions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("patient_id", sa.Integer(), sa.ForeignKey("patients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("appointment_id", sa.Integer(), sa.ForeignKey("appointments.id", ondelete="SET NULL"), nullable=True),
        sa.Column("prescriber_id", sa.Integer(), sa.ForeignKey("workers.id", ondelete="SET NULL"), nullable=True),
        sa.Column(
            "diagnosis_id", sa.Integer(), sa.ForeignKey("visit_diagnoses.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="issued"),
        sa.Column("note_to_pharmacist", sa.String(length=1000), nullable=True),
        sa.Column("allergy_override", sa.String(length=500), nullable=True),
        sa.Column("verification", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("issued_at", sa.DateTime(), nullable=False),
        sa.Column("voided_at", sa.DateTime(), nullable=True),
        sa.Column("void_reason", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_prescriptions_patient_id", "prescriptions", ["patient_id"])
    op.create_index("ix_prescriptions_appointment_id", "prescriptions", ["appointment_id"])
    op.create_table(
        "prescription_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "prescription_id", sa.Integer(), sa.ForeignKey("prescriptions.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("position", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("drug", sa.String(length=200), nullable=False),
        sa.Column("strength", sa.String(length=60), nullable=True),
        sa.Column("form", sa.String(length=40), nullable=False, server_default="tablet"),
        sa.Column("sig", sa.String(length=500), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("quantity_unit", sa.String(length=30), nullable=False, server_default="tablets"),
        sa.Column("refills", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("substitution_allowed", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "health_item_id",
            sa.Integer(),
            sa.ForeignKey("patient_health_items.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("added_health_item", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_prescription_items_prescription_id", "prescription_items", ["prescription_id"])
    op.create_table(
        "prescription_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "prescription_id", sa.Integer(), sa.ForeignKey("prescriptions.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("pharmacy_id", sa.Integer(), sa.ForeignKey("pharmacies.id", ondelete="SET NULL"), nullable=True),
        sa.Column("destination", sa.String(length=320), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("by_account", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_prescription_deliveries_prescription_id", "prescription_deliveries", ["prescription_id"])


def downgrade() -> None:
    op.drop_index("ix_prescription_deliveries_prescription_id", table_name="prescription_deliveries")
    op.drop_table("prescription_deliveries")
    op.drop_index("ix_prescription_items_prescription_id", table_name="prescription_items")
    op.drop_table("prescription_items")
    op.drop_index("ix_prescriptions_appointment_id", table_name="prescriptions")
    op.drop_index("ix_prescriptions_patient_id", table_name="prescriptions")
    op.drop_table("prescriptions")
    op.drop_index("ix_visit_diagnoses_patient_id", table_name="visit_diagnoses")
    op.drop_index("ix_visit_diagnoses_appointment_id", table_name="visit_diagnoses")
    op.drop_table("visit_diagnoses")
    op.drop_table("pharmacies")
