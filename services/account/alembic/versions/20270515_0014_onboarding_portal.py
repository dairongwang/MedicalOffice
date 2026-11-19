"""onboarding portal: time-limited invite links (with delivery log) and the new hire's submissions
(forms and electronic signatures)

Revision ID: 0014_onboarding_portal
Revises: 0013_hiring
Create Date: 2027-05-15 09:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0014_onboarding_portal"
down_revision = "0013_hiring"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "onboarding_invites",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("onboarding_id", sa.Integer(), sa.ForeignKey("onboardings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("link", sa.String(length=2048), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("channels", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("sent_to", sa.String(length=400), nullable=False, server_default=""),
        sa.Column("delivery_note", sa.String(length=500), nullable=True),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("first_opened_at", sa.DateTime(), nullable=True),
        sa.Column("last_opened_at", sa.DateTime(), nullable=True),
        sa.Column("opens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_onboarding_invites_onboarding_id", "onboarding_invites", ["onboarding_id"])

    op.create_table(
        "onboarding_submissions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("onboarding_id", sa.Integer(), sa.ForeignKey("onboardings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.String(length=40), nullable=False),
        sa.Column("data", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("document_version", sa.String(length=16), nullable=True),
        sa.Column("signer_name", sa.String(length=200), nullable=True),
        sa.Column("signature_png", sa.Text(), nullable=True),
        sa.Column("signed_at", sa.DateTime(), nullable=True),
        sa.Column("esign_reference", sa.String(length=120), nullable=True),
        sa.Column("esign_provider", sa.String(length=32), nullable=True),
        sa.Column("submitted_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("onboarding_id", "key", name="uq_onboarding_submission"),
    )
    op.create_index("ix_onboarding_submissions_onboarding_id", "onboarding_submissions", ["onboarding_id"])


def downgrade() -> None:
    op.drop_index("ix_onboarding_submissions_onboarding_id", table_name="onboarding_submissions")
    op.drop_table("onboarding_submissions")
    op.drop_index("ix_onboarding_invites_onboarding_id", table_name="onboarding_invites")
    op.drop_table("onboarding_invites")
