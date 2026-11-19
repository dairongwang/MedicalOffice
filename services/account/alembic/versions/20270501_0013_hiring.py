"""hiring: job applications (age / licence / background checks) and onboarding checklists

Revision ID: 0013_hiring
Revises: 0012_workers
Create Date: 2027-05-01 09:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0013_hiring"
down_revision = "0012_workers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_applications",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("first_name", sa.String(length=100), nullable=False),
        sa.Column("last_name", sa.String(length=100), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("date_of_birth", sa.Date(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("specialty", sa.String(length=120), nullable=True),
        sa.Column("employment_type", sa.String(length=16), nullable=True),
        sa.Column("available_from", sa.Date(), nullable=True),
        sa.Column("years_experience", sa.Integer(), nullable=True),
        sa.Column("license_number", sa.String(length=64), nullable=True),
        sa.Column("license_authority", sa.String(length=120), nullable=True),
        sa.Column("license_expires", sa.Date(), nullable=True),
        sa.Column("certifications", sa.Text(), nullable=True),
        sa.Column("cover_note", sa.Text(), nullable=True),
        sa.Column("emergency_contact_name", sa.String(length=120), nullable=True),
        sa.Column("emergency_contact_phone", sa.String(length=32), nullable=True),
        sa.Column("background_consent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="submitted"),
        sa.Column("age_check", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("age_note", sa.String(length=200), nullable=True),
        sa.Column("license_check", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("license_note", sa.String(length=500), nullable=True),
        sa.Column("license_checked_at", sa.DateTime(), nullable=True),
        sa.Column("license_checked_by", sa.String(length=36), nullable=True),
        sa.Column("background_check", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("background_note", sa.String(length=500), nullable=True),
        sa.Column("background_reference", sa.String(length=120), nullable=True),
        sa.Column("background_checked_at", sa.DateTime(), nullable=True),
        sa.Column("background_checked_by", sa.String(length=36), nullable=True),
        sa.Column("decision_note", sa.String(length=1000), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("decided_by", sa.String(length=36), nullable=True),
        sa.Column("worker_id", sa.Integer(), sa.ForeignKey("workers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("email_norm", sa.String(length=320), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_job_applications_email_norm", "job_applications", ["email_norm"])
    op.create_index("ix_job_applications_status", "job_applications", ["status"])

    op.create_table(
        "onboardings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("worker_id", sa.Integer(), sa.ForeignKey("workers.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "application_id", sa.Integer(), sa.ForeignKey("job_applications.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("worker_id", name="uq_onboarding_worker"),
    )
    op.create_table(
        "onboarding_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("onboarding_id", sa.Integer(), sa.ForeignKey("onboardings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.String(length=40), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("completed_by", sa.String(length=36), nullable=True),
        sa.UniqueConstraint("onboarding_id", "key", name="uq_onboarding_item"),
    )
    op.create_index("ix_onboarding_items_onboarding_id", "onboarding_items", ["onboarding_id"])


def downgrade() -> None:
    op.drop_index("ix_onboarding_items_onboarding_id", table_name="onboarding_items")
    op.drop_table("onboarding_items")
    op.drop_table("onboardings")
    op.drop_index("ix_job_applications_status", table_name="job_applications")
    op.drop_index("ix_job_applications_email_norm", table_name="job_applications")
    op.drop_table("job_applications")
