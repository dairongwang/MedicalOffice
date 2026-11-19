"""Referrals written by the provider on the patient's chart — a signed referral letter for the specialist.

    The visit's provider — a doctor or physician assistant (models.PRESCRIBER_ROLES):
    POST /api/v1/staff/me/appointments/{id}/referrals                  write & sign {counterpart, specialty,
                                                                       urgency, reason, diagnosis_id, question,
                                                                       clinical_summary, include_results, the
                                                                       specialist's contact, valid_until,
                                                                       send_to_specialist, send_to_patient}
    GET  /api/v1/staff/me/appointments/{id}/referrals/{rid}/pdf        the letter as a PDF
    POST /api/v1/staff/me/appointments/{id}/referrals/{rid}/send       e-mail it {to: specialist | patient}
    POST /api/v1/staff/me/appointments/{id}/referrals/{rid}/withdraw   withdraw it {reason} (its author)

    The office (administrators):
    GET  /api/v1/patients/{id}/referrals/{rid}/pdf                     print the letter for the patient

A written referral is an outgoing referral note (models.PatientReferral — the office's referral notes,
clinical.py) with a **letter**: the specialist and their contact, the urgency, the reason, the diagnosis
(ICD-10), the question for the specialist, a clinical summary, and — taken from the chart when the PDF is
made — the current medications, allergies, active conditions, today's vital signs and, when asked for,
the recent results; the patient's insurance (a plan that requires referrals needs it). It is signed
electronically (the provider, the time, a verification code) and never edited afterwards: a mistake is
*withdrawn* (with the reason — whoever got the letter by e-mail is told) and a new one written.

**The office is told** about every new referral (OFFICE_NOTIFY_EMAIL / OFFICE_NOTIFY_PHONE — no medical
details), so that it can help the patient get the appointment; it follows the referral up on the patient's
Clinical page (scheduled → completed). Every hand-over is kept (ReferralDelivery) and audited. Outside
production every letter carries a "SAMPLE" banner: the data is dummy data.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

import accounts
import appointments
import assignments
import assistant_review
import auth
import config
import health_records
import messaging
import models
import pdf
import prescriptions
import schemas
import staff_accounts
import twofactor
from database import get_db

log = logging.getLogger("account.referrals")

staff_router = APIRouter(prefix="/staff/me/appointments", tags=["staff"])
admin_router = APIRouter(tags=["patients"])

MAX_RESULTS_IN_LETTER = 3
URGENCY_LABELS = {"routine": "Routine", "urgent": "URGENT"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def office_today() -> date:
    return _now().astimezone(appointments.office_tz()).date()


def can_refer(w: models.Worker | None) -> bool:
    return prescriptions.can_prescribe(w)


def _require_referrer(w: models.Worker) -> None:
    if not can_refer(w):
        raise HTTPException(status_code=403, detail="Only a doctor or a physician assistant can write referrals.")


def _verification(row: models.PatientReferral) -> str:
    msg = f"ref:{row.id}:{row.patient_id}:{appointments.as_utc(row.signed_at).isoformat()}".encode()
    return hmac.new(config.TOKEN_SECRET.encode("utf-8"), msg, hashlib.sha256).hexdigest()[:10].upper()


def _patient_name(p: models.Patient) -> str:
    return f"{p.first_name} {p.last_name}"


# ---------------------------------------------------------------------------- output


def deliveries_of(db: Session, row: models.PatientReferral) -> list[models.ReferralDelivery]:
    return (
        db.execute(
            select(models.ReferralDelivery)
            .where(models.ReferralDelivery.referral_id == row.id)
            .order_by(models.ReferralDelivery.id)
        )
        .scalars()
        .all()
    )


def referral_out(db: Session, row: models.PatientReferral, today: date | None = None) -> schemas.ReferralOut:
    today = today or office_today()
    a = db.get(models.Appointment, row.appointment_id) if row.appointment_id else None
    d = db.get(models.VisitDiagnosis, row.diagnosis_id) if row.diagnosis_id else None
    deliveries = []
    for x in deliveries_of(db, row):
        by = db.get(models.Account, x.by_account) if x.by_account else None
        deliveries.append(
            schemas.ReferralDeliveryOut(
                method=x.method,
                recipient=x.recipient,
                destination=x.destination,
                status=x.status,
                note=x.note,
                by=by.email if by else None,
                created_at=x.created_at,
            )
        )
    return schemas.ReferralOut(
        id=row.id,
        number=row.number,
        direction=row.direction,
        counterpart=row.counterpart,
        specialty=row.specialty,
        referral_date=row.referral_date,
        valid_until=row.valid_until,
        expired=bool(row.valid_until and row.valid_until < today and row.status in ("open", "scheduled")),
        reason=row.reason,
        note=row.note,
        status=row.status,
        appointment_id=row.appointment_id,
        appointment_number=a.number if a else None,
        created_at=row.created_at,
        updated_at=row.updated_at,
        letter=row.has_letter,
        provider_id=row.worker_id,
        provider_name=prescriptions._worker_name(db, row.worker_id),
        urgency=row.urgency,
        diagnosis=prescriptions.diagnosis_out(db, d) if d else None,
        question=row.question,
        clinical_summary=row.clinical_summary,
        include_results=row.include_results,
        counterpart_email=row.counterpart_email,
        counterpart_phone=row.counterpart_phone,
        counterpart_fax=row.counterpart_fax,
        counterpart_address=row.counterpart_address,
        signed_at=row.signed_at,
        verification=f"{row.number}-{row.verification}" if row.verification else None,
        withdrawn_at=row.withdrawn_at,
        withdraw_reason=row.withdraw_reason,
        deliveries=deliveries,
    )


# ---------------------------------------------------------------------------- writing, withdrawing


def write(
    db: Session, a: models.Appointment, w: models.Worker, body: schemas.ReferralWriteIn, actor: models.Account
) -> models.PatientReferral:
    """Write and sign the referral; tell the office; e-mail the letter if asked. Caller commits."""
    _require_referrer(w)
    if a.status in ("canceled", "no_show"):
        raise HTTPException(status_code=409, detail=f"This visit is {a.status.replace('_', ' ')}.")
    patient = db.get(models.Patient, a.patient_id)
    if body.diagnosis_id is not None:
        d = db.get(models.VisitDiagnosis, body.diagnosis_id)
        if d is None or d.appointment_id != a.id:
            raise HTTPException(status_code=422, detail="diagnosis_id: not a diagnosis of this visit")
    today = office_today()
    if body.valid_until is not None and body.valid_until < today:
        raise HTTPException(status_code=422, detail="valid_until: must not be in the past")
    if body.send_to_patient and not patient.email:
        raise HTTPException(
            status_code=422,
            detail="The patient has no e-mail address on file — print the letter for them instead.",
        )
    row = models.PatientReferral(
        patient_id=patient.id,
        direction="outgoing",
        counterpart=body.counterpart,
        specialty=body.specialty,
        referral_date=today,
        valid_until=body.valid_until,
        reason=body.reason,
        status="open",
        appointment_id=a.id,
        entered_by=actor.id,
        worker_id=w.id,
        urgency=body.urgency,
        diagnosis_id=body.diagnosis_id,
        question=body.question,
        clinical_summary=body.clinical_summary,
        include_results=body.include_results,
        counterpart_email=str(body.counterpart_email) if body.counterpart_email else None,
        counterpart_phone=body.counterpart_phone,
        counterpart_fax=body.counterpart_fax,
        counterpart_address=body.counterpart_address,
        signed_at=_now(),
    )
    db.add(row)
    db.flush()
    row.verification = _verification(row)
    accounts.audit(
        db,
        actor,
        "referral.write",
        None,
        patient=patient.mrn,
        referral=row.number,
        specialty=row.specialty,
        urgency=row.urgency,
    )
    log.info("%s written for %s by %s (%s, %s)", row.number, patient.mrn, w.staff_number, row.specialty, row.urgency)
    _notify_office(db, row, actor)
    if body.send_to_specialist:
        send(db, row, "specialist", actor)
    if body.send_to_patient:
        send(db, row, "patient", actor)
    return row


def withdraw(db: Session, row: models.PatientReferral, w: models.Worker, reason: str, actor: models.Account) -> None:
    """Withdraw a written referral (only its author, while it is open / scheduled): whoever got the letter
    by e-mail — the specialist, the patient — and the office are told. Caller commits."""
    if not row.has_letter:
        raise HTTPException(status_code=409, detail="Only a referral written on the chart can be withdrawn here.")
    if row.worker_id != w.id:
        raise HTTPException(status_code=403, detail="Only the provider who wrote this referral can withdraw it.")
    if row.status not in ("open", "scheduled"):
        raise HTTPException(status_code=409, detail=f"This referral is already {row.status}.")
    row.status, row.withdrawn_at, row.withdraw_reason = "withdrawn", _now(), reason
    db.flush()
    told = {
        d.recipient
        for d in deliveries_of(db, row)
        if d.method == "email" and d.status == "sent" and d.recipient in ("specialist", "patient")
    }
    for recipient in ("specialist", "patient"):
        if recipient in told:
            send(db, row, recipient, actor, withdraw_notice=True)
    _notify_office(db, row, actor, withdrawn=True)
    accounts.audit(db, actor, "referral.withdraw", None, referral=row.number, reason=reason)


# ---------------------------------------------------------------------------- hand-over


def _record(
    db: Session,
    row: models.PatientReferral,
    recipient: str,
    destination: str | None,
    ok: bool,
    notes: list[str],
    actor: models.Account | None,
) -> models.ReferralDelivery:
    delivery = models.ReferralDelivery(
        referral_id=row.id,
        method="email",
        recipient=recipient,
        destination=destination,
        status="sent" if ok else "failed",
        note=("; ".join(notes))[:500] or None,
        by_account=actor.id if actor else None,
    )
    db.add(delivery)
    db.flush()
    return delivery


def send(
    db: Session,
    row: models.PatientReferral,
    to: str,
    actor: models.Account | None,
    *,
    withdraw_notice: bool = False,
) -> models.ReferralDelivery:
    """E-mail the letter (the PDF attached) to the specialist or a copy to the patient; recorded either way."""
    if not row.has_letter:
        raise HTTPException(status_code=409, detail="Only a referral written on the chart has a letter to send.")
    if row.status == "withdrawn" and not withdraw_notice:
        raise HTTPException(status_code=409, detail="This referral is withdrawn — it cannot be sent.")
    patient = db.get(models.Patient, row.patient_id)
    if to == "specialist":
        address = row.counterpart_email
        if not address:
            raise HTTPException(
                status_code=422, detail="There is no e-mail address for the specialist on this referral."
            )
    else:
        address = patient.email
        if not address:
            raise HTTPException(status_code=422, detail="The patient has no e-mail address on file.")
    subject, body = _message(db, row, patient, to, withdraw_notice)
    attachment = messaging.Attachment(
        filename=f"{row.number}{'-WITHDRAWN' if withdraw_notice else ''}.pdf",
        content_type="application/pdf",
        data=render_pdf(db, row),
    )
    channel = messaging.channel(messaging.EMAIL)
    notes = ["withdrawal notice"] if withdraw_notice else []
    ok = False
    try:
        if not channel.available:
            raise messaging.MessagingError("e-mail is not set up on this server")
        channel.send(address, body, subject=subject, attachments=[attachment])
        ok = True
        if channel.fake:
            notes.append(f"built-in fake — read it at {messaging.OUTBOX_PATH}")
    except messaging.MessagingError as exc:
        notes.append(str(exc))
    delivery = _record(db, row, to, twofactor.mask_email(address), ok, notes, actor)
    accounts.audit(db, actor, "referral.send", None, referral=row.number, to=to, status=delivery.status)
    log.info("%s e-mailed to the %s: %s", row.number, to, delivery.status)
    return delivery


def _office_line() -> str:
    return config.OFFICE_NAME + (f", {config.OFFICE_PHONE}" if config.OFFICE_PHONE else "")


def _message(
    db: Session, row: models.PatientReferral, patient: models.Patient, to: str, withdrawn: bool
) -> tuple[str, str]:
    provider = prescriptions._worker_name(db, row.worker_id) or "the provider"
    name = _patient_name(patient)
    dob = f"{patient.date_of_birth:%m/%d/%Y}"
    urgent = row.urgency == "urgent"
    valid = f"{row.valid_until:%b %d, %Y}" if row.valid_until else None
    if to == "specialist":
        if withdrawn:
            subject = f"WITHDRAWN — referral {row.number} for {name}"
            lines = [
                f"Dear colleague at {row.counterpart},",
                "",
                f"referral {row.number} for {name} (date of birth {dob}), sent to you earlier, has been",
                f"WITHDRAWN by {provider}. Please do not book the patient on it.",
                f"Reason: {row.withdraw_reason}",
            ]
        else:
            subject = f"{'URGENT — ' if urgent else ''}Referral {row.number}: {name} ({dob}) — {row.specialty}"
            lines = [
                f"Dear colleague at {row.counterpart},",
                "",
                f"{provider} refers {name} (date of birth {dob}) to you — {row.specialty}"
                + (", URGENT." if urgent else "."),
                f"Reason: {row.reason}",
                *([f"Valid until: {valid}"] if valid else []),
                "",
                "The referral letter with the clinical summary is attached. Please send your consultation report",
                f"to {_office_line()}.",
            ]
    else:
        if withdrawn:
            subject = f"Your referral to {row.counterpart} has been withdrawn"
            lines = [
                f"Hi {patient.first_name},",
                "",
                f"the referral to {row.counterpart} ({row.specialty}) that {provider} wrote for you has been",
                "withdrawn. Please do not use the letter you received — we will contact you about the next steps.",
            ]
        else:
            subject = f"Your referral to {row.counterpart} ({row.specialty})"
            contact = [
                x for x in (row.counterpart_address, row.counterpart_phone and f"phone {row.counterpart_phone}") if x
            ]
            lines = [
                f"Hi {patient.first_name},",
                "",
                f"{provider} has referred you to {row.counterpart} — {row.specialty}.",
                "Please contact them to make an appointment"
                + (" within the next few days (urgent)" if urgent else "")
                + (f": {', '.join(contact)}." if contact else "."),
                *([f"The referral is valid until {valid}."] if valid else []),
                "",
                "Your referral letter is attached — bring it (printed or on your phone) to the appointment.",
            ]
    lines += ["", f"Referral {row.number}-{row.verification}. Questions: {_office_line()}."]
    return subject, "\n".join(lines)


def _admin_link(patient: models.Patient) -> str | None:
    admin = (config.ADMIN_DASHBOARD_URL or "").strip()
    if not admin:
        return None
    base = appointments.app_base(None).rstrip("/") + admin if admin.startswith("/") else admin
    return f"{base.rstrip('/')}/#/patients/{patient.id}/clinical"


def _notify_office(
    db: Session, row: models.PatientReferral, actor: models.Account | None, *, withdrawn: bool = False
) -> None:
    """Tell the office about a new (or withdrawn) referral — no medical details — when an address / number
    for it is configured; recorded on the referral (recipient "office")."""
    if not (config.OFFICE_NOTIFY_EMAIL or config.OFFICE_NOTIFY_PHONE):
        return
    patient = db.get(models.Patient, row.patient_id)
    provider = prescriptions._worker_name(db, row.worker_id) or "a provider"
    urgent = " (URGENT)" if row.urgency == "urgent" else ""
    what = "Withdrawn" if withdrawn else "New"
    subject = f"{what} referral {row.number}: {_patient_name(patient)} → {row.specialty}{urgent}"
    lines = [
        f"{provider} {'withdrew' if withdrawn else 'wrote'} a referral:",
        "",
        f"  Patient:   {_patient_name(patient)} ({patient.mrn})",
        f"  To:        {row.counterpart} — {row.specialty}{urgent}",
        *([f"  Valid:     until {row.valid_until:%b %d, %Y}"] if row.valid_until and not withdrawn else []),
        "",
        (
            "No need to schedule it any more."
            if withdrawn
            else "Please help the patient get the appointment and follow it up (Patients → Clinical → Referral notes):"
        ),
        *([f"  {_admin_link(patient)}"] if _admin_link(patient) and not withdrawn else []),
    ]
    sms = f"{config.OFFICE_NAME}: {what.lower()} referral {row.number} for {patient.mrn} → {row.specialty}{urgent}."
    sent, to, notes = appointments.deliver(
        config.OFFICE_NOTIFY_EMAIL or None,
        config.OFFICE_NOTIFY_PHONE or None,
        subject,
        "\n".join(lines),
        sms,
        whose="the office settings",
    )
    if withdrawn:
        notes.insert(0, "withdrawal notice")
    _record(db, row, "office", ", ".join(to) or None, bool(sent), notes, actor)


def record_download(db: Session, row: models.PatientReferral, actor: models.Account) -> None:
    db.add(
        models.ReferralDelivery(
            referral_id=row.id, method="pdf", recipient="-", status="downloaded", by_account=actor.id
        )
    )
    accounts.audit(db, actor, "referral.pdf", None, referral=row.number)


# ---------------------------------------------------------------------------- the letter (PDF)


def _vitals_text(db: Session, row: models.PatientReferral) -> str | None:
    a = db.get(models.Appointment, row.appointment_id) if row.appointment_id else None
    if a is None:
        return None
    tasks = assignments.history(db, a)
    task = next((t for t in tasks if t.status == "completed"), None) or assignments.current_for(db, a)
    vitals = assistant_review.vitals_out(assistant_review.vitals_of(task)) if task else None
    return vitals.text if vitals and vitals.text else None


def _result_lines(db: Session, patient: models.Patient) -> list[str]:
    import clinical  # clinical imports this module

    out = []
    for r in clinical.results_of(db, patient)[:MAX_RESULTS_IN_LETTER]:
        when = r.resulted_on or r.ordered_on
        head = f"{r.name}{f' ({when:%m/%d/%Y})' if when else ''}{'' if r.status == 'final' else f' — {r.status}'}"
        values = clinical._values(r)
        flagged = [v for v in values if v.flag]
        if r.kind == "imaging":
            out.append(f"{head}: {r.summary or 'no impression recorded'}")
        elif flagged:
            parts = [
                f"{v.name} {v.value}{f' {v.unit}' if v.unit else ''} {v.flag}{f' (ref. {v.range})' if v.range else ''}"
                for v in flagged
            ]
            out.append(f"{head}: out of range — {'; '.join(parts)}")
        else:
            out.append(f"{head}: {'all values within range' if values else r.summary or 'see report'}")
    return out


def render_pdf(db: Session, row: models.PatientReferral) -> bytes:
    """The referral letter as a Letter-size PDF (one page or more)."""
    patient = db.get(models.Patient, row.patient_id)
    provider = db.get(models.Worker, row.worker_id) if row.worker_id else None
    d = db.get(models.VisitDiagnosis, row.diagnosis_id) if row.diagnosis_id else None
    tz = appointments.office_tz()
    signed = appointments.as_utc(row.signed_at or row.created_at).astimezone(tz)
    doc = pdf.PdfDocument(title=f"Referral {row.number}", author=config.OFFICE_NAME, margin=FOOTER_TOP)
    left, right, width = 54, 558, 504
    col2 = 310
    col1_width, col2_width = col2 - left - 14, right - col2

    def header() -> float:
        doc.text(
            left, 750, f"{config.OFFICE_NAME} · referral {row.number} — {_patient_name(patient)} (continued)", size=9
        )
        return 726

    def room(y: float, needed: float) -> float:
        if y - needed < FOOTER_TOP:
            doc.new_page()
            return header()
        return y

    y = 738
    doc.text(left, y, config.OFFICE_NAME, size=18, bold=True)
    doc.text_right(right, y, "REFERRAL", size=14, bold=True)
    y -= 16
    office = " · ".join(x for x in (config.OFFICE_ADDRESS, config.OFFICE_PHONE and f"Phone {config.OFFICE_PHONE}") if x)
    if office:
        doc.text(left, y, office, size=9, gray=0.35)
    doc.text_right(right, y, row.number, size=10, bold=True)
    y -= 12
    doc.line(left, y, right, y, width=1.2)
    y -= 20

    # To (the specialist) and from (the referring provider).
    doc.text(left, y, "TO", size=8, bold=True, gray=0.4)
    doc.text(col2, y, "FROM", size=8, bold=True, gray=0.4)
    y -= 14
    contact = " · ".join(
        x
        for x in (
            row.counterpart_phone and f"Phone {row.counterpart_phone}",
            row.counterpart_fax and f"Fax {row.counterpart_fax}",
        )
        if x
    )
    to_lines = [
        (row.counterpart, True),
        (row.specialty or "", False),
        (row.counterpart_address or "", False),
        (contact, False),
        (row.counterpart_email or "", False),
    ]
    role = appointments.workers_role_label(provider.role) if provider else ""
    from_lines = [
        (appointments._provider_name(provider) if provider else "—", True),
        (" · ".join(x for x in (role, provider.specialty if provider else None) if x), False),
        (f"Licence: {provider.license_number}" if provider and provider.license_number else "", False),
        (config.OFFICE_NAME, False),
        (f"Phone {config.OFFICE_PHONE}" if config.OFFICE_PHONE else "", False),
    ]
    y = doc.columns(y, (left, col1_width, to_lines), (col2, col2_width, from_lines))
    y -= 6
    doc.line(left, y, right, y)
    y -= 18

    # The patient (left) and the insurance (right).
    doc.text(left, y, "PATIENT", size=8, bold=True, gray=0.4)
    doc.text(col2, y, "INSURANCE", size=8, bold=True, gray=0.4)
    y -= 14
    address = ", ".join(
        x
        for x in (
            patient.address_line,
            patient.postal_code and patient.city and f"{patient.postal_code} {patient.city}" or patient.city,
            patient.state,
        )
        if x
    )
    sex = f" · {patient.sex}" if patient.sex and patient.sex != "unspecified" else ""
    patient_lines = [
        (_patient_name(patient), True),
        (f"Date of birth: {patient.date_of_birth:%m/%d/%Y}{sex} · {patient.mrn}", False),
        (address or "Address: not on file", False),
        (f"Phone: {patient.phone}" if patient.phone else "", False),
    ]
    insurance_lines = [
        (patient.insurance_provider or "Self-pay", True),
        (patient.insurance_plan_name or "", False),
        (f"Member ID: {patient.insurance_member_id}" if patient.insurance_member_id else "", False),
        (f"Group: {patient.insurance_group_number}" if patient.insurance_group_number else "", False),
    ]
    y = doc.columns(y, (left, col1_width, patient_lines), (col2, col2_width, insurance_lines))
    y -= 4
    urgency = URGENCY_LABELS.get(row.urgency or "", "Routine")
    doc.text(left, y, f"Date: {row.referral_date:%B} {row.referral_date.day}, {row.referral_date.year}", size=10)
    doc.text(left + 170, y, f"Urgency: {urgency}", size=10, bold=row.urgency == "urgent")
    if row.valid_until:
        doc.text(col2 + 60, y, f"Valid until: {row.valid_until:%m/%d/%Y}", size=10)
    y -= 12
    doc.line(left, y, right, y)
    y -= 20

    def section(title: str, text: str | list[str] | None) -> None:
        nonlocal y
        if not text:
            return
        y = room(y, 40)
        doc.text(left, y, title.upper(), size=8.5, bold=True, gray=0.35)
        y -= 14
        paragraphs = text if isinstance(text, list) else text.split("\n")
        for para in paragraphs:
            if not para.strip():
                y -= 5
                continue
            y = doc.paragraph(left, y, para, width=width, size=10, on_new_page=header)
        y -= 8

    section("Reason for referral", row.reason)
    if d is not None:
        section("Diagnosis", f"{d.code + ' — ' if d.code else ''}{d.description}")
    section("Question for the specialist", row.question)
    section("Clinical summary", row.clinical_summary)

    def items(kind: str) -> list[str]:
        return [
            f"• {e.name}{f' — {e.detail}' if e.detail else ''}"
            for e in health_records.items_of(db, patient, kind, active_only=True)
        ]

    section("Current medications", items("medication") or ["None recorded."])
    section("Allergies", items("allergy") or ["No known allergies."])
    section("Active conditions", items("condition"))
    vitals = _vitals_text(db, row)
    if vitals:
        section("Vital signs at the visit", vitals)
    if row.include_results:
        section("Recent results", [f"• {x}" for x in _result_lines(db, patient)] or ["No results on record."])

    y = room(y, 50)
    doc.line(left, y, right, y)
    y -= 18
    signer = appointments._provider_name(provider) if provider else "the provider"
    doc.text(left, y, f"Electronically signed by {signer}", size=10, bold=True)
    doc.text(left, y - 13, f"on {signed:%B} {signed.day}, {signed.year} at {signed:%H:%M} ({tz})", size=9.5)
    doc.text_right(right, y, f"Verification: {row.number}-{row.verification or ''}", size=9.5)
    doc.text_right(right, y - 13, f"Please send your report to {config.OFFICE_NAME}.", size=8.5)
    y -= 40
    if row.status == "withdrawn":
        y = room(y, 60)
        withdrawn = appointments.as_utc(row.withdrawn_at).astimezone(tz)
        when = f"{withdrawn:%B} {withdrawn.day}, {withdrawn.year} at {withdrawn:%H:%M}"
        doc.text(left, y, "WITHDRAWN — DO NOT USE", size=26, bold=True, gray=0.15)
        doc.paragraph(
            left,
            y - 18,
            f"Withdrawn on {when}: {row.withdraw_reason}",
            width=width,
            size=10,
        )
    doc.on_each_page(
        lambda n, total: footer(
            doc, n, total, f"Referral {row.number}", "SAMPLE — dummy data (development): not a real referral"
        )
    )
    return doc.output()


FOOTER_TOP = 72  # the content stays above the footer (and the SAMPLE banner outside production)


def footer(doc: pdf.PdfDocument, n: int, total: int, what: str, sample: str) -> None:
    """Page n of total — and, outside production, the SAMPLE banner — on every page of a letter / prescription."""
    left, right, width = 54, 558, 504
    if not config.IS_PROD:
        doc.box(left, 40, width, 22, gray=0.9)
        doc.text(left + 10, 47, sample, size=11, bold=True)
    doc.text(left, 26, f"{config.OFFICE_NAME} · {what}", size=8, gray=0.45)
    doc.text_right(right, 26, f"Page {n} of {total}", size=8)


# ---------------------------------------------------------------------------- routes: the provider


def _patients_referral(db: Session, a: models.Appointment, referral_id: int) -> models.PatientReferral:
    row = db.get(models.PatientReferral, referral_id)
    if row is None or row.patient_id != a.patient_id:
        raise HTTPException(status_code=404, detail="Referral not found.")
    return row


def _pdf_response(row: models.PatientReferral, data: bytes) -> Response:
    return Response(
        content=data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{row.number}.pdf"',
            "Cache-Control": "private, no-store",
        },
    )


def _letter(row: models.PatientReferral) -> models.PatientReferral:
    if not row.has_letter:
        raise HTTPException(status_code=404, detail="This referral note has no letter (the office entered it).")
    return row


@staff_router.post("/{appointment_id}/referrals", response_model=schemas.ReferralOut, status_code=201)
def post_referral(
    appointment_id: int,
    body: schemas.ReferralWriteIn,
    s: staff_accounts.StaffPrincipal = Depends(staff_accounts.staff_principal),
    db: Session = Depends(get_db),
):
    """Write and sign a referral (the office is told; e-mailed to the specialist / the patient when asked)."""
    a = prescriptions._my_visit(db, s, appointment_id)
    row = write(db, a, s.worker, body, s.principal.account)
    db.commit()
    return referral_out(db, row)


@staff_router.get(
    "/{appointment_id}/referrals/{referral_id}/pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}, "description": "The referral letter"}},
)
def get_referral_pdf(
    appointment_id: int,
    referral_id: int,
    s: staff_accounts.StaffPrincipal = Depends(staff_accounts.staff_principal),
    db: Session = Depends(get_db),
):
    a = prescriptions._my_visit(db, s, appointment_id)
    row = _letter(_patients_referral(db, a, referral_id))
    data = render_pdf(db, row)
    record_download(db, row, s.principal.account)
    db.commit()
    return _pdf_response(row, data)


@staff_router.post("/{appointment_id}/referrals/{referral_id}/send", response_model=schemas.ReferralOut)
def post_referral_send(
    appointment_id: int,
    referral_id: int,
    body: schemas.ReferralSend,
    s: staff_accounts.StaffPrincipal = Depends(staff_accounts.staff_principal),
    db: Session = Depends(get_db),
):
    """E-mail the letter (PDF attached) to the specialist, or a copy to the patient."""
    a = prescriptions._my_visit(db, s, appointment_id)
    row = _patients_referral(db, a, referral_id)
    _require_referrer(s.worker)
    send(db, row, body.to, s.principal.account)
    db.commit()
    return referral_out(db, row)


@staff_router.post("/{appointment_id}/referrals/{referral_id}/withdraw", response_model=schemas.ReferralOut)
def post_referral_withdraw(
    appointment_id: int,
    referral_id: int,
    body: schemas.ReferralWithdraw,
    s: staff_accounts.StaffPrincipal = Depends(staff_accounts.staff_principal),
    db: Session = Depends(get_db),
):
    a = prescriptions._my_visit(db, s, appointment_id)
    row = _patients_referral(db, a, referral_id)
    withdraw(db, row, s.worker, body.reason, s.principal.account)
    db.commit()
    return referral_out(db, row)


# ---------------------------------------------------------------------------- routes: the office


@admin_router.get(
    "/patients/{patient_id}/referrals/{referral_id}/pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}, "description": "The referral letter"}},
)
def patient_referral_pdf(
    patient_id: int, referral_id: int, p: auth.Principal = Depends(auth.admin_principal), db: Session = Depends(get_db)
):
    """The letter, for the front desk to print for the patient (recorded)."""
    row = db.get(models.PatientReferral, referral_id)
    if row is None or row.patient_id != patient_id:
        raise HTTPException(status_code=404, detail="Referral not found.")
    _letter(row)
    data = render_pdf(db, row)
    record_download(db, row, p.account)
    db.commit()
    return _pdf_response(row, data)
