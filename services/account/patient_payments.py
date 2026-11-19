"""Patient payments — the ledger behind the "Payment history" tabs, and the booking rule that a
patient needs a validated payment method to schedule a visit.

    A signed-in patient account (auth.patient_principal):
    GET  /api/v1/me/payments                          every payment and validation on the account's
                                                      methods, newest first, with totals

    Administrators (auth.admin_principal), by patient record:
    GET  /api/v1/patients/{patient_id}/payments       the same for one record (validations of the
                                                      account's methods included)
    POST /api/v1/patients/{patient_id}/payments       charge the patient now (statement, fee) against
                                                      one of the active methods
    POST /api/v1/patients/{patient_id}/payments/{id}/capture   charge a hold
    POST /api/v1/patients/{patient_id}/payments/{id}/release   let a hold go
    POST /api/v1/patients/{patient_id}/payments/{id}/refund    give (part of) a charge back

    Administrators, across every patient (the office administration dashboard's Payments tab):
    GET  /api/v1/patient-payments                     the office-wide ledger: every hold, charge,
                                                      release and refund, newest first, paged —
                                                      search (patient, MRN, description, reference),
                                                      kind, date range, status group; totals + counts
    GET  /api/v1/patient-payments/export.csv          the same search as CSV (for accounting)

How money moves (``models.PatientPayment``, one row per movement, amounts in cents):

    booking      a patient can book only signed in and with an *active* payment method on the
                 account (config.APPOINTMENT_REQUIRE_PAYMENT_METHOD). The copay the coverage
                 check named — or config.SELF_PAY_VISIT_CENTS for a self-pay visit — is put on
                 hold on the default method: status ``authorized`` (``hold_for_appointment``).
                 A hold the vault declines means the visit is not booked.
    completed    the office marks the visit completed → the hold is ``captured`` (charged).
    canceled /   the hold is ``released`` — by the patient, from the link, or by the office.
    no-show
    statement    the office charges an amount against an active method: ``captured`` at once.
    refund       (part of) a captured payment goes back: ``partially_refunded`` / ``refunded``.

Validations (patient_payment_methods.PaymentValidation — the small authorization every method
passes before it is usable) are listed in the history too, marked as such, because the patient
may see them on a bank statement as a pending hold.

**The history is independent of a method's lifetime.** Every payment and validation row keeps
its own snapshot of the method (account, kind, masked description such as "Visa •••• 4242 ·
12/2031") and only *references* the method (ON DELETE SET NULL): removing a card, a Stripe /
PayPal wallet or a bank account detaches its rows, never deletes them — the history then shows
the entry as it was, marked "removed". Nothing here touches a payment detail: the
vault is addressed by the method's token (``vault_charge`` & co., the seam next to
``patient_payment_methods.tokenize`` / ``authorize``; the built-in fake outside prod).
"""

from __future__ import annotations

import csv
import io
import logging
import re
import secrets
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

import accounts
import auth
import config
import models
import patient_payment_methods as ppm
import patients
import schemas
from database import get_db

log = logging.getLogger("account.patient_payments")

me_router = APIRouter(prefix="/me/payments", tags=["me"])
admin_router = APIRouter(prefix="/patients/{patient_id}/payments", tags=["patients"])
office_router = APIRouter(prefix="/patient-payments", tags=["patients"])

KIND_DESCRIPTIONS = {
    "copay": "Copay",
    "self_pay": "Self-pay visit",
    "statement": "Statement",
    "other": "Charge",
    "check_in": "Check-in pre-authorization",
}
# The built-in fake vault declines holds (not the $1 validation) on a method whose label or holder
# name contains this word — to rehearse a failed check-in pre-authorization without a real card.
FAKE_HOLD_DECLINE_WORD = "overlimit"


# ---------------------------------------------------------------------------- the vault seam


def _vault_ok(m: models.PatientPaymentMethod) -> tuple[bool, str]:
    if m.provider != "fake":
        return False, "No payment vault is configured on this server — nothing can be charged yet."
    if m.status != "active":
        return False, f"{ppm.display(m)} has not been validated."
    return True, ""


def vault_authorize(m: models.PatientPaymentMethod, cents: int, currency: str) -> tuple[bool, str | None, str]:
    """Put ``cents`` on hold on the method's token. (ok, reference, message)."""
    ok, why = _vault_ok(m)
    if not ok:
        return False, None, why
    if FAKE_HOLD_DECLINE_WORD in f"{m.label or ''} {m.holder_name or ''}".lower():
        return False, f"auth_fake_{secrets.token_hex(6)}", "Insufficient funds (the built-in fake vault declines holds "
        f"on methods labelled '{FAKE_HOLD_DECLINE_WORD}')."
    return True, f"auth_fake_{secrets.token_hex(6)}", f"{_money(cents, currency)} on hold on {ppm.display(m)}."


def vault_capture(p: models.PatientPayment) -> tuple[bool, str | None, str]:
    """Charge a hold (or charge at once when there is no hold). (ok, reference, message)."""
    if p.provider != "fake":
        return False, None, "No payment vault is configured on this server — nothing can be charged yet."
    return (
        True,
        f"ch_fake_{secrets.token_hex(6)}",
        f"{_money(p.amount_cents, p.currency)} charged to {p.method_display}.",
    )


def vault_release(p: models.PatientPayment) -> tuple[bool, str]:
    if p.provider != "fake":
        return False, "No payment vault is configured on this server."
    return True, f"Hold of {_money(p.amount_cents, p.currency)} on {p.method_display} released."


def vault_refund(p: models.PatientPayment, cents: int) -> tuple[bool, str | None, str]:
    if p.provider != "fake":
        return False, None, "No payment vault is configured on this server."
    return True, f"re_fake_{secrets.token_hex(6)}", f"{_money(cents, p.currency)} refunded to {p.method_display}."


def _money(cents: int, currency: str) -> str:
    return f"{cents / 100:.2f} {currency}"


# ---------------------------------------------------------------------------- methods to charge


def active_methods(db: Session, account: models.Account) -> list[models.PatientPaymentMethod]:
    return [m for m in ppm.list_methods(db, account) if m.status == "active"]


def default_method(db: Session, account: models.Account) -> models.PatientPaymentMethod | None:
    """The default (active) method, else the oldest active one, else None."""
    rows = active_methods(db, account)
    return next((m for m in rows if m.is_default), rows[0] if rows else None)


NO_METHOD_DETAIL = (
    "No valid payment method on file. Please add a card, Stripe / PayPal account or bank account "
    "on the Payment methods page and make sure it is validated before booking."
)


def require_booking_method(db: Session, me: auth.Principal | None) -> models.PatientPaymentMethod | None:
    """The rule at booking: signed in as a patient, with an active method. Returns the method
    the hold goes on (None when the rule is switched off)."""
    if not config.APPOINTMENT_REQUIRE_PAYMENT_METHOD:
        return None
    if me is None or me.account.role != models.ROLE_PATIENT:
        raise HTTPException(
            status_code=401,
            detail="Please sign in to your patient account to book — a validated payment method on the "
            "account is required for appointments.",
        )
    method = default_method(db, me.account)
    if method is None:
        raise HTTPException(status_code=409, detail=NO_METHOD_DETAIL)
    return method


# ---------------------------------------------------------------------------- movements


def _new_payment(
    account: models.Account,
    patient: models.Patient | None,
    m: models.PatientPaymentMethod,
    *,
    kind: str,
    cents: int,
    description: str,
    created_by: str,
    actor: models.Account | None,
    appointment: models.Appointment | None = None,
) -> models.PatientPayment:
    return models.PatientPayment(
        account_id=account.id,
        patient_id=patient.id if patient else None,
        method_id=m.id,
        method_kind=m.kind,
        method_display=ppm.display(m),
        appointment_id=appointment.id if appointment else None,
        kind=kind,
        amount_cents=cents,
        currency=config.BILLING_CURRENCY,
        status="declined",
        description=description,
        provider=m.provider or "none",
        created_by=created_by,
        actor_account_id=actor.id if actor else None,
    )


def amount_for_visit(cover: schemas.CoverageCheck) -> tuple[int, str]:
    """What a visit costs the patient up front: the copay the coverage check named, or the
    self-pay fee. (cents, kind) — 0 when nothing is due (e.g. covered, no copay known)."""
    if cover.covered:
        copay = cover.verification.copay_cents if cover.verification else None
        return (copay or 0), "copay"
    return config.SELF_PAY_VISIT_CENTS, "self_pay"


def hold_for_appointment(
    db: Session,
    appointment: models.Appointment,
    patient: models.Patient,
    account: models.Account,
    m: models.PatientPaymentMethod,
    cover: schemas.CoverageCheck,
    *,
    created_by: str = "patient",
    actor: models.Account | None = None,
) -> models.PatientPayment | None:
    """Put the visit's copay / self-pay fee on hold when it is booked. ``409`` when the vault
    declines (the caller does not commit: the visit is not booked). None when nothing is due."""
    cents, kind = amount_for_visit(cover)
    if cents <= 0:
        return None
    provider = db.get(models.Worker, appointment.provider_id)
    who = f"{provider.first_name} {provider.last_name}" if provider else "the office"
    p = _new_payment(
        account,
        patient,
        m,
        kind=kind,
        cents=cents,
        description=f"{KIND_DESCRIPTIONS[kind]} · visit with {who}",
        created_by=created_by,
        actor=actor,
        appointment=appointment,
    )
    ok, reference, message = vault_authorize(m, cents, p.currency)
    p.reference = reference
    p.message = message
    p.status = "authorized" if ok else "declined"
    db.add(p)
    db.flush()
    if not ok:
        raise HTTPException(
            status_code=409,
            detail=f"Your payment method ({ppm.display(m)}) could not be authorized for the "
            f"{_money(cents, p.currency)} {KIND_DESCRIPTIONS[kind].lower()}: {message} "
            "Please validate it or add another one on the Payment methods page.",
        )
    log.info("hold placed: %s %s for %s (%s)", _money(cents, p.currency), kind, appointment.number, m.kind)
    return p


def preauthorize_check_in(
    db: Session,
    appointment: models.Appointment,
    patient: models.Patient,
    account: models.Account,
    m: models.PatientPaymentMethod,
    cents: int,
) -> models.PatientPayment:
    """Check-in: put the estimated cost not covered by insurance on hold on the method. The row is
    kept either way (``authorized`` or ``declined`` with the vault's message); the caller decides.
    Captured with the other holds when the visit is completed, released when it is canceled."""
    provider = db.get(models.Worker, appointment.provider_id)
    who = f"{provider.first_name} {provider.last_name}" if provider else "the office"
    p = _new_payment(
        account,
        patient,
        m,
        kind="check_in",
        cents=cents,
        description=f"{KIND_DESCRIPTIONS['check_in']} · visit with {who}",
        created_by="patient",
        actor=None,
        appointment=appointment,
    )
    ok, reference, message = vault_authorize(m, cents, p.currency)
    p.reference = reference
    p.message = message
    p.status = "authorized" if ok else "declined"
    db.add(p)
    db.flush()
    log.info("check-in pre-authorization %s for %s: %s", _money(cents, p.currency), appointment.number, p.status)
    return p


def release_replaced_holds(db: Session, appointment: models.Appointment, keep: models.PatientPayment | None) -> None:
    """The check-in pre-authorization replaces the booking's copay / self-pay hold: release the
    older holds of the visit (never the one just placed). Caller commits."""
    for p in holds_for(db, appointment):
        if keep is not None and p.id == keep.id:
            continue
        ok, message = vault_release(p)
        if ok:
            p.status = "released"
            p.released_at = models.utcnow()
            p.message = message + " Replaced by the check-in pre-authorization."
    db.flush()


def holds_for(db: Session, appointment: models.Appointment) -> list[models.PatientPayment]:
    return (
        db.execute(
            select(models.PatientPayment)
            .where(
                models.PatientPayment.appointment_id == appointment.id,
                models.PatientPayment.status == "authorized",
            )
            .order_by(models.PatientPayment.id)
        )
        .scalars()
        .all()
    )


def charge_for(db: Session, appointment: models.Appointment) -> models.PatientPayment | None:
    """The newest payment row of a visit (for AppointmentOut.charge)."""
    return db.execute(
        select(models.PatientPayment)
        .where(models.PatientPayment.appointment_id == appointment.id)
        .order_by(models.PatientPayment.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def on_appointment_status(db: Session, appointment: models.Appointment, actor: models.Account | None = None) -> None:
    """The visit's status changed: completed → charge the hold, canceled / no-show → release it.
    Caller commits. A vault failure is logged, never raised — the status change itself stands."""
    for p in holds_for(db, appointment):
        if appointment.status == "completed":
            capture(db, p, actor, raise_on_failure=False)
        elif appointment.status in ("canceled", "no_show"):
            release(db, p, actor, raise_on_failure=False)


def capture(
    db: Session, p: models.PatientPayment, actor: models.Account | None = None, *, raise_on_failure: bool = True
) -> models.PatientPayment:
    if p.status != "authorized":
        raise HTTPException(status_code=409, detail=f"This payment is {p.status.replace('_', ' ')}, not on hold.")
    ok, reference, message = vault_capture(p)
    p.message = message
    if ok:
        p.status = "captured"
        p.reference = reference or p.reference
        p.captured_at = models.utcnow()
        log.info("payment #%d captured: %s", p.id, message)
    else:
        log.warning("payment #%d could not be captured: %s", p.id, message)
        if raise_on_failure:
            raise HTTPException(status_code=502, detail=message)
    db.flush()
    return p


def release(
    db: Session, p: models.PatientPayment, actor: models.Account | None = None, *, raise_on_failure: bool = True
) -> models.PatientPayment:
    if p.status != "authorized":
        raise HTTPException(status_code=409, detail=f"This payment is {p.status.replace('_', ' ')}, not on hold.")
    ok, message = vault_release(p)
    p.message = message
    if ok:
        p.status = "released"
        p.released_at = models.utcnow()
        log.info("payment #%d released", p.id)
    else:
        log.warning("payment #%d could not be released: %s", p.id, message)
        if raise_on_failure:
            raise HTTPException(status_code=502, detail=message)
    db.flush()
    return p


def charge(
    db: Session,
    account: models.Account,
    patient: models.Patient,
    m: models.PatientPaymentMethod,
    body: schemas.PatientChargeIn,
    actor: models.Account,
) -> models.PatientPayment:
    """The office charges the patient now (a statement, a fee)."""
    p = _new_payment(
        account,
        patient,
        m,
        kind=body.kind,
        cents=body.amount_cents,
        description=body.description,
        created_by="staff",
        actor=actor,
    )
    ok, reference, message = vault_capture(p)
    p.reference = reference
    p.message = message
    p.status = "captured" if ok else "declined"
    if ok:
        p.captured_at = models.utcnow()
    db.add(p)
    db.flush()
    if not ok:
        raise HTTPException(status_code=409, detail=f"{ppm.display(m)} could not be charged: {message}")
    log.info("payment #%d charged by %s: %s", p.id, actor.email, message)
    return p


def refund(db: Session, p: models.PatientPayment, body: schemas.PatientRefundIn, actor: models.Account) -> None:
    if p.status not in ("captured", "partially_refunded"):
        raise HTTPException(
            status_code=409, detail=f"This payment is {p.status.replace('_', ' ')} — nothing to refund."
        )
    left = p.amount_cents - p.refunded_cents
    cents = body.amount_cents if body.amount_cents is not None else left
    if cents > left:
        raise HTTPException(status_code=409, detail=f"Only {_money(left, p.currency)} is left to refund.")
    ok, reference, message = vault_refund(p, cents)
    if not ok:
        raise HTTPException(status_code=502, detail=message)
    p.refunded_cents += cents
    p.refund_reference = reference
    p.refunded_at = models.utcnow()
    p.status = "refunded" if p.refunded_cents >= p.amount_cents else "partially_refunded"
    p.message = message + (f" Reason: {body.reason}" if body.reason else "")
    db.flush()
    log.info("payment #%d refunded %s by %s", p.id, _money(cents, p.currency), actor.email)


# ---------------------------------------------------------------------------- output


def _appointment_ref(db: Session, appointment_id: int | None) -> schemas.PaymentAppointmentRef | None:
    if appointment_id is None:
        return None
    a = db.get(models.Appointment, appointment_id)
    if a is None:
        return None
    import appointments

    provider = db.get(models.Worker, a.provider_id)
    return schemas.PaymentAppointmentRef(
        id=a.id,
        number=a.number,
        start_local=appointments.format_local(a.start_at),
        provider=f"{provider.first_name} {provider.last_name}" if provider else "",
        status=a.status,
    )


def payment_out(db: Session, p: models.PatientPayment) -> schemas.PatientPaymentOut:
    patient = db.get(models.Patient, p.patient_id) if p.patient_id else None
    return schemas.PatientPaymentOut(
        id=p.id,
        kind=p.kind,
        status=p.status,
        amount_cents=p.amount_cents,
        refunded_cents=p.refunded_cents,
        currency=p.currency,
        description=p.description,
        message=p.message,
        method=schemas.PaymentMethodRef(
            id=p.method_id, kind=p.method_kind, display=p.method_display, removed=p.method_id is None
        ),
        appointment=_appointment_ref(db, p.appointment_id),
        patient=patients.summary(patient) if patient else None,
        reference=p.reference,
        refund_reference=p.refund_reference,
        provider=p.provider,
        created_by=p.created_by,
        created_at=p.created_at,
        captured_at=p.captured_at,
        released_at=p.released_at,
        refunded_at=p.refunded_at,
    )


def payment_entry(db: Session, p: models.PatientPayment) -> schemas.PaymentHistoryEntry:
    """One payment as a line of a payment history (the patient's, one record's, the office's)."""
    out = payment_out(db, p)
    return schemas.PaymentHistoryEntry(
        id=f"payment-{p.id}",
        at=p.created_at,
        entry="payment",
        kind=p.kind,
        status=p.status,
        amount_cents=p.amount_cents,
        refunded_cents=p.refunded_cents,
        currency=p.currency,
        description=p.description,
        message=p.message,
        method=out.method,
        appointment=out.appointment,
        patient=out.patient,
        reference=p.reference,
        provider=p.provider,
        by=p.created_by,
        payment=out,
    )


def history(db: Session, account: models.Account, patient: models.Patient | None = None) -> schemas.PaymentHistory:
    """Every payment on the account (or one record of it) plus the validations of the
    account's methods, newest first, with totals over the payments."""
    q = select(models.PatientPayment).where(models.PatientPayment.account_id == account.id)
    if patient is not None:
        q = q.where(models.PatientPayment.patient_id == patient.id)
    rows = db.execute(q).scalars().all()
    entries: list[schemas.PaymentHistoryEntry] = []
    charged = refunded = held = 0
    for p in rows:
        entries.append(payment_entry(db, p))
        if p.status in ("captured", "partially_refunded", "refunded"):
            charged += p.amount_cents - p.refunded_cents
            refunded += p.refunded_cents
        elif p.status == "authorized":
            held += p.amount_cents
    for v in ppm.account_validations(db, account):  # of the methods on file and of those removed since
        entries.append(
            schemas.PaymentHistoryEntry(
                id=f"validation-{v.id}",
                at=v.created_at,
                entry="validation",
                kind="validation",
                status=v.result,
                amount_cents=v.amount_cents,
                currency=v.currency,
                description=f"Validation of {v.method_display}",
                message=v.message,
                method=schemas.PaymentMethodRef(
                    id=v.method_id, kind=v.method_kind, display=v.method_display, removed=v.method_id is None
                ),
                reference=v.reference,
                provider=v.provider,
                by=v.triggered_by,
            )
        )
    entries.sort(key=lambda e: (e.at, e.id), reverse=True)
    return schemas.PaymentHistory(
        entries=entries,
        totals=schemas.PaymentTotals(
            currency=config.BILLING_CURRENCY,
            charged_cents=charged,
            refunded_cents=refunded,
            held_cents=held,
            count=len(rows),
        ),
    )


# ---------------------------------------------------------------------------- routes


@me_router.get("", response_model=schemas.PaymentHistory)
def my_payments(p: auth.Principal = Depends(auth.patient_principal), db: Session = Depends(get_db)):
    """The account's payment history: every hold, charge and refund on every method, the
    validations too — newest first, with totals."""
    return history(db, p.account)


def _own_payment(db: Session, account: models.Account, payment_id: int) -> models.PatientPayment:
    row = db.get(models.PatientPayment, payment_id)
    if row is None or row.account_id != account.id:
        raise HTTPException(status_code=404, detail="Payment not found.")
    return row


def _audit(db: Session, staff: models.Account, action: str, patient: models.Patient, target: models.Account, **d):
    accounts.audit(db, staff, action, target, patient_id=patient.id, mrn=patient.mrn, **d)


@admin_router.get("", response_model=schemas.PaymentHistory)
def patient_payments(
    patient_id: int, _p: auth.Principal = Depends(auth.admin_principal), db: Session = Depends(get_db)
):
    """One record's payment history (and the validations of the account's methods)."""
    patient, account = ppm._patient_account(db, patient_id)
    if account is None:
        return schemas.PaymentHistory(
            entries=[],
            totals=schemas.PaymentTotals(
                currency=config.BILLING_CURRENCY, charged_cents=0, refunded_cents=0, held_cents=0, count=0
            ),
        )
    result = history(db, account, patient)
    db.commit()
    return result


@admin_router.post("", response_model=schemas.PatientPaymentOut, status_code=201)
def admin_charge(
    patient_id: int,
    body: schemas.PatientChargeIn,
    p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    """Charge the patient now against one of the active methods (a statement, a fee)."""
    patient, account = ppm._require_account(db, patient_id)
    m = ppm._own(db, account, body.method_id)
    if m.status != "active":
        raise HTTPException(status_code=409, detail=f"{ppm.display(m)} has not been validated — validate it first.")
    row = charge(db, account, patient, m, body, p.account)
    _audit(db, p.account, "patient.payment.charge", patient, account, payment_id=row.id, amount_cents=row.amount_cents)
    db.commit()
    return payment_out(db, row)


@admin_router.post("/{payment_id}/capture", response_model=schemas.PatientPaymentOut)
def admin_capture(
    patient_id: int, payment_id: int, p: auth.Principal = Depends(auth.admin_principal), db: Session = Depends(get_db)
):
    """Charge a hold now (normally done when the visit is marked completed)."""
    patient, account = ppm._require_account(db, patient_id)
    row = capture(db, _own_payment(db, account, payment_id), p.account)
    _audit(db, p.account, "patient.payment.capture", patient, account, payment_id=row.id)
    db.commit()
    return payment_out(db, row)


@admin_router.post("/{payment_id}/release", response_model=schemas.PatientPaymentOut)
def admin_release(
    patient_id: int, payment_id: int, p: auth.Principal = Depends(auth.admin_principal), db: Session = Depends(get_db)
):
    """Let a hold go without charging it."""
    patient, account = ppm._require_account(db, patient_id)
    row = release(db, _own_payment(db, account, payment_id), p.account)
    _audit(db, p.account, "patient.payment.release", patient, account, payment_id=row.id)
    db.commit()
    return payment_out(db, row)


@admin_router.post("/{payment_id}/refund", response_model=schemas.PatientPaymentOut)
def admin_refund(
    patient_id: int,
    payment_id: int,
    body: schemas.PatientRefundIn,
    p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    """Refund (part of) a captured payment to the method it came from."""
    patient, account = ppm._require_account(db, patient_id)
    row = _own_payment(db, account, payment_id)
    refund(db, row, body, p.account)
    _audit(
        db, p.account, "patient.payment.refund", patient, account, payment_id=row.id, refunded_cents=row.refunded_cents
    )
    db.commit()
    return payment_out(db, row)


# ---------------------------------------------------------------------------- routes: the office-wide ledger

# Which statuses each "show" group of the office ledger covers (the Payments tab's filter chips).
SHOW_STATUSES: dict[str, tuple[str, ...] | None] = {
    "all": None,
    "charges": ("captured", "partially_refunded", "refunded"),
    "holds": ("authorized",),
    "refunds": ("partially_refunded", "refunded"),
    "released": ("released",),
    "declined": ("declined",),
}
CHARGED_STATUSES = SHOW_STATUSES["charges"]
EXPORT_MAX_ROWS = 10_000
_DATE = r"^(|\d{4}-\d{2}-\d{2})$"
_MRN = re.compile(r"^[Pp]-?0*(\d{1,9})$")
_APPOINTMENT_NUMBER = re.compile(r"^(?i:apt)-?0*(\d{1,9})$")


def _office_day(value: str, name: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{name}: not a valid date (YYYY-MM-DD)") from None


def _office_start(day: date) -> datetime:
    """Midnight of an office-local day, as the UTC instant the database compares with."""
    import appointments  # appointments imports this module

    return datetime.combine(day, time.min, tzinfo=appointments.office_tz())


def office_conditions(q: str, kind: str, since: date | None, until: date | None) -> list:
    """The WHERE clause of the office ledger (everything but the status group).

    ``q``: a record number (``P-000001``) or appointment number (``APT-000003``) finds exactly
    that; anything else matches the patient's name (first, last or both, any order — compared
    like the roster compares names), the description, the method's masked description and the
    vault references."""
    conds: list = []
    term = q.strip()
    if term:
        if m := _MRN.match(term):
            conds.append(models.PatientPayment.patient_id == int(m.group(1)))
        elif m := _APPOINTMENT_NUMBER.match(term):
            conds.append(models.PatientPayment.appointment_id == int(m.group(1)))
        else:
            like = f"%{term.lower()}%"
            options = [
                func.lower(models.PatientPayment.description).like(like),
                func.lower(models.PatientPayment.method_display).like(like),
                func.lower(models.PatientPayment.reference).like(like),
                func.lower(models.PatientPayment.refund_reference).like(like),
            ]
            norm = patients.normalize_name(term)
            if norm:
                name = f"%{norm}%"
                options += [
                    models.Patient.first_name_norm.like(name),
                    models.Patient.last_name_norm.like(name),
                    (models.Patient.first_name_norm + models.Patient.last_name_norm).like(name),
                    (models.Patient.last_name_norm + models.Patient.first_name_norm).like(name),
                ]
            conds.append(or_(*options))
    if kind:
        conds.append(models.PatientPayment.kind == kind)
    if since and until and since > until:
        raise HTTPException(status_code=422, detail="since: must not be after until")
    if since:
        conds.append(models.PatientPayment.created_at >= _office_start(since))
    if until:
        conds.append(models.PatientPayment.created_at < _office_start(until + timedelta(days=1)))
    return conds


def _ledger(*columns):
    return (
        select(*columns)
        .select_from(models.PatientPayment)
        .outerjoin(models.Patient, models.Patient.id == models.PatientPayment.patient_id)
    )


def office_summary(db: Session, conds: list) -> tuple[schemas.PaymentTotals, schemas.OfficePaymentCounts]:
    """Totals (charged net of refunds · refunded · on hold · count) and the size of every status
    group — one GROUP BY over the search, whatever page / group is shown."""
    rows = db.execute(
        _ledger(
            models.PatientPayment.status,
            func.count(),
            func.coalesce(func.sum(models.PatientPayment.amount_cents), 0),
            func.coalesce(func.sum(models.PatientPayment.refunded_cents), 0),
        )
        .where(*conds)
        .group_by(models.PatientPayment.status)
    ).all()
    by_status = {status: (int(n), int(amount), int(refunded)) for status, n, amount, refunded in rows}
    charged = sum(a - r for s, (_n, a, r) in by_status.items() if s in CHARGED_STATUSES)
    refunded = sum(r for _n, _a, r in by_status.values())
    held = by_status.get("authorized", (0, 0, 0))[1]
    count = sum(n for n, _a, _r in by_status.values())
    counts = {
        key: count if statuses is None else sum(by_status.get(s, (0, 0, 0))[0] for s in statuses)
        for key, statuses in SHOW_STATUSES.items()
    }
    return (
        schemas.PaymentTotals(
            currency=config.BILLING_CURRENCY,
            charged_cents=charged,
            refunded_cents=refunded,
            held_cents=held,
            count=count,
        ),
        schemas.OfficePaymentCounts(**counts),
    )


def _shown(conds: list, show: str) -> list:
    statuses = SHOW_STATUSES[show]
    return conds if statuses is None else [*conds, models.PatientPayment.status.in_(statuses)]


def _ordered(stmt):
    return stmt.order_by(models.PatientPayment.created_at.desc(), models.PatientPayment.id.desc())


_OFFICE_Q = Query(
    default="",
    max_length=120,
    description="Patient name, record number (P-000001), appointment number, description or reference",
)
_OFFICE_SHOW = Query(default="all", description="Status group: all · charges · holds · refunds · released · declined")
_OFFICE_KIND = Query(default="", pattern="^(|copay|self_pay|statement|other|check_in)$")
_OFFICE_SINCE = Query(default="", pattern=_DATE, description="First day (office time zone), YYYY-MM-DD")
_OFFICE_UNTIL = Query(default="", pattern=_DATE, description="Last day (office time zone), YYYY-MM-DD")


@office_router.get("", response_model=schemas.OfficePaymentPage)
def office_payments(
    q: str = _OFFICE_Q,
    show: schemas.OfficePaymentShow = _OFFICE_SHOW,
    kind: str = _OFFICE_KIND,
    since: str = _OFFICE_SINCE,
    until: str = _OFFICE_UNTIL,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    _p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    """The office-wide payment history: every hold, charge, release and refund on every patient's
    payment methods — including methods removed since — newest first. Validations (the small
    authorizations of each method) are not money movements and stay on each record's history."""
    conds = office_conditions(q, kind, _office_day(since, "since"), _office_day(until, "until"))
    totals, counts = office_summary(db, conds)
    rows = (
        db.execute(_ordered(_ledger(models.PatientPayment).where(*_shown(conds, show))).offset(offset).limit(limit))
        .scalars()
        .all()
    )
    return schemas.OfficePaymentPage(
        items=[payment_entry(db, p) for p in rows],
        total=getattr(counts, show),
        offset=offset,
        limit=limit,
        totals=totals,
        counts=counts,
    )


def _stamp(value: datetime | None) -> str:
    return value.isoformat() if value else ""


def _cell(value: object) -> object:
    """Spreadsheet-safe: text that a spreadsheet would run as a formula gets a leading apostrophe."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


@office_router.get("/export.csv", response_class=Response)
def office_payments_csv(
    q: str = _OFFICE_Q,
    show: schemas.OfficePaymentShow = _OFFICE_SHOW,
    kind: str = _OFFICE_KIND,
    since: str = _OFFICE_SINCE,
    until: str = _OFFICE_UNTIL,
    p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    """The same search as CSV (for accounting), newest first, at most EXPORT_MAX_ROWS rows.
    Amounts in major units; the export is recorded in the audit log."""
    conds = office_conditions(q, kind, _office_day(since, "since"), _office_day(until, "until"))
    rows = db.execute(
        _ordered(_ledger(models.PatientPayment, models.Patient).where(*_shown(conds, show))).limit(EXPORT_MAX_ROWS)
    ).all()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(
        [
            "created_at", "id", "patient_mrn", "patient_name", "kind", "status", "amount", "refunded", "currency",
            "description", "method", "method_removed", "appointment", "created_by", "reference", "refund_reference",
            "provider", "captured_at", "released_at", "refunded_at", "message",
        ]
    )  # fmt: skip
    for pay, patient in rows:
        w.writerow(
            [
                _cell(v)
                for v in (
                    _stamp(pay.created_at), pay.id, patient.mrn if patient else "",
                    f"{patient.first_name} {patient.last_name}" if patient else "", pay.kind, pay.status,
                    f"{pay.amount_cents / 100:.2f}", f"{pay.refunded_cents / 100:.2f}", pay.currency,
                    pay.description, pay.method_display, "yes" if pay.method_id is None else "no",
                    f"APT-{pay.appointment_id:06d}" if pay.appointment_id else "", pay.created_by,
                    pay.reference or "", pay.refund_reference or "", pay.provider, _stamp(pay.captured_at),
                    _stamp(pay.released_at), _stamp(pay.refunded_at), pay.message,
                )
            ]
        )  # fmt: skip
    accounts.audit(
        db, p.account, "patient.payments.export", None, rows=len(rows), q=q, show=show, kind=kind, since=since,
        until=until,
    )  # fmt: skip
    db.commit()
    return Response(
        out.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="patient-payments-{date.today().isoformat()}.csv"',
            "Cache-Control": "no-store",
        },
    )
