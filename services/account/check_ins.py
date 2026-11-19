"""Check-in — the page the patient completes before a visit, and what happens when it is signed.

"Check in" (the patient, in the app: their account · the appointment link · the lookup page —
from CHECK_IN_WINDOW_HOURS before the visit until it ends) opens a **check-in page**
``<web app>/check-in/<token>``. The office can start a check-in at any time from the
administration dashboard: the page's link is then **sent to the patient** by e-mail and/or
text to what is on the patient record. Either way the page must be completed within
CHECK_IN_LINK_MINUTES (60) or the check-in fails.

The page (GET /api/v1/check-ins/{token}) has two parts the patient signs:
  1. a **questionnaire** about the visit (reason, symptoms, pain, fever, medications, allergies,
     …) — signed with a typed full name and a drawn signature;
  2. the **estimated cost not covered by insurance** (``estimate``: the coverage checked again
     for the day of the visit — the copay, or deductible + coinsurance, or the self-pay fee)
     — the patient accepts financial responsibility and signs again.

Submitting (POST /api/v1/check-ins/{token}) processes it, in this order:
  * the answers and both signatures are validated (``422`` — the patient corrects the form);
  * the estimate must still be the one the patient saw (``409`` — the page reloads);
  * both signatures are recorded through esign.py (the built-in fake outside production);
  * the visit must still be scheduled;
  * the estimate is **pre-authorized** on the default validated payment method of the patient's
    account (patient_payments.preauthorize_check_in, kind ``check_in``); it replaces the copay /
    self-pay hold placed at booking, which is released;
  * only then is the appointment **checked in** (``appointments.checked_in_at``).

Once completed, the appointment's **provider is notified** (notify_staff): an e-mail / text to
the staff record saying the patient checked in — without medical details; the outcome is on
the row (``staff_notified_*``) and shown on the agenda and the patient's page.

Anything that goes wrong after the form was accepted makes the check-in **fail** — expired,
no payment method on file, the pre-authorization declined, the link could not be delivered,
the visit canceled meanwhile … — and ``failure_reason`` says why in plain words. The patient's
board (My appointments, the appointment page) and the office's agenda both show it; the
patient (within the window) or the office starts a new check-in. Every attempt is a row
(``AppointmentCheckIn``): what was answered and signed stays on record, also for a failure.

Administrators: GET /api/v1/appointments/{id}/check-ins — every attempt with the answers, both
signatures, the estimate and the pre-authorization (the office administration dashboard).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

import appointments
import config
import esign
import models
import patient_payments
import schemas
from database import get_db

log = logging.getLogger("account.check_ins")

router = APIRouter(prefix="/check-ins", tags=["appointments"])

STAFF = "staff"
PATIENT_CHANNELS = ("account", "link", "identity")

# ---------------------------------------------------------------------------- the questionnaire

QUESTIONNAIRE_VERSION = "2027-10"
QUESTIONNAIRE_TITLE = "Before your visit"
QUESTIONNAIRE_INTRO = (
    "A few questions about today's visit for your provider. Your answers are kept with your medical "
    "record and seen only by the office."
)

_YES_NO = [{"value": "yes", "label": "Yes"}, {"value": "no", "label": "No"}]
QUESTIONS: tuple[dict, ...] = (
    {
        "id": "reason",
        "label": "What is the main reason for this visit?",
        "kind": "textarea",
        "required": True,
        "max_length": 500,
    },
    {
        "id": "symptoms_since",
        "label": "How long have you had these symptoms?",
        "kind": "choice",
        "required": True,
        "options": [
            {"value": "none", "label": "No symptoms — a routine or follow-up visit"},
            {"value": "days", "label": "Less than a week"},
            {"value": "weeks", "label": "One to four weeks"},
            {"value": "months", "label": "More than a month"},
        ],
    },
    {
        "id": "pain_level",
        "label": "Pain right now, from 0 (none) to 10 (worst imaginable)",
        "kind": "scale",
        "required": True,
    },
    {"id": "fever", "label": "Have you had a fever in the last 48 hours?", "kind": "yesno", "required": True},
    {
        "id": "medications_changed",
        "label": "Have your medications changed since your last visit?",
        "kind": "yesno",
        "required": True,
    },
    {
        "id": "medications",
        "label": "Medications you take now (name and dose)",
        "kind": "textarea",
        "required": False,
        "max_length": 1000,
        "hint": "Leave empty if none.",
    },
    {
        "id": "allergies",
        "label": "Allergies (medicines, food, latex, …)",
        "kind": "text",
        "required": True,
        "max_length": 300,
        "hint": 'Write "none" if you have no known allergies.',
    },
    {
        "id": "pregnant",
        "label": "Are you pregnant, or could you be?",
        "kind": "choice",
        "required": True,
        "options": [
            {"value": "no", "label": "No"},
            {"value": "yes", "label": "Yes / possibly"},
            {"value": "not_applicable", "label": "Not applicable"},
        ],
    },
    {
        "id": "details_changed",
        "label": "Have your address, phone number or insurance changed since your last visit?",
        "kind": "yesno",
        "required": True,
        "hint": "If yes, please tell the front desk or update your details on the registration page.",
    },
    {
        "id": "consent_treatment",
        "label": "I consent to examination and treatment by the office's providers for this visit.",
        "kind": "consent",
        "required": True,
    },
)
for _q in QUESTIONS:
    if _q["kind"] == "yesno":
        _q["options"] = _YES_NO


def questionnaire() -> schemas.CheckInQuestionnaire:
    return schemas.CheckInQuestionnaire(
        version=QUESTIONNAIRE_VERSION,
        title=QUESTIONNAIRE_TITLE,
        intro=QUESTIONNAIRE_INTRO,
        questions=[schemas.CheckInQuestion(**q) for q in QUESTIONS],
    )


def _bad(question: dict, message: str) -> HTTPException:
    return HTTPException(status_code=422, detail=f"{question['label']} — {message}")


def validate_answers(raw: dict) -> dict:
    """The submitted answers → a clean dict (only known questions). ``422`` names the question."""
    clean: dict = {}
    for q in QUESTIONS:
        value = raw.get(q["id"])
        if isinstance(value, str):
            value = value.strip()
        empty = value is None or value == "" or (q["kind"] == "consent" and value is False)
        if empty:
            if q["required"]:
                raise _bad(q, "please answer this question." if q["kind"] != "consent" else "please tick to consent.")
            continue
        kind = q["kind"]
        if kind in ("text", "textarea"):
            if not isinstance(value, str):
                raise _bad(q, "expected text.")
            if len(value) > q.get("max_length", 500):
                raise _bad(q, f"please keep it under {q.get('max_length', 500)} characters.")
        elif kind in ("choice", "yesno"):
            if value not in {o["value"] for o in q["options"]}:
                raise _bad(q, "please choose one of the options.")
        elif kind == "scale":
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise _bad(q, "please choose a number from 0 to 10.") from None
            if not 0 <= value <= 10:
                raise _bad(q, "please choose a number from 0 to 10.")
        elif kind == "consent":
            if value is not True:
                raise _bad(q, "please tick to consent.")
        clean[q["id"]] = value
    return clean


# ---------------------------------------------------------------------------- the estimate

GENERAL_SPECIALTIES = ("general", "family", "primary", "internal")


def money(cents: int, currency: str = "USD") -> str:
    return f"${cents / 100:,.2f}" if currency == "USD" else f"{cents / 100:,.2f} {currency}"


def _is_specialist(provider: models.Worker | None) -> bool:
    if provider is None or provider.role != "doctor" or not provider.specialty:
        return False
    return not any(w in provider.specialty.lower() for w in GENERAL_SPECIALTIES)


def estimate(db: Session, a: models.Appointment, patient: models.Patient) -> schemas.CheckInEstimate:
    """The estimated cost of the visit not covered by insurance, from the coverage checked again
    for the day of the visit (appointments.coverage_for — re-uses a check younger than
    COVERAGE_REUSE_SECONDS):

      * no active coverage                 → the self-pay visit fee (SELF_PAY_VISIT_CENTS);
      * covered, the plan names a copay    → the office-visit copay (the specialist copay for a
                                             doctor whose specialty is not general practice);
      * covered, no copay                  → the deductible still open (up to the visit's
                                             VISIT_ALLOWED_CENTS), then the coinsurance on the rest.
    """
    day = appointments.as_utc(a.start_at).astimezone(appointments.office_tz()).date()
    cover = appointments.coverage_for(db, patient, day)
    provider = db.get(models.Worker, a.provider_id)
    currency = config.BILLING_CURRENCY
    lines: list[schemas.EstimateLine] = []
    notes: list[str] = []
    if not cover.covered:
        lines.append(
            schemas.EstimateLine(
                label="Office visit — self-pay (not covered by insurance)", amount_cents=config.SELF_PAY_VISIT_CENTS
            )
        )
    else:
        v = cover.verification
        specialist = _is_specialist(provider)
        copay = (
            v.specialist_copay_cents
            if specialist and v is not None and v.specialist_copay_cents is not None
            else (v.copay_cents if v is not None else None)
        )
        allowed = config.VISIT_ALLOWED_CENTS
        if copay is not None:
            label = f"{'Specialist' if specialist else 'Office visit'} copay"
            lines.append(schemas.EstimateLine(label=label, amount_cents=copay))
            notes.append(f"Your plan pays the rest of the office visit ({money(allowed, currency)} before insurance).")
        else:
            remaining = (v.deductible_remaining_cents or 0) if v is not None else 0
            deductible = min(remaining, allowed)
            if deductible:
                lines.append(schemas.EstimateLine(label="Deductible not met yet", amount_cents=deductible))
            rest = allowed - deductible
            pct = (v.coinsurance_percent or 0) if v is not None else 0
            if rest and pct:
                lines.append(
                    schemas.EstimateLine(
                        label=f"Coinsurance {pct}% of the remaining {money(rest, currency)}",
                        amount_cents=round(rest * pct / 100),
                    )
                )
        if v is not None and v.referral_required and specialist:
            notes.append(
                "Your plan requires a referral for specialist visits — without one it may not pay, and you "
                "would owe the full visit."
            )
    notes.append(
        "This is an estimate: tests or procedures added during the visit may cost more — any difference "
        "comes as a statement."
    )
    method = _payment_method(db, patient)
    replaces = sum(p.amount_cents for p in patient_payments.holds_for(db, a) if p.kind != "check_in")
    return schemas.CheckInEstimate(
        currency=currency,
        total_cents=sum(line.amount_cents for line in lines),
        lines=lines,
        covered=cover.covered,
        coverage_status=cover.status,
        coverage_message=cover.message,
        notes=notes,
        payment_method=_display(method),
        replaces_hold_cents=replaces,
    )


def _account_of(db: Session, patient: models.Patient) -> models.Account | None:
    account = db.get(models.Account, patient.account_id) if patient.account_id else None
    return account if account is not None and account.deleted_at is None else None


def _payment_method(db: Session, patient: models.Patient) -> models.PatientPaymentMethod | None:
    account = _account_of(db, patient)
    return patient_payments.default_method(db, account) if account is not None else None


def _display(m: models.PatientPaymentMethod | None) -> str | None:
    import patient_payment_methods as ppm

    return ppm.display(m) if m is not None else None


# ---------------------------------------------------------------------------- rows and output


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clock(value: datetime) -> str:
    """ "14:05 UTC" — in the office's time zone, always with the zone named (a reader elsewhere
    must not take it for their own time)."""
    local = appointments.as_utc(value).astimezone(appointments.office_tz())
    return f"{local:%H:%M} {local.tzname() or appointments.office_tz()}"


def latest(db: Session, a: models.Appointment) -> models.AppointmentCheckIn | None:
    return db.execute(
        select(models.AppointmentCheckIn)
        .where(models.AppointmentCheckIn.appointment_id == a.id)
        .order_by(models.AppointmentCheckIn.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def history(db: Session, a: models.Appointment) -> list[models.AppointmentCheckIn]:
    return (
        db.execute(
            select(models.AppointmentCheckIn)
            .where(models.AppointmentCheckIn.appointment_id == a.id)
            .order_by(models.AppointmentCheckIn.id.desc())
        )
        .scalars()
        .all()
    )


def _fail(c: models.AppointmentCheckIn, code: str, reason: str, now: datetime | None = None) -> None:
    c.status = "failed"
    c.failure_code = code
    c.failure_reason = reason[:500]
    c.failed_at = now or _now()
    log.info("check-in #%d failed (%s): %s", c.id or 0, code, reason)


def expire_if_due(db: Session, c: models.AppointmentCheckIn, now: datetime | None = None) -> bool:
    """A pending check-in whose page was not completed in time — or whose visit is no longer
    scheduled — fails now. True when it changed (caller commits)."""
    if c.status != "pending":
        return False
    now = now or _now()
    a = db.get(models.Appointment, c.appointment_id)
    if a is None or a.status != "scheduled":
        status = a.status.replace("_", " ") if a is not None else "gone"
        _fail(c, "appointment_not_scheduled", f"The appointment is {status} — it can no longer be checked in.", now)
        return True
    if now >= appointments.as_utc(c.expires_at):
        minutes = config.CHECK_IN_LINK_MINUTES
        if c.via == STAFF:
            sent = _clock(c.link_sent_at or c.created_at)
            reason = (
                f"The patient did not complete the check-in link sent at {sent} within {minutes} minutes "
                f"(it expired at {_clock(c.expires_at)}). Send a new link, or help the patient at the desk."
            )
        else:
            reason = (
                f"The check-in page opened at {_clock(c.created_at)} was not completed within {minutes} minutes. "
                "Please start the check-in again."
            )
        _fail(c, "expired", reason, now)
        return True
    return False


def expire_due(db: Session, now: datetime | None = None) -> int:
    """The sweep (reminders.run_once, every REMINDER_CHECK_SECONDS): fail what timed out."""
    now = now or _now()
    n = 0
    pending = select(models.AppointmentCheckIn).where(models.AppointmentCheckIn.status == "pending")
    for c in db.execute(pending).scalars():
        if expire_if_due(db, c, now):
            n += 1
    if n:
        db.commit()
    return n


def summary(db: Session, c: models.AppointmentCheckIn) -> schemas.CheckInSummary:
    """As the boards show it — a pending check-in past its time already reads as failed (the
    sweep stores that within a minute)."""
    expire_if_due(db, c)
    return schemas.CheckInSummary(
        id=c.id,
        status=c.status,
        via=c.via,
        started_at=c.created_at,
        expires_at=c.expires_at,
        link_channels=[x for x in (c.link_channels or "").split(",") if x],
        link_sent_to=[x for x in (c.link_sent_to or "").split(", ") if x],
        link_sent_at=c.link_sent_at,
        link_delivery_note=c.link_delivery_note,
        failure_code=c.failure_code,
        failure_reason=c.failure_reason,
        completed_at=c.completed_at,
        estimate_cents=c.estimate_cents,
        currency=c.currency,
        payment_id=c.payment_id,
        staff_name=_provider_name(db, c),
        staff_notified_at=c.staff_notified_at,
        staff_notify_channels=[x for x in (c.staff_notify_channels or "").split(",") if x],
        staff_notified_to=[x for x in (c.staff_notified_to or "").split(", ") if x],
        staff_notify_note=c.staff_notify_note,
    )


def _provider_name(db: Session, c: models.AppointmentCheckIn) -> str | None:
    a = db.get(models.Appointment, c.appointment_id)
    provider = db.get(models.Worker, a.provider_id) if a is not None else None
    return appointments._provider_name(provider) if provider is not None else None


def latest_summary(db: Session, a: models.Appointment) -> schemas.CheckInSummary | None:
    c = latest(db, a)
    return summary(db, c) if c is not None else None


def _stored_estimate(c: models.AppointmentCheckIn) -> schemas.CheckInEstimate | None:
    try:
        data = json.loads(c.estimate or "{}")
    except ValueError:
        return None
    return schemas.CheckInEstimate(**data) if data else None


def _payment(db: Session, c: models.AppointmentCheckIn) -> schemas.PatientPaymentOut | None:
    p = db.get(models.PatientPayment, c.payment_id) if c.payment_id else None
    return patient_payments.payment_out(db, p) if p is not None else None


def session_out(db: Session, c: models.AppointmentCheckIn) -> schemas.CheckInSession:
    a = db.get(models.Appointment, c.appointment_id)
    base = summary(db, c)
    patient = db.get(models.Patient, a.patient_id)
    if c.status == "pending":
        est = estimate(db, a, patient)
    else:
        est = _stored_estimate(c)
    try:
        answers = json.loads(c.answers or "{}")
    except ValueError:
        answers = {}
    if c.status == "pending" and not answers:
        answers = prefill(db, a)
    return schemas.CheckInSession(
        **base.model_dump(),
        appointment=appointments.appointment_out(db, a, with_link=False),
        questionnaire=questionnaire(),
        estimate=est,
        answers=answers,
        answers_signer_name=c.answers_signer_name,
        answers_signed_at=c.answers_signed_at,
        financial_signer_name=c.financial_signer_name,
        financial_signed_at=c.financial_signed_at,
        payment=_payment(db, c),
        can_restart=c.status in ("failed", "canceled") and appointments.can_check_in(a),
    )


def prefill(db: Session, a: models.Appointment) -> dict:
    """The answers of the last attempt for this visit (a retry after a failure), else the reason
    the patient gave when booking."""
    for c in history(db, a):
        try:
            earlier = json.loads(c.answers or "{}")
        except ValueError:
            earlier = {}
        if earlier:
            return {k: v for k, v in earlier.items() if k != "consent_treatment"}
    return {"reason": a.reason} if a.reason else {}


def record_out(db: Session, c: models.AppointmentCheckIn) -> schemas.CheckInRecord:
    base = summary(db, c)
    staff = db.get(models.Account, c.started_by) if c.started_by else None
    try:
        answers = json.loads(c.answers or "{}")
    except ValueError:
        answers = {}
    return schemas.CheckInRecord(
        **base.model_dump(),
        questionnaire_version=c.questionnaire_version,
        questions=questionnaire().questions,
        answers=answers,
        answers_signer_name=c.answers_signer_name,
        answers_signed_at=c.answers_signed_at,
        answers_signature_png=c.answers_signature_png,
        answers_esign_reference=c.answers_esign_reference,
        estimate=_stored_estimate(c),
        financial_signer_name=c.financial_signer_name,
        financial_signed_at=c.financial_signed_at,
        financial_signature_png=c.financial_signature_png,
        financial_esign_reference=c.financial_esign_reference,
        esign_provider=c.esign_provider,
        opened_at=c.opened_at,
        payment=_payment(db, c),
        started_by=staff.email if staff is not None else None,
    )


# ---------------------------------------------------------------------------- starting a check-in


def _check_window(a: models.Appointment, now: datetime) -> None:
    opens, closes = appointments.check_in_window(a)
    if now < opens:
        raise HTTPException(
            status_code=409,
            detail=f"Check-in opens {appointments._hours(config.CHECK_IN_WINDOW_HOURS)} before your appointment — "
            f"from {appointments.format_local(opens)} ({appointments.office_tz()}). Please come back then, "
            "or check in at the front desk.",
        )
    if now >= closes:
        raise HTTPException(
            status_code=409,
            detail="The time of this appointment has passed, so it cannot be checked in from the app. "
            "Please speak to the front desk.",
        )


def start(
    db: Session,
    a: models.Appointment,
    via: str,
    request: Request | None,
    actor: models.Account | None = None,
    now: datetime | None = None,
) -> tuple[models.AppointmentCheckIn, str]:
    """Open a check-in page for the visit. The patient (``via`` account / link / identity) only
    inside the window; the office (``via`` staff) at any time — then the link is sent to the
    patient (a check-in whose link cannot be delivered fails at once, saying why). A pending
    check-in of the same visit is replaced. Returns the row and its token. Caller commits."""
    now = now or _now()
    if a.status != "scheduled":
        raise HTTPException(
            status_code=409,
            detail=f"This appointment is {a.status.replace('_', ' ')} — there is nothing to check in for.",
        )
    if a.checked_in_at is not None:
        raise HTTPException(status_code=409, detail="This appointment is already checked in.")
    if via != STAFF:
        _check_window(a, now)
    for old in db.execute(
        select(models.AppointmentCheckIn).where(
            models.AppointmentCheckIn.appointment_id == a.id, models.AppointmentCheckIn.status == "pending"
        )
    ).scalars():
        old.status = "canceled"
        old.canceled_at = now
        old.failure_reason = "Replaced by a newer check-in."
    token = appointments.new_token()
    c = models.AppointmentCheckIn(
        appointment_id=a.id,
        status="pending",
        via=via,
        started_by=actor.id if actor is not None and via == STAFF else None,
        token_hash=appointments.hash_token(token),
        link=f"{appointments.app_base(request)}check-in/{token}",
        expires_at=now + timedelta(minutes=config.CHECK_IN_LINK_MINUTES),
        questionnaire_version=QUESTIONNAIRE_VERSION,
        currency=config.BILLING_CURRENCY,
        created_at=now,
    )
    db.add(c)
    db.flush()
    if via == STAFF:
        _send_link(db, c, a, now)
    log.info("check-in #%d started for %s (%s): %s", c.id, a.number, via, c.status)
    return c, token


def _send_link(db: Session, c: models.AppointmentCheckIn, a: models.Appointment, now: datetime) -> None:
    patient = db.get(models.Patient, a.patient_id)
    minutes = config.CHECK_IN_LINK_MINUTES
    until = _clock(c.expires_at)
    when = appointments.format_local(a.start_at)
    subject = f"Please check in for your {config.OFFICE_NAME} appointment {a.number} — within {minutes} minutes"
    body = "\n".join(
        [
            f"Hi {patient.first_name}, the front desk started your check-in for {when} ({a.number}).",
            "",
            "Please open the link, answer a few questions about your visit, review the estimated cost that",
            f"your insurance does not cover, and sign — within {minutes} minutes (until {until}):",
            f"  {c.link}",
            "",
            "The estimate is pre-authorized on the payment method on file when you sign. If the link expires,",
            "ask the front desk for a new one.",
        ]
    )
    sms = (
        f"{config.OFFICE_NAME}: please check in for {a.number} ({when}) within {minutes} min — answer, review "
        f"the estimate and sign: {c.link}"
    )
    sent, to, notes = appointments.deliver_to_patient(patient, subject, body, sms)
    c.link_channels = ",".join(sent)
    c.link_sent_to = ", ".join(to)
    c.link_delivery_note = ("; ".join(notes))[:500] or None
    if sent:
        c.link_sent_at = now
    else:
        _fail(
            c,
            "undeliverable",
            "The check-in link could not be sent to the patient — "
            + ("; ".join(notes) or "no e-mail address or phone number on the patient record")
            + ". Add an e-mail address or phone number to the record and send it again, or help the patient "
            "at the desk.",
            now,
        )


# ---------------------------------------------------------------------------- processing the page


def submit(db: Session, c: models.AppointmentCheckIn, body: schemas.CheckInSubmit) -> models.AppointmentCheckIn:
    """Process a signed check-in page (see the module docstring for the order). Form problems raise
    ``422``, a changed estimate ``409``; everything else ends as completed or failed (with the
    reason) — both are returned, the caller commits."""
    now = _now()
    if expire_if_due(db, c, now):
        return c
    if c.status != "pending":
        state = {"completed": "already completed", "failed": "failed", "canceled": "no longer valid"}[c.status]
        raise HTTPException(status_code=409, detail=f"This check-in is {state}. {c.failure_reason or ''}".strip())
    answers = validate_answers(body.answers)
    if not body.accept_financial_responsibility:
        raise HTTPException(
            status_code=422,
            detail="Please confirm that you are responsible for the charges not covered by your insurance.",
        )
    for label, sig in (("questionnaire", body.answers_signature), ("financial", body.financial_signature)):
        try:
            esign.validate_image(sig.image_png)
        except esign.ESignError as exc:
            raise HTTPException(status_code=422, detail=f"Your {label} signature: {exc}") from None
    a = db.get(models.Appointment, c.appointment_id)
    patient = db.get(models.Patient, a.patient_id)
    est = estimate(db, a, patient)
    if body.estimate_cents != est.total_cents:
        raise HTTPException(
            status_code=409,
            detail=f"The estimate changed to {money(est.total_cents, est.currency)} since the page was opened — "
            "please review it and sign again.",
        )

    # What the patient submitted stays on record — also when the check-in fails below.
    c.answers = json.dumps(answers)
    c.questionnaire_version = QUESTIONNAIRE_VERSION
    c.answers_signer_name = body.answers_signature.signer_name
    c.answers_signature_png = body.answers_signature.image_png
    c.answers_signed_at = now
    c.financial_signer_name = body.financial_signature.signer_name
    c.financial_signature_png = body.financial_signature.image_png
    c.financial_signed_at = now
    c.estimate_cents = est.total_cents
    c.currency = est.currency
    c.estimate = est.model_dump_json()

    try:
        signed_q, _ = esign.sign(
            c.answers_signer_name, c.answers_signature_png, "check_in_questionnaire", QUESTIONNAIRE_VERSION
        )
        signed_f, _ = esign.sign(
            c.financial_signer_name,
            c.financial_signature_png,
            "check_in_financial_responsibility",
            str(est.total_cents),  # the document "version": the amount the patient accepted
        )
    except esign.ESignError as exc:
        _fail(c, "signature", f"The signatures could not be recorded: {exc}", now)
        return c
    c.answers_esign_reference, c.financial_esign_reference = signed_q.reference, signed_f.reference
    c.esign_provider = signed_q.provider

    if a.status != "scheduled" or a.checked_in_at is not None:
        state = "already checked in" if a.checked_in_at is not None else a.status.replace("_", " ")
        _fail(c, "appointment_not_scheduled", f"The appointment is {state} — it can no longer be checked in.", now)
        return c

    hold = None
    if est.total_cents > 0:
        account = _account_of(db, patient)
        method = patient_payments.default_method(db, account) if account is not None else None
        if method is None:
            _fail(
                c,
                "no_payment_method",
                f"No validated payment method on file to pre-authorize the estimated {money(est.total_cents)} not "
                "covered by insurance. Add and validate a card, PayPal / Stripe account or bank account (Payment "
                "methods in the app, or at the front desk), then check in again.",
                now,
            )
            return c
        hold = patient_payments.preauthorize_check_in(db, a, patient, account, method, est.total_cents)
        c.payment_id = hold.id
        if hold.status != "authorized":
            _fail(
                c,
                "payment_declined",
                f"The pre-authorization of {money(est.total_cents)} on {hold.method_display} was declined: "
                f"{hold.message} Update the payment method or make another one the default, then check in again.",
                now,
            )
            return c
    patient_payments.release_replaced_holds(db, a, keep=hold)

    c.status = "completed"
    c.completed_at = now
    c.failure_code = c.failure_reason = None
    a.checked_in_at = now
    a.checked_in_via = c.via
    a.checked_in_by = c.started_by
    if a.confirmed_at is None:
        a.confirmed_at, a.confirmed_via = now, c.via
    log.info("check-in #%d completed: %s checked in (%s)", c.id, a.number, money(est.total_cents))
    notify_staff(db, c, a, now)
    return c


# ---------------------------------------------------------------------------- telling the medical staff


def _absolute(base: str) -> str:
    return appointments.app_base(None).rstrip("/") + base if base.startswith("/") else base


def _dashboard_link(a: models.Appointment) -> tuple[str, str] | None:
    """Where the provider reads the check-in: (label, URL) — their own "My appointments" on the
    staff dashboard (STAFF_URL) when it is configured, else the visit on the office
    administration dashboard's agenda (ADMIN_DASHBOARD_URL)."""
    staff = (config.STAFF_URL or "").strip()
    if staff:
        day = appointments.as_utc(a.start_at).astimezone(appointments.office_tz()).date().isoformat()
        return (
            "in My appointments on the staff dashboard",
            f"{_absolute(staff).rstrip('/')}/#/my-appointments?from={day}",
        )
    admin = (config.ADMIN_DASHBOARD_URL or "").strip()
    if admin:
        return "in the check-in record on the office administration dashboard", (
            f"{_absolute(admin).rstrip('/')}/#/appointments?q={a.number}"
        )
    return None


def notify_staff(db: Session, c: models.AppointmentCheckIn, a: models.Appointment, now: datetime) -> None:
    """The patient has checked in: tell the appointment's provider (the doctor / nurse / physician
    assistant on the visit) by e-mail and text to the staff record. Deliberately no medical
    details in the message — the answers are in the check-in record on the dashboard. Never
    fails the check-in: the outcome (or why it could not be sent) is kept on the row."""
    provider = db.get(models.Worker, a.provider_id)
    patient = db.get(models.Patient, a.patient_id)
    if provider is None:
        c.staff_notify_note = "No provider on the appointment — nobody to notify."
        return
    who = f"{patient.first_name} {patient.last_name}"
    if c.via == STAFF:
        how = "with the check-in link the front desk sent"
    elif c.via == "link":
        how = "in the app (appointment link)"
    else:
        how = "in the app"
    tz = appointments.office_tz()
    link = _dashboard_link(a)
    subject = f"Checked in: {who} ({patient.mrn}) for {a.number} at {_clock(a.start_at)}"
    lines = [
        f"{who} ({patient.mrn}) has checked in for the appointment with you.",
        "",
        f"  When:        {appointments.format_local(a.start_at)} ({tz})",
        f"  Number:      {a.number}",
        f"  Reason:      {a.reason or '—'}",
        f"  Checked in:  {_clock(now)} — {how}",
        "",
        "The visit questionnaire and the financial responsibility were signed"
        + (f"; {money(c.estimate_cents)} is pre-authorized." if c.estimate_cents else "."),
        (f"The answers are {link[0]}:\n  {link[1]}" if link else "The answers are in the check-in record."),
        "",
        "(No medical details are included in this message.)",
    ]
    sms = f"{config.OFFICE_NAME}: {who} ({patient.mrn}) checked in for {a.number} at {_clock(a.start_at)}."
    sent, to, notes = appointments.deliver(
        provider.email, provider.phone, subject, "\n".join(lines), sms, whose="the staff record"
    )
    c.staff_notify_channels = ",".join(sent)
    c.staff_notified_to = ", ".join(to)
    c.staff_notify_note = ("; ".join(notes))[:500] or None
    if sent:
        c.staff_notified_at = now
    log.info("check-in #%d: provider %s notified by %s", c.id, provider.email, ",".join(sent) or "nothing")


def cancel_pending(db: Session, a: models.Appointment, reason: str) -> bool:
    """The office calls a pending check-in off (the link stops working)."""
    changed = False
    for c in db.execute(
        select(models.AppointmentCheckIn).where(
            models.AppointmentCheckIn.appointment_id == a.id, models.AppointmentCheckIn.status == "pending"
        )
    ).scalars():
        c.status = "canceled"
        c.canceled_at = _now()
        c.failure_reason = reason
        changed = True
    return changed


def undo(db: Session, a: models.Appointment) -> None:
    """The office takes a completed check-in back: the appointment is no longer checked in and the
    check-in's pre-authorization is released (a new check-in places a new one). Caller commits."""
    c = latest(db, a)
    if c is not None and c.status == "completed":
        c.status = "canceled"
        c.canceled_at = _now()
        c.failure_reason = "Undone by the office."
        p = db.get(models.PatientPayment, c.payment_id) if c.payment_id else None
        if p is not None and p.status == "authorized":
            patient_payments.release(db, p, raise_on_failure=False)
    appointments.undo_check_in(a)


# ---------------------------------------------------------------------------- routes (the page)


def _by_token(db: Session, token: str) -> models.AppointmentCheckIn:
    c = (
        db.execute(
            select(models.AppointmentCheckIn).where(
                models.AppointmentCheckIn.token_hash == appointments.hash_token(token)
            )
        ).scalar_one_or_none()
        if token and len(token) <= 128
        else None
    )
    if c is None:
        raise HTTPException(status_code=404, detail="This check-in link is not valid.")
    return c


def _limit(request: Request) -> None:
    if not appointments.TOKEN_LIMITER.allow(appointments._caller(request)):
        raise HTTPException(status_code=429, detail="Too many requests. Please try again in a few minutes.")


@router.get("/{token}", response_model=schemas.CheckInSession)
def check_in_page(token: str, request: Request, db: Session = Depends(get_db)):
    """The check-in page: the questionnaire and the estimate (pending), or the result."""
    _limit(request)
    c = _by_token(db, token)
    if c.opened_at is None:
        c.opened_at = _now()
    expire_if_due(db, c)
    out = session_out(db, c)
    db.commit()
    return out


@router.post("/{token}", response_model=schemas.CheckInSession)
def submit_check_in(token: str, body: schemas.CheckInSubmit, request: Request, db: Session = Depends(get_db)):
    """Sign and process the page: completed (checked in, the estimate pre-authorized) or failed —
    ``failure_reason`` says why. ``422`` form problems, ``409`` the estimate changed / not pending."""
    _limit(request)
    c = _by_token(db, token)
    submit(db, c, body)
    db.commit()
    return session_out(db, c)


@router.post("/{token}/restart", response_model=schemas.CheckInStart)
def restart_check_in(token: str, request: Request, db: Session = Depends(get_db)):
    """After a failure (or expiry), the patient starts again from the same page — within the window."""
    _limit(request)
    c = _by_token(db, token)
    expire_if_due(db, c)
    if c.status not in ("failed", "canceled"):
        raise HTTPException(status_code=409, detail="This check-in is still open — finish it on this page.")
    a = db.get(models.Appointment, c.appointment_id)
    fresh, new_token = start(db, a, "link", request)
    db.commit()
    return schemas.CheckInStart(token=new_token, url=fresh.link, check_in=summary(db, fresh))
