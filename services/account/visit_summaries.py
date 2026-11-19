"""Visit summaries — after the visit, the patient gets a summary of it, signed by the doctor.

What it says (``build_content`` — generated from the chart, in plain words): the visit (when, with whom,
where), the reason the patient came, the vital signs the medical assistant took, the diagnoses, the
assessment and the plan, the medications prescribed at the visit, the referrals written and the tests
ordered, the current medication and allergy lists — plus the doctor's instructions and the follow-up.

How it comes about:

    prepared   automatically as a **draft** once the visit is over — when the office marks it completed
               (``on_appointment_status``), or when the background job (reminders.run_once → ``prepare_due``)
               finds a visit the provider saw that has ended — and the provider is told it waits for their
               signature (e-mail / text to the staff record, no medical details). Opening it on the chart
               prepares it too (once the visit has started).
    edited     until it is signed the generated part follows the chart (every read rebuilds it); the
               assessment and the plan are the visit note's unless the provider rewrites them for the
               patient; the instructions and the follow-up are the provider's.
    signed     by the visit's provider — a **doctor** (or a physician assistant, like prescriptions):
               typed full name + drawn signature + consent, recorded through esign.py (the built-in fake
               outside production), a verification code printed on the PDF. Signing freezes it.
    handed     to the patient: e-mailed with the PDF attached and / or a text that it is ready (messaging.py
               — the test inbox in development), and in the patient's app (My appointments → Past → Visit
               summary). The office sees it on the agenda and can print it or send it again.
    amended    a signed summary is never edited: a mistake is corrected by an amendment — a new version
               (a draft prefilled from it) that supersedes the old one once signed. Every version stays.

Routes

    The visit's provider (staff dashboard — the patient's chart):
    GET  /api/v1/staff/me/appointments/{id}/visit-summary            the summary (the draft is prepared here)
    PUT  /api/v1/staff/me/appointments/{id}/visit-summary            edit the draft's texts
    POST /api/v1/staff/me/appointments/{id}/visit-summary/sign       sign {signature, send_to_patient}
    POST /api/v1/staff/me/appointments/{id}/visit-summary/amend      a new version of a signed one {reason}
    POST /api/v1/staff/me/appointments/{id}/visit-summary/send       e-mail / text it to the patient again
    GET  /api/v1/staff/me/appointments/{id}/visit-summary/pdf        the PDF (a draft says DRAFT)

    A signed-in patient (the patients' app):
    GET  /api/v1/me/appointments/{id}/visit-summary                  the signed summary (404 until signed)
    GET  /api/v1/me/appointments/{id}/visit-summary/pdf

    The office (administrators):
    GET  /api/v1/appointments/{id}/visit-summary                     where it stands (draft / signed) + history
    GET  /api/v1/appointments/{id}/visit-summary/pdf                 print it for the patient
    POST /api/v1/appointments/{id}/visit-summary/send                send it to the patient again
    POST /api/v1/appointments/visit-summaries/run[?as_of=]          prepare the due drafts now (as_of outside prod)

Every signature, hand-over, PDF and patient view lands in the audit log. Outside production every PDF
carries the "SAMPLE" banner: the data is dummy data.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
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
from database import get_db

log = logging.getLogger("account.visit_summaries")

staff_router = APIRouter(prefix="/staff/me/appointments", tags=["staff"])
me_router = APIRouter(prefix="/me/appointments", tags=["me"])
admin_router = APIRouter(prefix="/appointments", tags=["appointments"])

DRAFT, SIGNED, SUPERSEDED = "draft", "signed", "superseded"
# The background job prepares drafts for visits that ended within this many days (older ones are left
# alone — the provider can still open them on the chart).
PREPARE_WITHIN_DAYS = 7


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _provider_name(w: models.Worker | None) -> str:
    return appointments._provider_name(w) if w is not None else "the provider"


def can_sign(w: models.Worker | None) -> bool:
    return w is not None and w.active and w.role in models.VISIT_SUMMARY_SIGNER_ROLES


# ---------------------------------------------------------------------------- the rows


def versions_of(db: Session, a: models.Appointment) -> list[models.VisitSummary]:
    """Every version of the visit's summary, newest first."""
    return (
        db.execute(
            select(models.VisitSummary)
            .where(models.VisitSummary.appointment_id == a.id)
            .order_by(models.VisitSummary.version.desc())
        )
        .scalars()
        .all()
    )


def current_of(db: Session, a: models.Appointment) -> models.VisitSummary | None:
    """What staff and the office work with: the draft being prepared / amended, else the signed version."""
    rows = versions_of(db, a)
    return next((s for s in rows if s.status == DRAFT), None) or next((s for s in rows if s.status == SIGNED), None)


def signed_of(db: Session, a: models.Appointment) -> models.VisitSummary | None:
    """What the patient gets: the signed version in force (not superseded)."""
    return next((s for s in versions_of(db, a) if s.status == SIGNED), None)


def brief(s: models.VisitSummary) -> schemas.VisitSummaryBrief:
    return schemas.VisitSummaryBrief(
        id=s.id,
        number=s.number,
        version=s.version,
        status=s.status,
        signed_at=s.signed_at,
        signer_name=s.signer_name,
        sent_at=s.sent_at,
        patient_viewed_at=s.patient_viewed_at,
    )


def brief_for(db: Session, a: models.Appointment, *, patient: bool) -> schemas.VisitSummaryBrief | None:
    """On the appointment: the patient sees the signed version only; staff and the office the current one."""
    s = signed_of(db, a) if patient else current_of(db, a)
    return brief(s) if s is not None else None


# ---------------------------------------------------------------------------- what it says


def _date(value) -> str:
    return f"{value:%m/%d/%Y}" if value else ""


def build_content(db: Session, a: models.Appointment) -> schemas.VisitSummaryContent:
    """The generated part, from the chart as it is now."""
    import clinical  # clinical imports appointments, which imports this module

    provider = db.get(models.Worker, a.provider_id)
    patient = db.get(models.Patient, a.patient_id)
    check_in = assistant_review.completed_check_in(db, a)
    reason = assistant_review._answers(check_in).get("reason") if check_in else None
    task = clinical._completed_task(db, a) or assignments.current_for(db, a)
    vitals = assistant_review.vitals_out(assistant_review.vitals_of(task)) if task else None
    note = clinical.note_of(db, a)

    diagnoses = sorted(prescriptions.diagnoses_of(db, a), key=lambda d: (not d.is_primary, d.id))
    diagnosis_lines = [
        f"{d.description}{f' ({d.code})' if d.code else ''}{' — primary' if d.is_primary else ''}"
        f"{f': {d.note}' if d.note else ''}"
        for d in diagnoses
    ]

    rx_lines = []
    for rx in db.execute(
        select(models.Prescription)
        .where(models.Prescription.appointment_id == a.id, models.Prescription.status == "issued")
        .order_by(models.Prescription.id)
    ).scalars():
        sent = next(
            (
                d
                for d in prescriptions.deliveries_of(db, rx)
                if d.method == "email" and d.status == "sent" and d.pharmacy_id
            ),
            None,
        )
        pharmacy = db.get(models.Pharmacy, sent.pharmacy_id) if sent else None
        where = f", sent to {pharmacy.name}" if pharmacy else ""
        for i in prescriptions.items_of(db, rx):
            refills = f"{i.refills} refill{'' if i.refills == 1 else 's'}"
            rx_lines.append(
                f"{prescriptions.item_line(i)} — {i.sig} — {i.quantity} {i.quantity_unit}, {refills} "
                f"({rx.number}{where})"
            )

    def items(kind: str) -> list[str]:
        return [
            f"{e.name}{f' — {e.detail}' if e.detail else ''}"
            for e in health_records.items_of(db, patient, kind, active_only=True)
        ]

    referral_lines = []
    for r in db.execute(
        select(models.PatientReferral)
        .where(
            models.PatientReferral.appointment_id == a.id,
            models.PatientReferral.direction == "outgoing",
            models.PatientReferral.status != "withdrawn",
        )
        .order_by(models.PatientReferral.id)
    ).scalars():
        urgent = " (urgent)" if r.urgency == "urgent" else ""
        valid = f" · valid until {r.valid_until:%b %d, %Y}" if r.valid_until else ""
        referral_lines.append(f"{r.specialty or 'Specialist'} — {r.counterpart}{urgent}{valid}: {r.reason}")

    test_lines = []
    for r in db.execute(
        select(models.PatientResult)
        .where(models.PatientResult.appointment_id == a.id)
        .order_by(models.PatientResult.id)
    ).scalars():
        values = clinical._values(r)
        when = f" ({_date(r.resulted_on)})" if r.resulted_on else ""
        state = {"pending": "result pending", "preliminary": "preliminary result"}.get(r.status, "result available")
        flagged = " — some values outside the reference range" if any(v.flag for v in values) else ""
        test_lines.append(f"{r.name} — {state}{when}{flagged}")

    role = appointments.workers_role_label(provider.role) if provider else ""
    return schemas.VisitSummaryContent(
        visit_number=a.number,
        visit_date=appointments.format_local(a.start_at),
        visit_start_at=appointments.as_utc(a.start_at),
        timezone=str(appointments.office_tz()),
        office_name=config.OFFICE_NAME,
        office_address=config.OFFICE_ADDRESS or None,
        office_phone=config.OFFICE_PHONE or None,
        provider_name=_provider_name(provider),
        provider_role=role,
        provider_specialty=provider.specialty if provider else None,
        patient_name=f"{patient.first_name} {patient.last_name}",
        patient_mrn=patient.mrn,
        patient_date_of_birth=patient.date_of_birth,
        reason=(reason if isinstance(reason, str) and reason.strip() else None) or a.reason,
        vitals=vitals.text if vitals and vitals.text else None,
        vitals_note=vitals.note if vitals else None,
        diagnoses=diagnosis_lines,
        prescriptions=rx_lines,
        medications=items("medication"),
        allergies=items("allergy"),
        referrals=referral_lines,
        tests=test_lines,
        note_assessment=note.assessment if note else None,
        note_plan=note.plan if note else None,
    )


def content_of(s: models.VisitSummary) -> schemas.VisitSummaryContent:
    return schemas.VisitSummaryContent.model_validate_json(s.content)


def _texts(s: models.VisitSummary, c: schemas.VisitSummaryContent) -> dict[str, str | None]:
    """The texts as the patient reads them (the visit note's assessment / plan unless rewritten)."""
    return {
        "assessment": s.assessment if s.assessment is not None else c.note_assessment,
        "plan": s.plan if s.plan is not None else c.note_plan,
        "instructions": s.instructions,
        "follow_up": s.follow_up,
    }


def refresh(db: Session, s: models.VisitSummary) -> None:
    """A draft follows the chart: rebuild the generated part (a signed version never changes)."""
    if s.status != DRAFT:
        return
    a = db.get(models.Appointment, s.appointment_id)
    fresh = build_content(db, a).model_dump_json()
    if fresh != s.content:
        s.content = fresh
        s.generated_at = _now()


# ---------------------------------------------------------------------------- preparing the draft


def visit_started(db: Session, a: models.Appointment, now: datetime | None = None) -> bool:
    """The visit took place (or is taking place): completed, the provider started it, or its time came."""
    import clinical

    if a.status == "completed":
        return True
    note = clinical.note_of(db, a)
    if note is not None and note.seen_at is not None:
        return True
    return appointments.as_utc(a.start_at) <= (now or _now())


def ensure_draft(db: Session, a: models.Appointment, by: str) -> tuple[models.VisitSummary, bool]:
    """The visit's current summary — a new draft (version 1) when there is none yet. Returns (row, created).
    Caller commits."""
    s = current_of(db, a)
    if s is not None:
        return s, False
    if a.status in ("canceled", "no_show"):
        raise HTTPException(
            status_code=409, detail=f"This visit is {a.status.replace('_', ' ')} — there is nothing to summarize."
        )
    s = models.VisitSummary(
        appointment_id=a.id,
        patient_id=a.patient_id,
        worker_id=a.provider_id,
        version=1,
        status=DRAFT,
        content=build_content(db, a).model_dump_json(),
        generated_at=_now(),
        generated_by=by,
    )
    db.add(s)
    db.flush()
    log.info("visit summary %s prepared for %s (%s)", s.number, a.number, by)
    return s, True


def notify_provider(db: Session, s: models.VisitSummary, a: models.Appointment) -> None:
    """Tell the visit's provider a draft waits for their signature — e-mail / text to the staff record, with
    the link to the chart; no medical details. The outcome is kept on the row. Never raises."""
    provider = db.get(models.Worker, a.provider_id)
    patient = db.get(models.Patient, a.patient_id)
    if provider is None:
        s.provider_notify_note = "No provider on the appointment — nobody to tell."
        return
    who = f"{patient.first_name} {patient.last_name}"
    link = assignments.chart_link(a)
    subject = f"Visit summary to sign: {who} ({patient.mrn}) — {a.number}"
    lines = [
        f"Hi {provider.first_name},",
        "",
        f"the visit summary for {who} ({patient.mrn}) is ready for your review and signature — the patient",
        "receives it once you have signed it.",
        "",
        f"  Visit:   {a.number} · {appointments.format_local(a.start_at)} ({appointments.office_tz()})",
        f"  Summary: {s.number} (draft)",
        "",
        "Open the patient's chart on the staff dashboard → Visit summary"
        + (f":\n  {link}" if link else " (My appointments → Open chart)."),
        "",
        "(No medical details are included in this message.)",
    ]
    sms = f"{config.OFFICE_NAME}: the visit summary for {a.number} ({patient.mrn}) is ready to sign — staff dashboard."
    sent, to, notes = appointments.deliver(
        provider.email, provider.phone, subject, "\n".join(lines), sms, whose="the staff record"
    )
    s.provider_notify_channels = ",".join(sent)
    s.provider_notified_to = ", ".join(to)
    s.provider_notify_note = ("; ".join(notes))[:500] or None
    s.provider_notified_at = _now() if sent else None


def on_appointment_status(db: Session, a: models.Appointment) -> models.VisitSummary | None:
    """The office marked the visit completed: prepare the draft and tell the provider. Never raises (the
    status change stands whatever happens here). Caller commits."""
    if a.status != "completed" or current_of(db, a) is not None:
        return None
    try:
        s, created = ensure_draft(db, a, "office")
    except HTTPException:
        return None
    if created:
        notify_provider(db, s, a)
    return s


def due(db: Session, now: datetime | None = None) -> list[models.Appointment]:
    """Visits that are over, that the provider saw (or the office completed), within the last
    PREPARE_WITHIN_DAYS days, without a summary yet."""
    now = now or _now()
    rows = (
        db.execute(
            select(models.Appointment)
            .outerjoin(models.VisitNote, models.VisitNote.appointment_id == models.Appointment.id)
            .outerjoin(models.VisitSummary, models.VisitSummary.appointment_id == models.Appointment.id)
            .where(
                models.Appointment.end_at <= now,
                models.Appointment.end_at >= now - timedelta(days=PREPARE_WITHIN_DAYS),
                models.VisitSummary.id.is_(None),
                (models.Appointment.status == "completed")
                | ((models.Appointment.status == "scheduled") & models.VisitNote.seen_at.is_not(None)),
            )
            .order_by(models.Appointment.end_at)
        )
        .scalars()
        .unique()
        .all()
    )
    return list(rows)


def prepare_due(db: Session, now: datetime | None = None) -> list[models.VisitSummary]:
    """The background job (reminders.run_once): a draft for every due visit, and the provider told."""
    out = []
    for a in due(db, now):
        s, created = ensure_draft(db, a, "job")
        if created:
            notify_provider(db, s, a)
            out.append(s)
    if out:
        db.commit()
        log.info("visit summaries prepared: %s", ", ".join(s.number for s in out))
    return out


# ---------------------------------------------------------------------------- editing, signing, amending


def sign_blocker(db: Session, a: models.Appointment, s: models.VisitSummary, w: models.Worker | None) -> str | None:
    """Why the provider cannot sign this summary right now — None when they can."""
    if s.status != DRAFT:
        return "This version is already signed." if s.status == SIGNED else "This version was replaced by an amendment."
    if not can_sign(w):
        return "Only a doctor or a physician assistant signs the visit summary."
    if a.status in ("canceled", "no_show"):
        return f"This visit is {a.status.replace('_', ' ')}."
    if not visit_started(db, a):
        return "The visit has not taken place yet."
    return None


def update(s: models.VisitSummary, body: schemas.VisitSummaryUpdate) -> None:
    if s.status != DRAFT:
        raise HTTPException(
            status_code=409, detail="A signed visit summary cannot be edited — write an amendment instead."
        )
    for key in ("assessment", "plan", "instructions", "follow_up"):
        if key in body.model_fields_set:
            setattr(s, key, getattr(body, key))
    s.updated_at = _now()


def _fingerprint(s: models.VisitSummary) -> str:
    c = content_of(s)
    payload = json.dumps(
        {"content": c.model_dump(mode="json"), "texts": _texts(s, c)}, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _verification(s: models.VisitSummary) -> str:
    msg = f"vs:{s.id}:{s.appointment_id}:{s.version}:{appointments.as_utc(s.signed_at).isoformat()}:{_fingerprint(s)}"
    return hmac.new(config.TOKEN_SECRET.encode("utf-8"), msg.encode(), hashlib.sha256).hexdigest()[:10].upper()


def sign(
    db: Session,
    a: models.Appointment,
    s: models.VisitSummary,
    w: models.Worker,
    actor: models.Account,
    body: schemas.VisitSummarySign,
) -> models.VisitSummary:
    """The doctor signs: the content is rebuilt one last time and frozen, the texts become final, the
    signature is recorded (esign.py) and a version it amends is superseded. Caller commits."""
    import esign

    problem = sign_blocker(db, a, s, w)
    if problem:
        raise HTTPException(status_code=403 if "Only a doctor" in problem else 409, detail=problem)
    refresh(db, s)
    c = content_of(s)
    texts = _texts(s, c)
    s.assessment, s.plan = texts["assessment"], texts["plan"]  # final: what the patient reads
    try:
        result, signed_at = esign.sign(
            body.signature.signer_name, body.signature.image_png, "visit_summary", f"{a.number}-v{s.version}"
        )
    except esign.NotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except esign.ESignError as exc:
        raise HTTPException(status_code=422, detail=f"Your signature: {exc}") from exc
    s.status = SIGNED
    s.signer_name = body.signature.signer_name
    s.signature_png = body.signature.image_png
    s.signed_at = signed_at
    s.signed_by = actor.id
    s.worker_id = w.id
    s.esign_reference, s.esign_provider = result.reference, result.provider
    db.flush()
    s.verification = _verification(s)
    for old in versions_of(db, a):
        if old.id != s.id and old.status == SIGNED:
            old.status, old.superseded_at, old.superseded_by_id = SUPERSEDED, signed_at, s.id
    db.flush()
    accounts.audit(
        db,
        actor,
        "visit_summary.sign",
        None,
        appointment=a.number,
        summary=s.number,
        version=s.version,
        esign=result.reference,
    )
    log.info("visit summary %s v%d signed by %s (%s)", s.number, s.version, w.staff_number, result.reference)
    if body.send_to_patient:
        send(db, s, actor)
    return s


def amend(
    db: Session, a: models.Appointment, w: models.Worker, actor: models.Account, reason: str
) -> models.VisitSummary:
    """A new version of the signed summary (a draft with its texts) — it supersedes the signed one once
    signed. Caller commits."""
    if not can_sign(w):
        raise HTTPException(status_code=403, detail="Only a doctor or a physician assistant signs the visit summary.")
    rows = versions_of(db, a)
    if any(r.status == DRAFT for r in rows):
        raise HTTPException(status_code=409, detail="An amendment is already being prepared — sign it or edit it.")
    signed = next((r for r in rows if r.status == SIGNED), None)
    if signed is None:
        raise HTTPException(status_code=409, detail="There is no signed visit summary to amend yet.")
    s = models.VisitSummary(
        appointment_id=a.id,
        patient_id=a.patient_id,
        worker_id=w.id,
        version=max(r.version for r in rows) + 1,
        status=DRAFT,
        content=build_content(db, a).model_dump_json(),
        generated_at=_now(),
        generated_by="provider",
        assessment=signed.assessment,
        plan=signed.plan,
        instructions=signed.instructions,
        follow_up=signed.follow_up,
        amendment_reason=reason,
    )
    db.add(s)
    db.flush()
    accounts.audit(
        db, actor, "visit_summary.amend", None, appointment=a.number, summary=signed.number, version=s.version
    )
    return s


# ---------------------------------------------------------------------------- the hand-over to the patient


def _office_line() -> str:
    return config.OFFICE_NAME + (f", {config.OFFICE_PHONE}" if config.OFFICE_PHONE else "")


def filename(s: models.VisitSummary) -> str:
    return f"{s.number}-v{s.version}.pdf"


def send(db: Session, s: models.VisitSummary, actor: models.Account | None) -> models.VisitSummary:
    """E-mail the signed summary to the patient (the PDF attached) and text them that it is ready — to what
    is on the patient record, on every channel the server can deliver on. Recorded on the row (channels,
    masked destinations, notes); a provider problem — or no address on file — becomes a note, never an
    error: the signature stands and the office can print it. ``409`` when it is not signed. Caller commits."""
    if s.status != SIGNED:
        raise HTTPException(status_code=409, detail="Only a signed visit summary can be sent to the patient.")
    a = db.get(models.Appointment, s.appointment_id)
    patient = db.get(models.Patient, s.patient_id)
    provider = db.get(models.Worker, s.worker_id) if s.worker_id else None
    local = appointments.as_utc(a.start_at).astimezone(appointments.office_tz())
    day = f"{local:%B} {local.day}, {local.year}"
    has_account = patient.account_id is not None
    subject = f"Your visit summary — {config.OFFICE_NAME}, {day}"
    body = "\n".join(
        [
            f"Hi {patient.first_name},",
            "",
            f"the summary of your visit with {_provider_name(provider)} on {day} ({a.number}) is attached as a PDF,",
            f"signed by {s.signer_name}. It lists what was found, the plan, the medications prescribed and",
            "your instructions and follow-up.",
            "",
            "You can also read it in your patient account: My appointments → Past → Visit summary."
            if has_account
            else "Keep it for your records — bring it to your next appointment or to a specialist.",
            "",
            f"Questions about it? {_office_line()}. If your symptoms get worse, contact the office; in an",
            "emergency call your local emergency number.",
            "",
            f"Visit summary {s.number} v{s.version} — verification {s.verification}.",
        ]
    )
    sms = (
        f"{config.OFFICE_NAME}: your visit summary for {day} ({a.number}) is ready — "
        + ("in your e-mail and your patient account." if has_account else "we sent it to your e-mail.")
        + " No medical details in this text."
    )
    attachment = messaging.Attachment(filename=filename(s), content_type="application/pdf", data=render_pdf(db, s))
    sent, to, notes = appointments.deliver_document(patient, subject, body, sms, attachment)
    s.sent_channels = ",".join(sent)
    s.sent_to = ", ".join(to)
    s.delivery_note = ("; ".join(notes))[:500] or None
    if sent:
        s.sent_at = _now()
    accounts.audit(
        db, actor, "visit_summary.send", None, summary=s.number, version=s.version, channels=sent or ["none"]
    )
    log.info("visit summary %s v%d sent to the patient by %s", s.number, s.version, ",".join(sent) or "nothing")
    return s


# ---------------------------------------------------------------------------- output


def _split(value: str | None) -> list[str]:
    return [x for x in (value or "").replace(", ", ",").split(",") if x]


def out(
    db: Session,
    s: models.VisitSummary,
    *,
    worker: models.Worker | None = None,
    patient_view: bool = False,
) -> schemas.VisitSummaryOut:
    """``worker``: the signed-in provider (staff) — ``can_sign`` is answered for them. ``patient_view``:
    leave out what is only the office's business (notifications, delivery notes, drafts)."""
    c = content_of(s)
    texts = _texts(s, c)
    a = db.get(models.Appointment, s.appointment_id)
    provider = db.get(models.Worker, s.worker_id) if s.worker_id else None
    blocker = sign_blocker(db, a, s, worker) if worker is not None else "Only the visit's provider signs it."
    previous = [r for r in versions_of(db, a) if r.id != s.id and r.status != DRAFT]
    return schemas.VisitSummaryOut(
        id=s.id,
        number=s.number,
        appointment_id=a.id,
        appointment_number=a.number,
        patient_id=s.patient_id,
        version=s.version,
        status=s.status,
        content=c,
        assessment=texts["assessment"],
        plan=texts["plan"],
        assessment_edited=s.status == DRAFT and s.assessment is not None,
        plan_edited=s.status == DRAFT and s.plan is not None,
        instructions=texts["instructions"],
        follow_up=texts["follow_up"],
        generated_at=s.generated_at,
        generated_by=s.generated_by,
        provider_name=_provider_name(provider) if provider else None,
        signer_name=s.signer_name,
        signed_at=s.signed_at,
        signature_png=s.signature_png,
        esign_reference=None if patient_view else s.esign_reference,
        verification=f"{s.number}-{s.verification}" if s.verification else None,
        amendment_reason=s.amendment_reason,
        superseded_at=s.superseded_at,
        provider_notified_at=None if patient_view else s.provider_notified_at,
        provider_notify_channels=[] if patient_view else _split(s.provider_notify_channels),
        sent_at=s.sent_at,
        sent_channels=_split(s.sent_channels),
        sent_to=[] if patient_view else [t for t in (s.sent_to or "").split(", ") if t],
        delivery_note=None if patient_view else s.delivery_note,
        patient_viewed_at=s.patient_viewed_at,
        can_sign=blocker is None,
        sign_blocker=blocker,
        previous_versions=[
            schemas.VisitSummaryVersion(
                id=r.id,
                number=r.number,
                version=r.version,
                status=r.status,
                signed_at=r.signed_at,
                signer_name=r.signer_name,
                superseded_at=r.superseded_at,
                verification=f"{r.number}-{r.verification}" if r.verification else None,
            )
            for r in previous
            if not patient_view or r.status in (SIGNED, SUPERSEDED)
        ],
        updated_at=s.updated_at,
    )


# ---------------------------------------------------------------------------- the PDF

FOOTER_TOP = 72


def render_pdf(db: Session, s: models.VisitSummary) -> bytes:
    """The visit summary as a Letter-size PDF: the visit, the sections, the doctor's signature (the drawn
    image, the time, the verification code) — or DRAFT / SUPERSEDED where that applies."""
    import referrals  # the shared footer (page n of total + the SAMPLE banner)

    c = content_of(s)
    texts = _texts(s, c)
    tz = appointments.office_tz()
    doc = pdf.PdfDocument(title=f"Visit summary {s.number} v{s.version}", author=config.OFFICE_NAME, margin=FOOTER_TOP)
    left, right, width = 54, 558, 504
    col2 = 310
    col1_width, col2_width = col2 - left - 14, right - col2

    def header() -> float:
        doc.text(left, 750, f"{c.office_name} · visit summary {c.visit_number} — {c.patient_name} (continued)", size=9)
        return 726

    def room(y: float, needed: float) -> float:
        if y - needed < FOOTER_TOP:
            doc.new_page()
            return header()
        return y

    y = 738
    doc.text(left, y, c.office_name, size=18, bold=True)
    doc.text_right(right, y, "VISIT SUMMARY", size=14, bold=True)
    y -= 16
    office = " · ".join(x for x in (c.office_address, c.office_phone and f"Phone {c.office_phone}") if x)
    if office:
        doc.text(left, y, office, size=9, gray=0.35)
    doc.text_right(right, y, f"{s.number} · version {s.version}", size=10, bold=True)
    y -= 12
    doc.line(left, y, right, y, width=1.2)
    y -= 20

    doc.text(left, y, "PATIENT", size=8, bold=True, gray=0.4)
    doc.text(col2, y, "VISIT", size=8, bold=True, gray=0.4)
    y -= 14
    patient_lines = [
        (c.patient_name, True),
        (f"Date of birth: {c.patient_date_of_birth:%m/%d/%Y}", False),
        (f"Record number: {c.patient_mrn}", False),
    ]
    visit_lines = [
        (c.visit_date, True),
        (f"Time zone: {c.timezone} · {c.visit_number}", False),
        (f"{c.provider_name} · {c.provider_role}{f' — {c.provider_specialty}' if c.provider_specialty else ''}", False),
    ]
    y = doc.columns(y, (left, col1_width, patient_lines), (col2, col2_width, visit_lines))
    y -= 2
    doc.line(left, y, right, y)
    y -= 20

    def section(title: str, body: str | list[str] | None, empty: str | None = None) -> None:
        nonlocal y
        if not body and not empty:
            return
        y = room(y, 40)
        doc.text(left, y, title.upper(), size=8.5, bold=True, gray=0.35)
        y -= 14
        if not body:
            paragraphs = [empty]
        elif isinstance(body, list):
            paragraphs = [f"• {x}" for x in body]
        else:
            paragraphs = body.split("\n")
        for para in paragraphs:
            if not para.strip():
                y -= 5
                continue
            y = doc.paragraph(left, y, para, width=width, size=10, on_new_page=header)
        y -= 8

    section("Why you came", c.reason)
    vitals = c.vitals + (f" ({c.vitals_note})" if c.vitals_note else "") if c.vitals else None
    section("Vital signs", vitals)
    section("Diagnoses", c.diagnoses)
    section("Assessment", texts["assessment"])
    section("Plan", texts["plan"])
    section("Medications prescribed at this visit", c.prescriptions)
    section("Your medications", c.medications, "None recorded.")
    section("Allergies", c.allergies, "No known allergies recorded.")
    section("Referrals", c.referrals)
    section("Tests", c.tests)
    section("Your instructions", texts["instructions"])
    section("Follow-up", texts["follow_up"])
    y = room(y, 30)
    y = doc.paragraph(
        left,
        y,
        f"Questions about this summary? Contact {_office_line()}. If your symptoms get worse, contact the "
        "office; in an emergency call your local emergency number.",
        width=width,
        size=9,
    )
    y -= 10

    # The signature.
    y = room(y, 110)
    doc.line(left, y, right, y)
    y -= 16
    if s.status == DRAFT:
        doc.text(left, y - 8, "DRAFT — NOT SIGNED YET", size=22, bold=True)
        doc.paragraph(
            left,
            y - 26,
            f"Prepared {appointments.format_local(s.generated_at)} ({tz}) for review by {c.provider_name}. "
            "The patient receives the summary once it is signed.",
            width=width,
            size=9.5,
        )
    else:
        signed = appointments.as_utc(s.signed_at).astimezone(tz)
        image = None
        if s.signature_png:
            try:
                image = pdf.decode_png(base64.b64decode(s.signature_png.split(",", 1)[1]))
            except (ValueError, IndexError):
                image = None
        if image is not None:
            doc.image(left, y - 52, 170, 50, image)
            y -= 58
        signer = s.signer_name or c.provider_name
        if signer.lower() not in c.provider_name.lower():  # typed differently from the roster: show both
            signer = f"{signer} ({c.provider_name})"
        elif signer != c.provider_name:
            signer = c.provider_name  # "Alex Example" typed → "Dr. Alex Example"
        doc.text(left, y, f"Electronically signed by {signer}", size=10, bold=True)
        doc.text(left, y - 13, f"on {signed:%B} {signed.day}, {signed.year} at {signed:%H:%M} ({tz})", size=9.5)
        doc.text_right(right, y, f"Verification: {s.number}-{s.verification or ''}", size=9.5)
        if s.esign_reference:
            doc.text_right(right, y - 13, f"E-signature: {s.esign_reference}", size=8.5)
        y -= 32
        if s.amendment_reason:
            y = doc.paragraph(
                left, y, f"Amended version {s.version}: {s.amendment_reason}", width=width, size=9, on_new_page=header
            )
        if s.status == SUPERSEDED:
            y = room(y, 40)
            replaced = appointments.as_utc(s.superseded_at).astimezone(tz) if s.superseded_at else None
            doc.text(left, y - 8, "SUPERSEDED — SEE THE AMENDED VERSION", size=18, bold=True)
            if replaced:
                doc.text(left, y - 24, f"Replaced on {replaced:%B} {replaced.day}, {replaced.year}", size=9.5)
    doc.on_each_page(
        lambda n, total: referrals.footer(
            doc,
            n,
            total,
            f"Visit summary {s.number} v{s.version}",
            "SAMPLE — dummy data (development): not a real medical record",
        )
    )
    return doc.output()


def _pdf_response(s: models.VisitSummary, data: bytes) -> Response:
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename(s)}"', "Cache-Control": "private, no-store"},
    )


# ---------------------------------------------------------------------------- routes: the provider


def _my_visit(db: Session, s: staff_accounts.StaffPrincipal, appointment_id: int) -> models.Appointment:
    a = db.get(models.Appointment, appointment_id)
    if a is None or a.provider_id != s.worker.id:
        raise HTTPException(status_code=404, detail="Appointment not found.")
    return a


def _staff_current(db: Session, a: models.Appointment) -> models.VisitSummary:
    s = current_of(db, a)
    if s is None:
        raise HTTPException(status_code=404, detail="There is no visit summary for this visit yet.")
    return s


@staff_router.get("/{appointment_id}/visit-summary", response_model=schemas.VisitSummaryOut)
def staff_get_summary(
    appointment_id: int,
    st: staff_accounts.StaffPrincipal = Depends(staff_accounts.staff_principal),
    db: Session = Depends(get_db),
):
    """The visit's summary — the draft is prepared here the first time (once the visit has started) and
    follows the chart until it is signed."""
    a = _my_visit(db, st, appointment_id)
    s = current_of(db, a)
    if s is None:
        if not visit_started(db, a):
            raise HTTPException(
                status_code=409,
                detail="The visit summary is prepared once the visit has started (Start visit on the chart).",
            )
        s, _ = ensure_draft(db, a, "provider")
    refresh(db, s)
    db.commit()
    return out(db, s, worker=st.worker)


@staff_router.put("/{appointment_id}/visit-summary", response_model=schemas.VisitSummaryOut)
def staff_update_summary(
    appointment_id: int,
    body: schemas.VisitSummaryUpdate,
    st: staff_accounts.StaffPrincipal = Depends(staff_accounts.staff_principal),
    db: Session = Depends(get_db),
):
    """Edit the draft: the assessment and the plan for the patient (empty = the visit note's), the
    instructions, the follow-up."""
    a = _my_visit(db, st, appointment_id)
    s = _staff_current(db, a)
    update(s, body)
    refresh(db, s)
    db.commit()
    return out(db, s, worker=st.worker)


@staff_router.post("/{appointment_id}/visit-summary/sign", response_model=schemas.VisitSummaryOut)
def staff_sign_summary(
    appointment_id: int,
    body: schemas.VisitSummarySign,
    st: staff_accounts.StaffPrincipal = Depends(staff_accounts.staff_principal),
    db: Session = Depends(get_db),
):
    """Sign the summary (the doctor): it is frozen, and — unless asked not to — sent to the patient."""
    a = _my_visit(db, st, appointment_id)
    s = _staff_current(db, a)
    sign(db, a, s, st.worker, st.principal.account, body)
    db.commit()
    return out(db, s, worker=st.worker)


@staff_router.post("/{appointment_id}/visit-summary/amend", response_model=schemas.VisitSummaryOut)
def staff_amend_summary(
    appointment_id: int,
    body: schemas.VisitSummaryAmend,
    st: staff_accounts.StaffPrincipal = Depends(staff_accounts.staff_principal),
    db: Session = Depends(get_db),
):
    """A signed summary has a mistake: a new version to edit and sign (it supersedes the signed one)."""
    a = _my_visit(db, st, appointment_id)
    s = amend(db, a, st.worker, st.principal.account, body.reason)
    db.commit()
    return out(db, s, worker=st.worker)


@staff_router.post("/{appointment_id}/visit-summary/send", response_model=schemas.VisitSummaryOut)
def staff_send_summary(
    appointment_id: int,
    st: staff_accounts.StaffPrincipal = Depends(staff_accounts.staff_principal),
    db: Session = Depends(get_db),
):
    """E-mail / text the signed summary to the patient again."""
    a = _my_visit(db, st, appointment_id)
    s = signed_of(db, a)
    if s is None:
        raise HTTPException(status_code=409, detail="Only a signed visit summary can be sent to the patient.")
    send(db, s, st.principal.account)
    db.commit()
    return out(db, s, worker=st.worker)


@staff_router.get(
    "/{appointment_id}/visit-summary/pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}, "description": "The visit summary"}},
)
def staff_summary_pdf(
    appointment_id: int,
    version: int | None = Query(default=None, ge=1),
    st: staff_accounts.StaffPrincipal = Depends(staff_accounts.staff_principal),
    db: Session = Depends(get_db),
):
    a = _my_visit(db, st, appointment_id)
    s = _pick(db, a, version) if version else _staff_current(db, a)
    refresh(db, s)
    data = render_pdf(db, s)
    accounts.audit(db, st.principal.account, "visit_summary.pdf", None, summary=s.number, version=s.version)
    db.commit()
    return _pdf_response(s, data)


def _pick(db: Session, a: models.Appointment, version: int) -> models.VisitSummary:
    s = next((r for r in versions_of(db, a) if r.version == version), None)
    if s is None:
        raise HTTPException(status_code=404, detail="No such version of the visit summary.")
    return s


# ---------------------------------------------------------------------------- routes: the patient


def _patients_visit(db: Session, p: auth.Principal, appointment_id: int) -> models.Appointment:
    rows = appointments.my_records(db, p.account)
    a = db.get(models.Appointment, appointment_id)
    if a is None or a.patient_id not in {r.id for r in rows}:
        raise HTTPException(status_code=404, detail="Appointment not found.")
    return a


def _patients_summary(db: Session, a: models.Appointment) -> models.VisitSummary:
    s = signed_of(db, a)
    if s is None:
        raise HTTPException(
            status_code=404,
            detail="There is no visit summary for this visit yet — it is sent to you once your doctor has signed it.",
        )
    return s


@me_router.get("/{appointment_id}/visit-summary", response_model=schemas.VisitSummaryOut)
def my_visit_summary(
    appointment_id: int, p: auth.Principal = Depends(auth.patient_principal), db: Session = Depends(get_db)
):
    """The signed summary of one of the account's visits (the first opening is recorded)."""
    a = _patients_visit(db, p, appointment_id)
    s = _patients_summary(db, a)
    if s.patient_viewed_at is None:
        s.patient_viewed_at = _now()
        accounts.audit(db, p.account, "visit_summary.view", p.account, summary=s.number, version=s.version)
    db.commit()
    return out(db, s, patient_view=True)


@me_router.get(
    "/{appointment_id}/visit-summary/pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}, "description": "The visit summary"}},
)
def my_visit_summary_pdf(
    appointment_id: int, p: auth.Principal = Depends(auth.patient_principal), db: Session = Depends(get_db)
):
    a = _patients_visit(db, p, appointment_id)
    s = _patients_summary(db, a)
    if s.patient_viewed_at is None:
        s.patient_viewed_at = _now()
    data = render_pdf(db, s)
    db.commit()
    return _pdf_response(s, data)


# ---------------------------------------------------------------------------- routes: the office


def _appointment(db: Session, appointment_id: int) -> models.Appointment:
    a = db.get(models.Appointment, appointment_id)
    if a is None:
        raise HTTPException(status_code=404, detail="Appointment not found.")
    return a


@admin_router.post("/visit-summaries/run", response_model=schemas.VisitSummaryRun)
def run_prepare(
    as_of: datetime | None = Query(default=None, description="Outside prod: pretend it is this moment"),
    p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    """Prepare the drafts of the visits that are over now (what the background job does every minute)."""
    if as_of is not None and config.IS_PROD:
        raise HTTPException(status_code=400, detail="as_of is not available in production.")
    now = appointments.as_utc(as_of) if as_of else _now()
    rows = prepare_due(db, now)
    accounts.audit(db, p.account, "visit_summary.run", None, prepared=len(rows))
    db.commit()
    return schemas.VisitSummaryRun(
        as_of=now,
        prepared=len(rows),
        appointments=[db.get(models.Appointment, s.appointment_id).number for s in rows],
    )


@admin_router.get("/{appointment_id}/visit-summary", response_model=schemas.VisitSummaryOut)
def office_visit_summary(
    appointment_id: int, _p: auth.Principal = Depends(auth.admin_principal), db: Session = Depends(get_db)
):
    """Where the visit's summary stands: the draft waiting for the doctor, or the signed version (+ history)."""
    a = _appointment(db, appointment_id)
    s = current_of(db, a)
    if s is None:
        raise HTTPException(status_code=404, detail="There is no visit summary for this visit yet.")
    refresh(db, s)
    db.commit()
    return out(db, s)


@admin_router.get(
    "/{appointment_id}/visit-summary/pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}, "description": "The visit summary"}},
)
def office_visit_summary_pdf(
    appointment_id: int,
    version: int | None = Query(default=None, ge=1),
    p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    """Print the summary for the patient (a draft prints as DRAFT). Recorded."""
    a = _appointment(db, appointment_id)
    s = _pick(db, a, version) if version else current_of(db, a)
    if s is None:
        raise HTTPException(status_code=404, detail="There is no visit summary for this visit yet.")
    refresh(db, s)
    data = render_pdf(db, s)
    accounts.audit(db, p.account, "visit_summary.pdf", None, summary=s.number, version=s.version)
    db.commit()
    return _pdf_response(s, data)


@admin_router.post("/{appointment_id}/visit-summary/send", response_model=schemas.VisitSummaryOut)
def office_send_visit_summary(
    appointment_id: int, p: auth.Principal = Depends(auth.admin_principal), db: Session = Depends(get_db)
):
    """Send the signed summary to the patient again (e-mail with the PDF / a text that it is ready)."""
    a = _appointment(db, appointment_id)
    s = signed_of(db, a)
    if s is None:
        raise HTTPException(status_code=409, detail="Only a signed visit summary can be sent to the patient.")
    send(db, s, p.account)
    db.commit()
    return out(db, s)
