"""onboarding: the manager's electronic approval signature ("onboarding is finished")

Revision ID: 0015_onboarding_approval
Revises: 0014_onboarding_portal
Create Date: 2027-05-20 09:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0015_onboarding_approval"
down_revision = "0014_onboarding_portal"
branch_labels = None
depends_on = None

COLUMNS = (
    sa.Column("steps_completed_at", sa.DateTime(), nullable=True),
    sa.Column("approved_at", sa.DateTime(), nullable=True),
    sa.Column("approved_by", sa.String(length=36), nullable=True),
    sa.Column("approval_signer_name", sa.String(length=200), nullable=True),
    sa.Column("approval_signature_png", sa.Text(), nullable=True),
    sa.Column("approval_esign_reference", sa.String(length=120), nullable=True),
    sa.Column("approval_esign_provider", sa.String(length=32), nullable=True),
)


def upgrade() -> None:
    with op.batch_alter_table("onboardings") as batch:
        for col in COLUMNS:
            batch.add_column(col)
    # Onboardings completed before approvals existed count as approved-by-record (no signature).
    op.execute(
        "UPDATE onboardings SET steps_completed_at = completed_at, approved_at = completed_at "
        "WHERE completed_at IS NOT NULL"
    )


def downgrade() -> None:
    with op.batch_alter_table("onboardings") as batch:
        for col in reversed(COLUMNS):
            batch.drop_column(col.name)
