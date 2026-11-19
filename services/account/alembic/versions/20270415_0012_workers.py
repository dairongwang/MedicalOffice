"""workers: the medical office's staff roster and each worker's weekly availability

Revision ID: 0012_workers
Revises: 0011_patients
Create Date: 2027-04-15 09:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0012_workers"
down_revision = "0011_patients"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("first_name", sa.String(length=100), nullable=False),
        sa.Column("last_name", sa.String(length=100), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("specialty", sa.String(length=120), nullable=True),
        sa.Column("license_number", sa.String(length=64), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("employment_type", sa.String(length=16), nullable=True),
        sa.Column("emergency_contact_name", sa.String(length=120), nullable=True),
        sa.Column("emergency_contact_phone", sa.String(length=32), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("first_name_norm", sa.String(length=100), nullable=False),
        sa.Column("last_name_norm", sa.String(length=100), nullable=False),
        sa.Column("email_norm", sa.String(length=320), nullable=False),
        sa.Column("phone_norm", sa.String(length=32), nullable=True),
        sa.Column("license_norm", sa.String(length=64), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_workers_email_norm", "workers", ["email_norm"])
    op.create_index("ix_workers_phone_norm", "workers", ["phone_norm"])
    op.create_index("ix_workers_license_norm", "workers", ["license_norm"])
    op.create_index("ix_workers_name", "workers", ["last_name_norm", "first_name_norm"])

    op.create_table(
        "worker_availability",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("worker_id", sa.Integer(), sa.ForeignKey("workers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("available", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("worker_id", "weekday", name="uq_worker_weekday"),
    )
    op.create_index("ix_worker_availability_worker", "worker_availability", ["worker_id"])


def downgrade() -> None:
    op.drop_index("ix_worker_availability_worker", table_name="worker_availability")
    op.drop_table("worker_availability")
    for name in ("ix_workers_name", "ix_workers_license_norm", "ix_workers_phone_norm", "ix_workers_email_norm"):
        op.drop_index(name, table_name="workers")
    op.drop_table("workers")
