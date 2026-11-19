"""payment validations outlive the method: snapshot account / kind / display on each row and
detach (SET NULL) instead of deleting when the method is removed — the payment history is
independent of the lifetime of a card, wallet or bank account

Revision ID: 0027_validations_outlive_methods
Revises: 0026_patient_payments
Create Date: 2027-09-05 09:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0027_validations_outlive_methods"
down_revision = "0026_patient_payments"
branch_labels = None
depends_on = None

TABLE = "patient_payment_validations"
BRANDS = {
    "visa": "Visa",
    "mastercard": "Mastercard",
    "amex": "American Express",
    "discover": "Discover",
    "diners": "Diners Club",
    "jcb": "JCB",
    "unionpay": "UnionPay",
}


def _display(kind, brand, last4, month, year, email, bank, acct_type, acct_last4) -> str:
    if kind == "card":
        expiry = f" · {month:02d}/{year}" if month and year else ""
        return f"{BRANDS.get(brand or '', 'Card')} •••• {last4}{expiry}"
    if kind in ("stripe", "paypal"):
        return f"{'Stripe' if kind == 'stripe' else 'PayPal'} · {email}"
    return f"{bank or 'Bank'} · {(acct_type or 'account').capitalize()} •••• {acct_last4}"


def _columns(*, detached: bool) -> list[sa.Column]:
    """The table's columns — after (detached=True) and before this revision."""
    cols = [
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "method_id",
            sa.Integer(),
            sa.ForeignKey("patient_payment_methods.id", ondelete="SET NULL" if detached else "CASCADE"),
            nullable=detached,
        ),
    ]
    if detached:
        cols += [
            sa.Column(
                "account_id", sa.String(length=36), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
            ),
            sa.Column("method_kind", sa.String(length=16), nullable=False),
            sa.Column("method_display", sa.String(length=120), nullable=False),
        ]
    cols += [
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="USD"),
        sa.Column("result", sa.String(length=16), nullable=False),
        sa.Column("message", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("reference", sa.String(length=120), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("triggered_by", sa.String(length=16), nullable=False),
        sa.Column("actor_account_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    ]
    return cols


COMMON = (
    "id, method_id, amount_cents, currency, result, message, reference, provider, triggered_by, "
    "actor_account_id, created_at"
)


def _rebuild(*, detached: bool, rows_sql: str, params_rows: list[dict] | None = None) -> None:
    """Create the new shape next to the old table, copy, swap. Portable (no constraint names)."""
    tmp = TABLE + "_next"
    op.create_table(tmp, *_columns(detached=detached))
    conn = op.get_bind()
    if params_rows is not None:
        if params_rows:
            conn.execute(sa.text(rows_sql), params_rows)
    else:
        conn.execute(sa.text(rows_sql))
    op.drop_index(f"ix_{TABLE}_method_id", table_name=TABLE)
    if not detached:
        op.drop_index(f"ix_{TABLE}_account_id", table_name=TABLE)
    op.drop_table(TABLE)
    op.rename_table(tmp, TABLE)
    op.create_index(f"ix_{TABLE}_method_id", TABLE, ["method_id"])
    if detached:
        op.create_index(f"ix_{TABLE}_account_id", TABLE, ["account_id"])


def upgrade() -> None:
    conn = op.get_bind()
    methods = {
        m[0]: (m[1], m[2], _display(*m[2:]))
        for m in conn.execute(
            sa.text(
                "SELECT id, account_id, kind, card_brand, card_last4, card_exp_month, card_exp_year, "
                "wallet_email_masked, bank_name, bank_account_type, bank_account_last4 FROM patient_payment_methods"
            )
        ).fetchall()
    }
    rows = []
    for v in conn.execute(sa.text(f"SELECT {COMMON} FROM {TABLE}")).mappings():
        snap = methods.get(v["method_id"])
        if snap is None:  # an orphan (should not exist with the old cascade) — nothing to attach it to
            continue
        rows.append({**dict(v), "account_id": snap[0], "method_kind": snap[1], "method_display": snap[2]})
    _rebuild(
        detached=True,
        rows_sql=(
            f"INSERT INTO {TABLE}_next ({COMMON}, account_id, method_kind, method_display) VALUES "
            "(:id, :method_id, :amount_cents, :currency, :result, :message, :reference, :provider, :triggered_by, "
            ":actor_account_id, :created_at, :account_id, :method_kind, :method_display)"
        ),
        params_rows=rows,
    )


def downgrade() -> None:
    # Rows of removed methods cannot be kept by the old schema (CASCADE, NOT NULL): they go.
    _rebuild(
        detached=False,
        rows_sql=f"INSERT INTO {TABLE}_next ({COMMON}) SELECT {COMMON} FROM {TABLE} WHERE method_id IS NOT NULL",
    )
