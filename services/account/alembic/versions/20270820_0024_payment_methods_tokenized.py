"""patient payment methods: every kind tokenized — masked wallet e-mail instead of the address,
last four digits of the routing number instead of the number, a keyed fingerprint per entry

Revision ID: 0024_payment_methods_tokenized
Revises: 0023_patient_payment_methods
Create Date: 2027-08-20 09:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0024_payment_methods_tokenized"
down_revision = "0023_patient_payment_methods"
branch_labels = None
depends_on = None


def _mask_email(address: str | None) -> str | None:
    if not address or "@" not in address:
        return None
    local, domain = address.rsplit("@", 1)
    return f"{local[:2] if len(local) > 3 else local[:1]}•••@{domain}"


def upgrade() -> None:
    with op.batch_alter_table("patient_payment_methods") as batch:
        batch.add_column(sa.Column("wallet_email_masked", sa.String(length=320), nullable=True))
        batch.add_column(sa.Column("bank_routing_last4", sa.String(length=4), nullable=True))
        batch.add_column(sa.Column("fingerprint", sa.String(length=64), nullable=True))
    # Existing rows: keep only what the new columns hold (the full values are dropped below).
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, wallet_email, bank_routing_number FROM patient_payment_methods")).fetchall()
    for row_id, email, routing in rows:
        conn.execute(
            sa.text(
                "UPDATE patient_payment_methods SET wallet_email_masked = :m, bank_routing_last4 = :r WHERE id = :id"
            ),
            {"m": _mask_email(email), "r": routing[-4:] if routing else None, "id": row_id},
        )
    with op.batch_alter_table("patient_payment_methods") as batch:
        batch.drop_column("wallet_email")
        batch.drop_column("bank_routing_number")
    op.create_index("ix_patient_payment_methods_fingerprint", "patient_payment_methods", ["fingerprint"])


def downgrade() -> None:
    op.drop_index("ix_patient_payment_methods_fingerprint", table_name="patient_payment_methods")
    with op.batch_alter_table("patient_payment_methods") as batch:
        batch.add_column(sa.Column("wallet_email", sa.String(length=320), nullable=True))
        batch.add_column(sa.Column("bank_routing_number", sa.String(length=34), nullable=True))
        batch.drop_column("fingerprint")
        batch.drop_column("bank_routing_last4")
        batch.drop_column("wallet_email_masked")
