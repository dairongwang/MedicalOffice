"""Patient payment methods — what a patient keeps on file to pay the office (copays, self-pay
visits, statements), managed from the patient dashboard of the patients' app and from the
office administration dashboard.

    A signed-in patient account (auth.patient_principal):
    GET    /api/v1/me/payment-methods                 the account's methods (default first)
    POST   /api/v1/me/payment-methods                 add one (201) — validated at once
    GET    /api/v1/me/payment-methods/{id}            one method
    PUT    /api/v1/me/payment-methods/{id}            update it (the kind cannot change)
    DELETE /api/v1/me/payment-methods/{id}            remove it (204)
    POST   /api/v1/me/payment-methods/{id}/default    make it the default (active methods only)
    POST   /api/v1/me/payment-methods/{id}/validate   run the small-amount authorization again
    GET    /api/v1/me/payment-methods/{id}/validations   every attempt so far

    Administrators (auth.admin_principal) — the same, by patient record, on the patient account the
    record is linked to (every action lands in the audit log):
    GET    /api/v1/patients/{patient_id}/payment-methods            {has_account, methods}
    POST   /api/v1/patients/{patient_id}/payment-methods            add on the patient's behalf
    PUT/DELETE /api/v1/patients/{patient_id}/payment-methods/{id}
    POST   /api/v1/patients/{patient_id}/payment-methods/{id}/default
    POST   /api/v1/patients/{patient_id}/payment-methods/{id}/validate
    GET    /api/v1/patients/{patient_id}/payment-methods/{id}/validations

Four kinds (``models.PAYMENT_METHOD_KINDS``) — **every one tokenized**: the payment detail is
validated, handed to the vault (``tokenize``) and discarded; what stays is a token, a keyed
fingerprint and just enough to recognise the entry:

    card     a credit / debit card — the number is checked (length, Luhn); kept: brand, last
             four digits, expiry, billing postal code. Number and security code are discarded.
    stripe   the patient's Stripe (Link) wallet — the e-mail address is tokenized; kept masked
             ("pa•••@example.com").
    paypal   the patient's PayPal account — same as Stripe.
    bank     a bank account — the routing number is checked (ABA checksum for the 9-digit US
             form) and the account number tokenized; kept: bank name, checking / savings, last
             four digits of the routing number and of the account number.

The same card / wallet / account cannot be added twice to an account (``fingerprint``, an HMAC
of the detail keyed with TOKEN_SECRET — not reversible, only comparable).

**Validation.** A method is usable only once it is ``active``: a small amount
(config.PAYMENT_VALIDATION_AMOUNT_CENTS, $1.00 by default) is authorized against the vault
token and released at once — nothing is charged (``authorize``: the vault seam; the built-in
fake answers deterministically, see there). That happens when the method is added and whenever
its detail changes (``PAYMENT_VALIDATE_ON_SAVE``), and on demand — the patient can retry after
a failure, the office can re-validate any method at any time. Every attempt is a
``PaymentValidation`` row; the newest one decides the status (pending → active / failed). Only
an active method can be the default; when a method turns active and the account has no default,
it becomes the default.

All of this belongs to the signed-in *patient account*, not to a patient record: one parent pays
for every child on the account. The office reaches it through any record linked to the account.

Security: no card number, security code, bank account / routing number or wallet e-mail
address is ever written to the database, the log or a response
(``tests/test_patient_payment_methods.py`` checks the database file for the test values).
``tokenize()`` / ``authorize()`` are the seams for a real vault: a deployment that takes payments
hands the detail to the payment provider (Stripe SetupIntent / PaymentIntent with manual
capture, PayPal Vault / authorization, micro-deposits for bank accounts) and keeps the
provider's references; until then the references are opaque, the provider is "fake" (outside
prod) — the dashboards say so — and in prod without a vault a method cannot become active.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

import accounts
import auth
import config
import models
import schemas
from database import get_db

log = logging.getLogger("account.patient_payment_methods")

me_router = APIRouter(prefix="/me/payment-methods", tags=["me"])
admin_router = APIRouter(prefix="/patients/{patient_id}/payment-methods", tags=["patients"])

MAX_METHODS_PER_ACCOUNT = 10

BRAND_LABELS = {
    "visa": "Visa",
    "mastercard": "Mastercard",
    "amex": "American Express",
    "discover": "Discover",
    "diners": "Diners Club",
    "jcb": "JCB",
    "unionpay": "UnionPay",
    "other": "Card",
}
KIND_LABELS = {"card": "Card", "stripe": "Stripe", "paypal": "PayPal", "bank": "Bank account"}


# ---------------------------------------------------------------------------- validation helpers


def luhn_ok(number: str) -> bool:
    """The check digit of a card number (ISO/IEC 7812)."""
    if not number.isdigit() or len(number) < 12:
        return False
    total = 0
    for i, ch in enumerate(reversed(number)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def card_brand(number: str) -> str:
    """Brand from the leading digits (the usual IIN ranges; "other" when none matches)."""
    if number.startswith("4"):
        return "visa"
    two = int(number[:2]) if len(number) >= 2 else 0
    four = int(number[:4]) if len(number) >= 4 else 0
    if 51 <= two <= 55 or 2221 <= four <= 2720:
        return "mastercard"
    if two in (34, 37):
        return "amex"
    if number.startswith("6011") or number.startswith("65") or 644 <= int(number[:3] or 0) <= 649:
        return "discover"
    if two in (36, 38, 39) or number.startswith("300") or number.startswith("305"):
        return "diners"
    if 3528 <= four <= 3589:
        return "jcb"
    if number.startswith("62"):
        return "unionpay"
    return "other"


def aba_ok(routing: str) -> bool:
    """Checksum of a 9-digit US ABA routing number."""
    if len(routing) != 9 or not routing.isdigit():
        return False
    weights = (3, 7, 1, 3, 7, 1, 3, 7, 1)
    return sum(int(d) * w for d, w in zip(routing, weights, strict=True)) % 10 == 0


def routing_number_ok(routing: str) -> bool:
    """9 digits → must pass the ABA checksum; otherwise 6–11 letters/digits (sort codes, BSBs,
    transit numbers) are accepted as typed."""
    compact = routing.replace(" ", "").replace("-", "")
    if compact.isdigit() and len(compact) == 9:
        return aba_ok(compact)
    return 6 <= len(compact) <= 11 and compact.isalnum()


def card_expired(month: int | None, year: int | None, today: date | None = None) -> bool:
    if month is None or year is None:
        return False
    today = today or date.today()
    return (year, month) < (today.year, today.month)


# ---------------------------------------------------------------------------- tokenization seam

TOKEN_PREFIX = {"card": "pm", "bank": "ba", "stripe": "wl", "paypal": "wl"}


def tokenize(kind: str, secret: str) -> tuple[str, str]:
    """Hand the payment detail (card number, bank routing + account number, wallet e-mail) to
    the vault and get a reference back: (provider, token). The detail is not kept anywhere on
    this side.

    Real vault integrations (Stripe SetupIntent / Customer, PayPal Vault, a clearing house) plug
    in here; today the reference is an opaque random id — "fake" outside prod, "reference" in
    prod — so that nothing in the product pretends to be able to charge it."""
    del secret  # validated by the caller; deliberately not used beyond this point
    provider = "fake" if config.BILLING_FAKE_PROVIDERS else "reference"
    return provider, f"{TOKEN_PREFIX[kind]}_{provider}_{secrets.token_hex(12)}"


def fingerprint(kind: str, secret: str) -> str:
    """Keyed hash of the detail (HMAC-SHA256 with TOKEN_SECRET): lets the server tell "this card
    is already on file" without being able to get the number back."""
    return hmac.new(config.TOKEN_SECRET.encode("utf-8"), f"{kind}:{secret}".encode(), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------- masking


def mask_email(address: str) -> str:
    """``pat.XXXX@example.com`` → ``pa•••@example.com`` (one letter for very short names)."""
    local, _, domain = address.partition("@")
    keep = local[:2] if len(local) > 3 else local[:1]
    return f"{keep}•••@{domain}"


def mask_routing(last4: str | None) -> str | None:
    return f"•••••{last4}" if last4 else None


# ---------------------------------------------------------------------------- output


def display(m: models.PatientPaymentMethod) -> str:
    if m.kind == "card":
        brand = BRAND_LABELS.get(m.card_brand or "other", "Card")
        expiry = f" · {m.card_exp_month:02d}/{m.card_exp_year}" if m.card_exp_month and m.card_exp_year else ""
        return f"{brand} •••• {m.card_last4}{expiry}"
    if m.kind in ("stripe", "paypal"):
        return f"{KIND_LABELS[m.kind]} · {m.wallet_email_masked}"
    account_type = (m.bank_account_type or "account").capitalize()
    return f"{m.bank_name or 'Bank'} · {account_type} •••• {m.bank_account_last4}"


def out(db: Session, m: models.PatientPaymentMethod) -> schemas.PatientPaymentMethodOut:
    latest = latest_validation(db, m)
    return schemas.PatientPaymentMethodOut(
        id=m.id,
        kind=m.kind,
        label=m.label,
        holder_name=m.holder_name,
        is_default=m.is_default,
        status=m.status,
        validated_at=m.validated_at,
        validation=validation_out(latest) if latest else None,
        display=display(m),
        expired=m.kind == "card" and card_expired(m.card_exp_month, m.card_exp_year),
        card_brand=m.card_brand,
        card_last4=m.card_last4,
        card_exp_month=m.card_exp_month,
        card_exp_year=m.card_exp_year,
        billing_postal_code=m.billing_postal_code,
        wallet_email_masked=m.wallet_email_masked,
        bank_name=m.bank_name,
        bank_account_type=m.bank_account_type,
        bank_routing_masked=mask_routing(m.bank_routing_last4),
        bank_account_last4=m.bank_account_last4,
        provider=m.provider,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


# ---------------------------------------------------------------------------- the rows


def list_methods(db: Session, account: models.Account) -> list[models.PatientPaymentMethod]:
    return (
        db.execute(
            select(models.PatientPaymentMethod)
            .where(models.PatientPaymentMethod.account_id == account.id)
            .order_by(models.PatientPaymentMethod.is_default.desc(), models.PatientPaymentMethod.id)
        )
        .scalars()
        .all()
    )


def _own(db: Session, account: models.Account, method_id: int) -> models.PatientPaymentMethod:
    row = db.get(models.PatientPaymentMethod, method_id)
    if row is None or row.account_id != account.id:
        raise HTTPException(status_code=404, detail="Payment method not found.")
    return row


def _apply(
    db: Session, m: models.PatientPaymentMethod, body: schemas.PatientPaymentMethodIn, *, creating: bool
) -> None:
    """Copy the request onto the row — validating the payment detail, tokenizing it and keeping
    only the recognisable part. Raises 422 with a field-prefixed message like the pydantic
    validators do, 409 when the same detail is already on file for this account."""
    m.label = body.label
    m.holder_name = body.holder_name
    if body.kind == "card":
        if body.card_number is None:
            if creating:
                raise HTTPException(status_code=422, detail="card_number: the card number is required")
        else:
            if not luhn_ok(body.card_number):
                raise HTTPException(status_code=422, detail="card_number: this is not a valid card number")
            _vault(db, m, "card", body.card_number)
            m.card_brand = card_brand(body.card_number)
            m.card_last4 = body.card_number[-4:]
        if card_expired(body.card_exp_month, body.card_exp_year):
            raise HTTPException(status_code=422, detail="card_exp_year: this card has expired")
        m.card_exp_month = body.card_exp_month
        m.card_exp_year = body.card_exp_year
        m.billing_postal_code = body.billing_postal_code
    elif body.kind in ("stripe", "paypal"):
        if body.wallet_email is None:
            if creating:
                raise HTTPException(
                    status_code=422,
                    detail=f"wallet_email: the e-mail address of your {KIND_LABELS[body.kind]} account is required",
                )
        else:
            email = body.wallet_email.lower()
            _vault(db, m, body.kind, email)
            m.wallet_email_masked = mask_email(email)
    else:  # bank
        routing = (body.bank_routing_number or "").replace(" ", "").replace("-", "").upper()
        if not routing and creating:
            raise HTTPException(status_code=422, detail="bank_routing_number: the routing number is required")
        if routing and not routing_number_ok(routing):
            raise HTTPException(
                status_code=422,
                detail="bank_routing_number: this is not a valid routing number (9 digits for a US bank)",
            )
        if body.bank_account_number is None:
            if creating:
                raise HTTPException(status_code=422, detail="bank_account_number: the account number is required")
            if routing and routing[-4:] != m.bank_routing_last4:
                raise HTTPException(
                    status_code=422,
                    detail="bank_account_number: enter the account number too when changing the routing number",
                )
        else:
            if not routing:
                raise HTTPException(status_code=422, detail="bank_routing_number: the routing number is required")
            _vault(db, m, "bank", f"{routing}/{body.bank_account_number}")
            m.bank_routing_last4 = routing[-4:]
            m.bank_account_last4 = body.bank_account_number[-4:]
        m.bank_name = body.bank_name
        m.bank_account_type = body.bank_account_type


def _vault(db: Session, m: models.PatientPaymentMethod, kind: str, secret: str) -> None:
    """Tokenize the detail onto the row — unless the same detail is already on file."""
    fp = fingerprint(kind, secret)
    twin = db.execute(
        select(models.PatientPaymentMethod).where(
            models.PatientPaymentMethod.account_id == m.account_id,
            models.PatientPaymentMethod.fingerprint == fp,
            models.PatientPaymentMethod.id != (m.id or 0),
        )
    ).scalar_one_or_none()
    if twin is not None:
        raise HTTPException(
            status_code=409,
            detail=f"This {KIND_LABELS[kind].lower()} is already on file ({display(twin)}).",
        )
    m.provider, m.provider_token = tokenize(kind, secret)
    m.fingerprint = fp


def _set_default(db: Session, account: models.Account, chosen: models.PatientPaymentMethod) -> None:
    for row in list_methods(db, account):
        row.is_default = row.id == chosen.id


# ---------------------------------------------------------------------------- validation (small-amount authorization)

# What the built-in fake vault answers (development / tests): declines the usual test patterns —
# a card ending in 0002 (Stripe's "card declined" number 4000 0000 0000 0002), a bank account
# ending in 0000, and any entry whose label or holder name contains the word "decline". Everything
# else is authorized (and released).
FAKE_DECLINE_CARD_LAST4 = "0002"
FAKE_INSUFFICIENT_CARD_LAST4 = "9995"  # 4000 0000 0000 9995: insufficient funds
FAKE_DECLINE_BANK_LAST4 = "0000"


def authorize(m: models.PatientPaymentMethod, amount_cents: int, currency: str) -> tuple[str, str | None, str]:
    """Authorize ``amount_cents`` against the method's vault token and release the hold at once.
    Returns (result, reference, message) with result in PAYMENT_VALIDATION_RESULTS.

    The vault seam for validation (the counterpart of ``tokenize``): Stripe PaymentIntent with
    ``capture_method=manual`` + cancel, PayPal authorization + void, a micro-deposit or instant
    account verification for bank accounts. Without a real vault: the built-in fake outside
    prod, "error" (cannot be validated) in prod."""
    if m.provider != "fake":
        return "error", None, "No payment vault is configured on this server — the method cannot be validated yet."
    words = f"{m.label or ''} {m.holder_name or ''}".lower()
    if "decline" in words:
        return "declined", f"auth_fake_{secrets.token_hex(6)}", "The authorization was declined."
    if m.kind == "card" and m.card_last4 == FAKE_DECLINE_CARD_LAST4:
        return "declined", f"auth_fake_{secrets.token_hex(6)}", "The card was declined by the issuer."
    if m.kind == "card" and m.card_last4 == FAKE_INSUFFICIENT_CARD_LAST4:
        return "declined", f"auth_fake_{secrets.token_hex(6)}", "Insufficient funds."
    if m.kind == "card" and card_expired(m.card_exp_month, m.card_exp_year):
        return "declined", None, "The card has expired."
    if m.kind == "bank" and m.bank_account_last4 == FAKE_DECLINE_BANK_LAST4:
        return "declined", f"auth_fake_{secrets.token_hex(6)}", "The bank could not find this account."
    return (
        "authorized",
        f"auth_fake_{secrets.token_hex(6)}",
        f"{_money(amount_cents, currency)} authorized and released — nothing was charged.",
    )


def _money(cents: int, currency: str) -> str:
    return f"{cents / 100:.2f} {currency}"


def latest_validation(db: Session, m: models.PatientPaymentMethod) -> models.PaymentValidation | None:
    return db.execute(
        select(models.PaymentValidation)
        .where(models.PaymentValidation.method_id == m.id)
        .order_by(models.PaymentValidation.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def list_validations(db: Session, m: models.PatientPaymentMethod) -> list[models.PaymentValidation]:
    return (
        db.execute(
            select(models.PaymentValidation)
            .where(models.PaymentValidation.method_id == m.id)
            .order_by(models.PaymentValidation.id.desc())
        )
        .scalars()
        .all()
    )


def account_validations(db: Session, account: models.Account) -> list[models.PaymentValidation]:
    """Every validation on the account — of the methods on file *and* of those removed since
    (the payment history is independent of a method's lifetime)."""
    return (
        db.execute(
            select(models.PaymentValidation)
            .where(models.PaymentValidation.account_id == account.id)
            .order_by(models.PaymentValidation.id.desc())
        )
        .scalars()
        .all()
    )


def validate(
    db: Session,
    m: models.PatientPaymentMethod,
    triggered_by: str,
    actor: models.Account | None = None,
) -> models.PaymentValidation:
    """Run the small-amount authorization, record the attempt and set the method's status.
    A method that turns active becomes the account's default when there is none. Caller commits."""
    amount, currency = config.PAYMENT_VALIDATION_AMOUNT_CENTS, config.BILLING_CURRENCY
    result, reference, message = authorize(m, amount, currency)
    row = models.PaymentValidation(
        method_id=m.id,
        account_id=m.account_id,
        method_kind=m.kind,
        method_display=display(m),  # a snapshot: the row outlives the method
        amount_cents=amount,
        currency=currency,
        result=result,
        message=message,
        reference=reference,
        provider=m.provider or "none",
        triggered_by=triggered_by,
        actor_account_id=actor.id if actor else None,
    )
    db.add(row)
    if result == "authorized":
        m.status = "active"
        m.validated_at = models.utcnow()
    else:
        m.status = "failed" if result == "declined" else "pending"
        m.is_default = False
    db.flush()
    if m.status == "active":
        account = db.get(models.Account, m.account_id)
        if account is not None and not any(r.is_default for r in list_methods(db, account)):
            _set_default(db, account, m)
    log.info("payment method %s #%d validated by %s: %s (%s)", m.kind, m.id, triggered_by, result, message)
    return row


def validation_out(v: models.PaymentValidation) -> schemas.PaymentValidationOut:
    return schemas.PaymentValidationOut(
        id=v.id,
        result=v.result,
        amount_cents=v.amount_cents,
        currency=v.currency,
        message=v.message,
        reference=v.reference,
        provider=v.provider,
        triggered_by=v.triggered_by,
        created_at=v.created_at,
    )


# ---------------------------------------------------------------------------- operations (both routers)


def add(
    db: Session,
    account: models.Account,
    body: schemas.PatientPaymentMethodIn,
    *,
    triggered_by: str,
    actor: models.Account | None = None,
) -> models.PatientPaymentMethod:
    existing = list_methods(db, account)
    if len(existing) >= MAX_METHODS_PER_ACCOUNT:
        raise HTTPException(
            status_code=409,
            detail=f"You can keep up to {MAX_METHODS_PER_ACCOUNT} payment methods. Remove one first.",
        )
    m = models.PatientPaymentMethod(account_id=account.id, kind=body.kind)
    _apply(db, m, body, creating=True)
    db.add(m)
    db.flush()
    if config.PAYMENT_VALIDATE_ON_SAVE:
        validate(db, m, triggered_by, actor)
    if body.is_default and m.status == "active":
        _set_default(db, account, m)
    log.info("payment method added: account %s, %s #%d (%s, %s)", account.email, m.kind, m.id, m.provider, m.status)
    return m


def update(
    db: Session,
    account: models.Account,
    m: models.PatientPaymentMethod,
    body: schemas.PatientPaymentMethodIn,
    *,
    triggered_by: str,
    actor: models.Account | None = None,
) -> models.PatientPaymentMethod:
    if body.kind != m.kind:
        raise HTTPException(
            status_code=409,
            detail=f"This is a {KIND_LABELS[m.kind].lower()} entry; to switch to {KIND_LABELS[body.kind].lower()}, "
            "remove it and add a new payment method.",
        )
    before = (m.provider_token, m.card_exp_month, m.card_exp_year)
    _apply(db, m, body, creating=False)
    revalidated = (m.provider_token, m.card_exp_month, m.card_exp_year) != before
    if revalidated:
        # A new detail (or expiry) is a new thing to validate: it is not usable until that passed.
        m.status = "pending"
        m.validated_at = None
        m.is_default = False
        db.flush()
        if config.PAYMENT_VALIDATE_ON_SAVE:
            validate(db, m, triggered_by, actor)
    if body.is_default:
        if m.status == "active":
            _set_default(db, account, m)
        elif not revalidated:  # asked to make a method the default that is known not to work
            raise HTTPException(status_code=409, detail=_not_active_detail(m))
    log.info("payment method updated: account %s, %s #%d (%s)", account.email, m.kind, m.id, m.status)
    return m


def make_default(db: Session, account: models.Account, m: models.PatientPaymentMethod) -> None:
    if m.status != "active":
        raise HTTPException(status_code=409, detail=_not_active_detail(m))
    _set_default(db, account, m)


def remove(db: Session, account: models.Account, m: models.PatientPaymentMethod) -> None:
    was_default = m.is_default
    db.delete(m)
    db.flush()
    if was_default:
        remaining = [r for r in list_methods(db, account) if r.status == "active"]
        if remaining:
            _set_default(db, account, remaining[0])


def _not_active_detail(m: models.PatientPaymentMethod) -> str:
    return (
        f"{display(m)} has not been validated"
        + (" (the last authorization was declined)" if m.status == "failed" else "")
        + " — validate it first; only an active payment method can be the default."
    )


# ---------------------------------------------------------------------------- routes: the patient


@me_router.get("", response_model=list[schemas.PatientPaymentMethodOut])
def my_payment_methods(p: auth.Principal = Depends(auth.patient_principal), db: Session = Depends(get_db)):
    """The payment methods on this patient account — the default first, then oldest first."""
    return [out(db, m) for m in list_methods(db, p.account)]


@me_router.post("", response_model=schemas.PatientPaymentMethodOut, status_code=201)
def add_payment_method(
    body: schemas.PatientPaymentMethodIn,
    p: auth.Principal = Depends(auth.patient_principal),
    db: Session = Depends(get_db),
):
    """Add a card, Stripe / PayPal wallet or bank account. It is validated at once (a small
    authorization, released immediately); the first *active* method on the account becomes the
    default, ``is_default`` makes any later one the default instead. ``409`` when the same
    detail is already on file."""
    m = add(db, p.account, body, triggered_by="patient")
    db.commit()
    return out(db, m)


@me_router.get("/{method_id}", response_model=schemas.PatientPaymentMethodOut)
def my_payment_method(
    method_id: int, p: auth.Principal = Depends(auth.patient_principal), db: Session = Depends(get_db)
):
    return out(db, _own(db, p.account, method_id))


@me_router.put("/{method_id}", response_model=schemas.PatientPaymentMethodOut)
def update_payment_method(
    method_id: int,
    body: schemas.PatientPaymentMethodIn,
    p: auth.Principal = Depends(auth.patient_principal),
    db: Session = Depends(get_db),
):
    """Change the name, label, expiry, bank name … The card number, wallet e-mail address or
    routing + account number may be left out to keep what is on file; a new one replaces it
    (tokenized and validated again). The kind cannot change — remove the method and add
    another instead."""
    m = update(db, p.account, _own(db, p.account, method_id), body, triggered_by="patient")
    db.commit()
    return out(db, m)


@me_router.post("/{method_id}/validate", response_model=schemas.PatientPaymentMethodOut)
def validate_my_payment_method(
    method_id: int, p: auth.Principal = Depends(auth.patient_principal), db: Session = Depends(get_db)
):
    """Run the small-amount authorization (again) — e.g. after a decline was sorted out with the bank."""
    m = _own(db, p.account, method_id)
    validate(db, m, "patient")
    db.commit()
    return out(db, m)


@me_router.get("/{method_id}/validations", response_model=list[schemas.PaymentValidationOut])
def my_payment_method_validations(
    method_id: int, p: auth.Principal = Depends(auth.patient_principal), db: Session = Depends(get_db)
):
    """Every validation attempt of this method, newest first."""
    return [validation_out(v) for v in list_validations(db, _own(db, p.account, method_id))]


@me_router.post("/{method_id}/default", response_model=list[schemas.PatientPaymentMethodOut])
def make_default_payment_method(
    method_id: int, p: auth.Principal = Depends(auth.patient_principal), db: Session = Depends(get_db)
):
    """Make this the default method (the one the office charges first). Active methods only
    (``409`` otherwise). Answers the whole list."""
    make_default(db, p.account, _own(db, p.account, method_id))
    db.commit()
    return [out(db, row) for row in list_methods(db, p.account)]


@me_router.delete("/{method_id}", status_code=204)
def delete_payment_method(
    method_id: int, p: auth.Principal = Depends(auth.patient_principal), db: Session = Depends(get_db)
):
    """Remove the method. When it was the default, the oldest remaining active one takes over.
    Its payments and validations stay in the history (detached, with the method's masked
    description as it was)."""
    m = _own(db, p.account, method_id)
    kind = m.kind
    remove(db, p.account, m)
    db.commit()
    log.info("payment method removed: account %s, %s #%d", p.account.email, kind, method_id)
    return Response(status_code=204)


# ---------------------------------------------------------------------------- routes: the office


def _patient_account(db: Session, patient_id: int) -> tuple[models.Patient, models.Account | None]:
    """The record and the patient account it belongs to. A record that nobody claimed yet but
    that carries the e-mail address of a patient account is attached on the way — the same
    rule the patients' app applies (appointments.my_records). Caller commits."""
    patient = db.get(models.Patient, patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found.")
    account = db.get(models.Account, patient.account_id) if patient.account_id else None
    if account is not None and account.deleted_at:
        account = None
    if account is None and patient.account_id is None and patient.email_norm:
        account = db.execute(
            select(models.Account).where(
                models.Account.email == patient.email_norm,
                models.Account.role == models.ROLE_PATIENT,
                models.Account.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
        if account is not None:
            patient.account_id = account.id
            db.flush()
    return patient, account


def _require_account(db: Session, patient_id: int) -> tuple[models.Patient, models.Account]:
    patient, account = _patient_account(db, patient_id)
    if account is None:
        raise HTTPException(
            status_code=409,
            detail="This patient has no patient account yet. Payment methods are kept on the patient's "
            "account — ask the patient to create one in the app (or to sign in once so the record is linked).",
        )
    return patient, account


def _audit(db: Session, staff: models.Account, action: str, patient: models.Patient, target: models.Account, **d):
    accounts.audit(db, staff, action, target, patient_id=patient.id, mrn=patient.mrn, **d)


@admin_router.get("", response_model=schemas.PatientPaymentMethodsAdmin)
def patient_payment_methods(
    patient_id: int, _p: auth.Principal = Depends(auth.admin_principal), db: Session = Depends(get_db)
):
    """The payment methods on the account this record belongs to (empty, ``has_account`` false,
    when the patient has no account yet)."""
    patient, account = _patient_account(db, patient_id)
    result = schemas.PatientPaymentMethodsAdmin(
        patient_id=patient.id,
        has_account=account is not None,
        methods=[out(db, m) for m in list_methods(db, account)] if account else [],
    )
    db.commit()  # the record may just have been attached to the account
    return result


@admin_router.post("", response_model=schemas.PatientPaymentMethodOut, status_code=201)
def admin_add_payment_method(
    patient_id: int,
    body: schemas.PatientPaymentMethodIn,
    p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    """Add a method on the patient's behalf (the patient read out the card at the desk). Same
    rules as for the patient: tokenized, validated at once, never stored in full."""
    patient, account = _require_account(db, patient_id)
    m = add(db, account, body, triggered_by="staff", actor=p.account)
    _audit(db, p.account, "patient.payment_method.add", patient, account, method_id=m.id, kind=m.kind)
    db.commit()
    return out(db, m)


@admin_router.put("/{method_id}", response_model=schemas.PatientPaymentMethodOut)
def admin_update_payment_method(
    patient_id: int,
    method_id: int,
    body: schemas.PatientPaymentMethodIn,
    p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    patient, account = _require_account(db, patient_id)
    m = update(db, account, _own(db, account, method_id), body, triggered_by="staff", actor=p.account)
    _audit(db, p.account, "patient.payment_method.update", patient, account, method_id=m.id, kind=m.kind)
    db.commit()
    return out(db, m)


@admin_router.post("/{method_id}/validate", response_model=schemas.PatientPaymentMethodOut)
def admin_validate_payment_method(
    patient_id: int,
    method_id: int,
    p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    """Validate the method (again) now: a small authorization against the vault token, released
    at once. The result decides whether the method is active."""
    patient, account = _require_account(db, patient_id)
    m = _own(db, account, method_id)
    v = validate(db, m, "staff", p.account)
    _audit(db, p.account, "patient.payment_method.validate", patient, account, method_id=m.id, result=v.result)
    db.commit()
    return out(db, m)


@admin_router.get("/{method_id}/validations", response_model=list[schemas.PaymentValidationOut])
def admin_payment_method_validations(
    patient_id: int,
    method_id: int,
    _p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    _patient, account = _require_account(db, patient_id)
    return [validation_out(v) for v in list_validations(db, _own(db, account, method_id))]


@admin_router.post("/{method_id}/default", response_model=schemas.PatientPaymentMethodsAdmin)
def admin_make_default_payment_method(
    patient_id: int,
    method_id: int,
    p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    patient, account = _require_account(db, patient_id)
    make_default(db, account, _own(db, account, method_id))
    _audit(db, p.account, "patient.payment_method.default", patient, account, method_id=method_id)
    db.commit()
    return schemas.PatientPaymentMethodsAdmin(
        patient_id=patient.id, has_account=True, methods=[out(db, m) for m in list_methods(db, account)]
    )


@admin_router.delete("/{method_id}", status_code=204)
def admin_delete_payment_method(
    patient_id: int,
    method_id: int,
    p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    patient, account = _require_account(db, patient_id)
    m = _own(db, account, method_id)
    kind = m.kind
    remove(db, account, m)
    _audit(db, p.account, "patient.payment_method.remove", patient, account, method_id=method_id, kind=kind)
    db.commit()
    return Response(status_code=204)
