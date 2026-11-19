"""Patient reports — on request the office prepares the **immunization record** or the **annual health
examination report** of a patient, and a **doctor signs** it.

How a report comes about:

    requested   the patient asks in the app (My reports → Request a report: which one, what for — school, work,
                sports, travel, camp, … — by when), or the office records a request made at the desk / on the
                phone. A patient request tells the office (OFFICE_NOTIFY_EMAIL / OFFICE_NOTIFY_PHONE; the queue on
                the office dashboard shows it either way).
    draft       the office prepares it from the record (``generate``): it chooses the doctor who signs and — for
                the annual exam — the exam visit it is based on (suggested: the patient's latest annual / physical
                exam). The doctor is told it waits for them (e-mail / text to the staff record, no medical
                details). Until it is signed the generated part follows the record.
    signed      the doctor completes their part on the staff dashboard (Reports) — the annual exam: height,
                vision, hearing, the examination by system (normal / abnormal + what / not examined), the fitness
                decision for the purpose (+ restrictions), recommendations, the next exam; the immunization
                record: contraindications / exemptions and the vaccines due — and signs it (typed name + drawn
                signature + consent, esign.py; a verification code on the PDF). Only a doctor signs.
    handed      to the patient: e-mailed with the PDF and a text that it is ready, and in the app (My reports);
                the office can print it or send it again.
    declined    the doctor can send it back with the reason (e.g. "no exam within the last year — please book
                one"): the office is told, prepares it again (another visit / doctor) or cancels it.
    canceled    by the office (the patient is told, with the reason) or by the patient while it is open.
    amended     a signed report is never edited: the doctor writes an amendment — a new version that supersedes
                it once signed.

Routes

    The office (administrators):
    GET  /api/v1/patient-reports?status=open|requested|draft|declined|signed|canceled&kind=&q=   the queue
    GET  /api/v1/patient-reports/{id}                     one report (the draft follows the record)
    POST /api/v1/patient-reports/{id}/generate            prepare it {worker_id, appointment_id}
    POST /api/v1/patient-reports/{id}/cancel              {reason}
    POST /api/v1/patient-reports/{id}/send                e-mail / text the signed report to the patient again
    GET  /api/v1/patient-reports/{id}/pdf                 print it (a draft prints as DRAFT)
    GET  /api/v1/patients/{id}/reports                    the patient's reports
    GET  /api/v1/patients/{id}/reports/options            the doctors and the patient's visits to choose from
    POST /api/v1/patients/{id}/reports                    record a request (and prepare it at once with worker_id)

    The doctor (staff dashboard → Reports):
    GET  /api/v1/staff/me/reports?status=todo|signed|all  the reports assigned to them
    GET/PUT /api/v1/staff/me/reports/{id}                 the report / their part {findings, doctor_note}
    POST /api/v1/staff/me/reports/{id}/sign | decline | amend | send,  GET …/pdf

    The patient (signed in — the patients' app → My reports):
    GET  /api/v1/me/reports                               the reports of the account's records
    POST /api/v1/me/reports                               request one {patient_id, kind, purpose, …}
    GET  /api/v1/me/reports/{id}, …/pdf                   a signed report (others: where it stands)
    POST /api/v1/me/reports/{id}/cancel                   withdraw an open request

Every request, preparation, signature, decline, cancellation, hand-over and PDF lands in the audit log.
Outside production every PDF carries the "SAMPLE" banner: the data is dummy data.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

import accounts
import appointments
import assignments
import assistant_review
import auth
import config
import health_records
import immunizations
import messaging
import models
import patients
import pdf
import schemas
import staff_accounts
from database import get_db

log = logging.getLogger("account.patient_reports")

admin_router = APIRouter(tags=["patient reports"])
staff_router = APIRouter(prefix="/staff/me/reports", tags=["staff"])
me_router = APIRouter(prefix="/me/reports", tags=["me"])

KIND_LABELS = {"immunization": "Immunization record", "annual_exam": "Annual health examination report"}
KIND_TITLES = {"immunization": "IMMUNIZATION RECORD", "annual_exam": "ANNUAL HEALTH EXAMINATION"}
STATUS_LABELS = {
    "requested": "Requested — the office prepares it",
    "draft": "Prepared — waiting for the doctor's signature",
    "declined": "Returned by the doctor",
    "signed": "Signed",
    "canceled": "Canceled",
    "superseded": "Replaced by an amended version",
}
# What the patient reads (drafts and returns are "in progress" for them).
PATIENT_STATUS_LABELS = {
    "requested": "Requested — the office is preparing it",
    "draft": "In progress — waiting for the doctor's signature",
    "declined": "In progress — the office is looking into it",
    "signed": "Ready — signed by the doctor",
    "canceled": "Canceled",
    "superseded": "Replaced by an amended version",
}
PURPOSE_LABELS = {
    "school": "School",
    "work": "Work / employment",
    "sports": "Sports",
    "travel": "Travel",
    "camp": "Camp",
    "personal": "Personal records",
    "other": "Other",
}
EXAM_SYSTEMS: tuple[tuple[str, str], ...] = (
    ("general", "General appearance"),
    ("heent", "Head, eyes, ears, nose, throat"),
    ("neck", "Neck and thyroid"),
    ("cardiovascular", "Heart and circulation"),
    ("respiratory", "Lungs and breathing"),
    ("abdomen", "Abdomen"),
    ("musculoskeletal", "Muscles, bones and joints"),
    ("neurological", "Nervous system"),
    ("skin", "Skin"),
)
FITNESS_LABELS = {
    "fit": "Fit for the stated purpose without restrictions.",
    "fit_with_restrictions": "Fit for the stated purpose with the restrictions below.",
    "not_fit": "Not fit for the stated purpose at this time.",
    "further_evaluation": "Further evaluation is needed before a decision can be made.",
}
FINDING_LABELS = {"normal": "Normal", "abnormal": "Abnormal", "not_examined": "Not examined"}
MAX_OPEN_PER_PATIENT = 5
EXAM_MAX_AGE_DAYS = 365  # older exam visits are flagged ("more than a year ago")
RESULTS_WITHIN_DAYS = 365
_EXAM_REASON = re.compile(r"annual|physical|check-?up|wellness|exam", re.IGNORECASE)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _today() -> date:
    return _now().astimezone(appointments.office_tz()).date()


def can_sign(w: models.Worker | None) -> bool:
    return w is not None and w.active and w.role in models.PATIENT_REPORT_SIGNER_ROLES


def purpose_label(r: models.PatientReport) -> str:
    label = PURPOSE_LABELS.get(r.purpose, r.purpose)
    return f"{label} — {r.purpose_detail}" if r.purpose_detail else label


# ---------------------------------------------------------------------------- choices for the office


def doctors(db: Session) -> list[models.Worker]:
    return (
        db.execute(
            select(models.Worker)
            .where(models.Worker.active.is_(True), models.Worker.role.in_(models.PATIENT_REPORT_SIGNER_ROLES))
            .order_by(models.Worker.last_name_norm, models.Worker.first_name_norm)
        )
        .scalars()
        .all()
    )


def past_visits(db: Session, patient: models.Patient) -> list[models.Appointment]:
    """The visits that took place: completed, or scheduled and over — newest first."""
    now = _now()
    return (
        db.execute(
            select(models.Appointment)
            .where(
                models.Appointment.patient_id == patient.id,
                or_(
                    models.Appointment.status == "completed",
                    (models.Appointment.status == "scheduled") & (models.Appointment.end_at <= now),
                ),
            )
            .order_by(models.Appointment.start_at.desc())
        )
        .scalars()
        .all()
    )


def suggested_visit(visits: list[models.Appointment]) -> models.Appointment | None:
    """The latest annual / physical exam, else the latest completed visit, else the latest one."""
    for pick in (
        lambda a: a.status == "completed" and bool(_EXAM_REASON.search(a.reason or "")),
        lambda a: a.status == "completed",
        lambda a: True,
    ):
        found = next((a for a in visits if pick(a)), None)
        if found is not None:
            return found
    return None


def suggested_doctor(db: Session, visits: list[models.Appointment], visit: models.Appointment | None) -> int | None:
    for a in ([visit] if visit else []) + visits:
        w = db.get(models.Worker, a.provider_id)
        if can_sign(w):
            return w.id
    everyone = doctors(db)
    return everyone[0].id if everyone else None


def options(db: Session, patient: models.Patient) -> schemas.ReportOptions:
    visits = past_visits(db, patient)
    visit = suggested_visit(visits)
    return schemas.ReportOptions(
        doctors=[
            schemas.ReportDoctor(
                id=w.id, staff_number=w.staff_number, name=appointments._provider_name(w), specialty=w.specialty
            )
            for w in doctors(db)
        ],
        visits=[
            schemas.ReportVisit(
                id=a.id,
                number=a.number,
                start_at=appointments.as_utc(a.start_at),
                start_local=appointments.format_local(a.start_at),
                provider_name=appointments._provider_name(db.get(models.Worker, a.provider_id)),
                reason=a.reason,
                status=a.status,
            )
            for a in visits
        ],
        suggested_worker_id=suggested_doctor(db, visits, visit),
        suggested_appointment_id=visit.id if visit else None,
    )


# ---------------------------------------------------------------------------- the generated part


def _items(db: Session, patient: models.Patient, kind: str) -> list[str]:
    return [
        f"{e.name}{f' — {e.detail}' if e.detail else ''}"
        for e in health_records.items_of(db, patient, kind, active_only=True)
    ]


def _result_lines(db: Session, patient: models.Patient) -> list[str]:
    import clinical

    since = _today() - timedelta(days=RESULTS_WITHIN_DAYS)
    out = []
    for r in clinical.results_of(db, patient):
        when = r.resulted_on or r.ordered_on
        if when is None or when < since:
            continue
        values = clinical._values(r)
        flagged = [v for v in values if v.flag]
        if r.kind == "imaging":
            detail = r.summary or "report on file"
        elif flagged:
            detail = "outside the reference range: " + "; ".join(
                f"{v.name} {v.value}{f' {v.unit}' if v.unit else ''}" for v in flagged
            )
        else:
            detail = "within the reference ranges" if values else (r.summary or "on file")
        out.append(f"{r.name} ({when:%m/%d/%Y}): {detail}")
    return out


def build_content(db: Session, r: models.PatientReport) -> schemas.ReportContent:
    """The generated part, from the record as it is now."""
    import clinical

    patient = db.get(models.Patient, r.patient_id)
    doctor = db.get(models.Worker, r.worker_id) if r.worker_id else None
    shots = immunizations.report_rows(db, patient)
    warnings: list[str] = []
    c = schemas.ReportContent(
        kind=r.kind,
        title=KIND_LABELS[r.kind],
        office_name=config.OFFICE_NAME,
        office_address=config.OFFICE_ADDRESS or None,
        office_phone=config.OFFICE_PHONE or None,
        patient_name=f"{patient.first_name} {patient.last_name}",
        patient_mrn=patient.mrn,
        patient_date_of_birth=patient.date_of_birth,
        patient_sex=patient.sex if patient.sex and patient.sex != "unspecified" else None,
        purpose=r.purpose,
        purpose_label=purpose_label(r),
        generated_for=appointments._provider_name(doctor) if doctor else None,
        allergies=_items(db, patient, "allergy"),
        immunizations=shots,
    )
    if r.kind == "immunization":
        if not shots:
            warnings.append("No immunizations are on record — enter them on the patient's page first.")
        c.warnings = warnings
        return c

    a = db.get(models.Appointment, r.appointment_id) if r.appointment_id else None
    if a is None:
        warnings.append("No exam visit is chosen — the doctor documents the examination on the report.")
    else:
        provider = db.get(models.Worker, a.provider_id)
        task = clinical._completed_task(db, a) or assignments.current_for(db, a)
        vitals = assistant_review.vitals_out(assistant_review.vitals_of(task)) if task else None
        note = clinical.note_of(db, a)
        import prescriptions

        c.exam_visit_number = a.number
        c.exam_date = appointments.format_local(a.start_at)
        c.exam_start_at = appointments.as_utc(a.start_at)
        c.exam_provider = appointments._provider_name(provider) if provider else None
        if vitals is not None:
            c.vitals = vitals.text or None
            if vitals.bp_systolic is not None and vitals.bp_diastolic is not None:
                c.blood_pressure = f"{vitals.bp_systolic}/{vitals.bp_diastolic} mmHg"
            c.pulse = f"{vitals.pulse}/min" if vitals.pulse is not None else None
            c.temperature = (
                f"{vitals.temperature:g} °{vitals.temperature_unit}" if vitals.temperature is not None else None
            )
            c.weight = f"{vitals.weight:g} {vitals.weight_unit}" if vitals.weight is not None else None
        else:
            warnings.append("No vital signs were recorded at the exam visit.")
        c.visit_assessment = note.assessment if note else None
        c.visit_plan = note.plan if note else None
        c.visit_diagnoses = [
            f"{d.description}{f' ({d.code})' if d.code else ''}" for d in prescriptions.diagnoses_of(db, a)
        ]
        age = (_today() - appointments.as_utc(a.start_at).astimezone(appointments.office_tz()).date()).days
        if age > EXAM_MAX_AGE_DAYS:
            warnings.append(f"The exam visit was {age // 30} months ago — more than a year.")
        if a.status != "completed":
            warnings.append(f"The exam visit {a.number} is not marked completed.")
    c.conditions = _items(db, patient, "condition")
    c.surgeries = _items(db, patient, "surgery")
    c.family_history = _items(db, patient, "family_history")
    c.social_history = _items(db, patient, "social_history")
    c.medications = _items(db, patient, "medication")
    c.recent_results = _result_lines(db, patient)
    c.warnings = warnings
    return c


def content_of(r: models.PatientReport) -> schemas.ReportContent | None:
    return schemas.ReportContent.model_validate_json(r.content) if r.content and r.content != "{}" else None


def findings_of(r: models.PatientReport) -> schemas.ReportFindings:
    return schemas.ReportFindings.model_validate_json(r.findings or "{}")


def refresh(db: Session, r: models.PatientReport) -> None:
    """A draft follows the record (a signed report never changes)."""
    if r.status not in ("draft", "declined") or r.generated_at is None:
        return
    fresh = build_content(db, r).model_dump_json()
    if fresh != r.content:
        r.content = fresh
        r.generated_at = _now()


# ---------------------------------------------------------------------------- requests


def _open_reports(db: Session, patient: models.Patient) -> list[models.PatientReport]:
    return (
        db.execute(
            select(models.PatientReport).where(
                models.PatientReport.patient_id == patient.id,
                models.PatientReport.status.in_(models.PATIENT_REPORT_OPEN),
            )
        )
        .scalars()
        .all()
    )


def create_request(
    db: Session,
    patient: models.Patient,
    *,
    kind: str,
    purpose: str,
    purpose_detail: str | None,
    needed_by: date | None,
    note: str | None,
    via: str,
    actor: models.Account | None,
) -> models.PatientReport:
    """A new request. ``409`` when the same kind is already open for the patient, or too many are open.
    Caller commits."""
    open_rows = _open_reports(db, patient)
    same = next((x for x in open_rows if x.kind == kind), None)
    if same is not None:
        raise HTTPException(
            status_code=409,
            detail=f"An {KIND_LABELS[kind].lower()} for {patient.first_name} is already in progress ({same.number}).",
        )
    if len(open_rows) >= MAX_OPEN_PER_PATIENT:
        raise HTTPException(status_code=409, detail="Too many open report requests — please wait for them first.")
    r = models.PatientReport(
        patient_id=patient.id,
        kind=kind,
        status="requested",
        purpose=purpose,
        purpose_detail=purpose_detail,
        needed_by=needed_by,
        request_note=note,
        requested_via=via,
        requested_by=actor.id if actor else None,
        requested_at=_now(),
    )
    db.add(r)
    db.flush()
    accounts.audit(db, actor, "patient.report.request", None, patient=patient.mrn, report=r.number, kind=kind, via=via)
    log.info("%s requested (%s) for %s via %s", r.number, kind, patient.mrn, via)
    return r


def _admin_link(path: str) -> str | None:
    admin = (config.ADMIN_DASHBOARD_URL or "").strip()
    if not admin:
        return None
    base = appointments.app_base(None).rstrip("/") + admin if admin.startswith("/") else admin
    return f"{base.rstrip('/')}/#/{path}"


def _staff_link(r: models.PatientReport) -> str | None:
    staff = (config.STAFF_URL or "").strip()
    if not staff:
        return None
    base = appointments.app_base(None).rstrip("/") + staff if staff.startswith("/") else staff
    return f"{base.rstrip('/')}/#/reports/{r.id}"


def notify_office(db: Session, r: models.PatientReport, what: str) -> None:
    """Tell the office (OFFICE_NOTIFY_EMAIL / OFFICE_NOTIFY_PHONE) — a new patient request, a report a doctor
    returned. No medical details. The outcome is kept on the row; nothing configured = the queue only."""
    if not (config.OFFICE_NOTIFY_EMAIL or config.OFFICE_NOTIFY_PHONE):
        r.office_notify_note = "No office address / number configured — the office dashboard's queue shows it."
        return
    patient = db.get(models.Patient, r.patient_id)
    who = f"{patient.first_name} {patient.last_name} ({patient.mrn})"
    if what == "declined":
        subject = f"Returned by the doctor: {KIND_LABELS[r.kind]} {r.number} — {who}"
        lines = [f"The doctor returned {r.number} ({KIND_LABELS[r.kind].lower()} for {who}):", f"  {r.decline_reason}"]
    else:
        subject = f"New report request {r.number}: {KIND_LABELS[r.kind]} — {who}"
        lines = [
            f"{who} asked for the {KIND_LABELS[r.kind].lower()} in the patients' app.",
            f"  For:       {purpose_label(r)}",
            *([f"  Needed by: {r.needed_by:%b %d, %Y}"] if r.needed_by else []),
        ]
    link = _admin_link("reports")
    lines += [
        "",
        "Prepare it and choose the doctor who signs on the office dashboard → Reports"
        + (f":\n  {link}" if link else "."),
    ]
    what_ = "returned" if what == "declined" else "new request"
    sms = f"{config.OFFICE_NAME}: {what_} {r.number} ({KIND_LABELS[r.kind].lower()}) for {patient.mrn}."
    sent, to, notes = appointments.deliver(
        config.OFFICE_NOTIFY_EMAIL or None,
        config.OFFICE_NOTIFY_PHONE or None,
        subject,
        "\n".join(lines),
        sms,
        whose="the office settings",
    )
    r.office_notify_note = (f"told {', '.join(to)}" if sent else "; ".join(notes))[:500] or None


# ---------------------------------------------------------------------------- preparing (the office)


def generate(
    db: Session, r: models.PatientReport, worker_id: int, appointment_id: int | None, actor: models.Account
) -> models.PatientReport:
    """Prepare the report from the record for ``worker_id`` (a doctor) to sign — a requested one, a draft
    (another doctor / visit) or one the doctor returned. Caller commits."""
    if r.status not in models.PATIENT_REPORT_OPEN:
        raise HTTPException(status_code=409, detail=f"This report is {r.status} — it cannot be prepared again.")
    w = db.get(models.Worker, worker_id)
    if not can_sign(w):
        raise HTTPException(status_code=422, detail="worker_id: choose a doctor — only a doctor signs these reports.")
    a = None
    if appointment_id is not None:
        a = db.get(models.Appointment, appointment_id)
        if a is None or a.patient_id != r.patient_id:
            raise HTTPException(status_code=422, detail="appointment_id: not a visit of this patient")
        if a.status in ("canceled", "no_show"):
            raise HTTPException(status_code=422, detail=f"appointment_id: that visit is {a.status.replace('_', ' ')}")
    if r.kind == "immunization":
        a = None
    tell = r.status != "draft" or r.worker_id != w.id
    r.worker_id = w.id
    r.appointment_id = a.id if a else None
    r.status = "draft"
    r.decline_reason = r.declined_at = None
    r.generated_by = actor.id
    r.content = build_content(db, r).model_dump_json()
    r.generated_at = _now()
    db.flush()
    accounts.audit(
        db,
        actor,
        "patient.report.generate",
        None,
        report=r.number,
        doctor=w.staff_number,
        visit=a.number if a else None,
    )
    if tell:
        notify_doctor(db, r)
    return r


def notify_doctor(db: Session, r: models.PatientReport) -> None:
    doctor = db.get(models.Worker, r.worker_id) if r.worker_id else None
    patient = db.get(models.Patient, r.patient_id)
    if doctor is None:
        return
    who = f"{patient.first_name} {patient.last_name} ({patient.mrn})"
    link = _staff_link(r)
    subject = f"To sign: {KIND_LABELS[r.kind]} {r.number} — {who}"
    lines = [
        f"Hi {doctor.first_name},",
        "",
        f"the office prepared the {KIND_LABELS[r.kind].lower()} of {who} for your review and signature",
        f"(for: {purpose_label(r)}{f'; needed by {r.needed_by:%b %d, %Y}' if r.needed_by else ''}).",
        "",
        "Open it on the staff dashboard → Reports" + (f":\n  {link}" if link else "."),
        "",
        "(No medical details are included in this message.)",
    ]
    sms = (
        f"{config.OFFICE_NAME}: {r.number} ({KIND_LABELS[r.kind].lower()}, {patient.mrn}) is waiting for your "
        "signature."
    )
    sent, to, notes = appointments.deliver(
        doctor.email, doctor.phone, subject, "\n".join(lines), sms, whose="the staff record"
    )
    r.doctor_notify_channels = ",".join(sent)
    r.doctor_notify_note = ("; ".join(notes))[:500] or None
    r.doctor_notified_at = _now() if sent else None


# ---------------------------------------------------------------------------- the doctor


def sign_blocker(r: models.PatientReport, w: models.Worker | None) -> str | None:
    if r.status != "draft":
        return {
            "signed": "This report is already signed.",
            "declined": "You returned this report to the office.",
            "requested": "The office has not prepared this report yet.",
        }.get(r.status, f"This report is {r.status}.")
    if not can_sign(w):
        return "Only a doctor signs these reports."
    return None


def findings_problems(r: models.PatientReport) -> list[str]:
    """What must be filled in before the doctor can sign."""
    if r.kind != "annual_exam":
        return []
    f = findings_of(r)
    problems = []
    if f.fitness is None:
        problems.append("Choose the decision for the stated purpose (fit / fit with restrictions / …).")
    if f.fitness == "fit_with_restrictions" and not f.restrictions:
        problems.append("Describe the restrictions.")
    statuses = {key: (f.systems.get(key) or schemas.ExamFinding()).status for key, _ in EXAM_SYSTEMS}
    if all(s == "not_examined" for s in statuses.values()):
        problems.append("Record the examination (at least one system).")
    for key, label in EXAM_SYSTEMS:
        finding = f.systems.get(key)
        if finding is not None and finding.status == "abnormal" and not finding.note:
            problems.append(f"Describe the abnormal finding: {label}.")
    return problems


def update(r: models.PatientReport, body: schemas.ReportUpdate) -> None:
    if r.status != "draft":
        raise HTTPException(status_code=409, detail="Only a report waiting for your signature can be edited.")
    if body.findings is not None:
        unknown = set(body.findings.systems) - {k for k, _ in EXAM_SYSTEMS}
        if unknown:
            raise HTTPException(status_code=422, detail=f"findings.systems: unknown {', '.join(sorted(unknown))}")
        r.findings = body.findings.model_dump_json()
    if "doctor_note" in body.model_fields_set:
        r.doctor_note = body.doctor_note
    r.updated_at = _now()


def _fingerprint(r: models.PatientReport) -> str:
    payload = json.dumps(
        {"content": json.loads(r.content or "{}"), "findings": json.loads(r.findings or "{}"), "note": r.doctor_note},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _verification(r: models.PatientReport) -> str:
    msg = f"rpt:{r.id}:{r.patient_id}:{r.version}:{appointments.as_utc(r.signed_at).isoformat()}:{_fingerprint(r)}"
    return hmac.new(config.TOKEN_SECRET.encode("utf-8"), msg.encode(), hashlib.sha256).hexdigest()[:10].upper()


def sign(
    db: Session, r: models.PatientReport, w: models.Worker, actor: models.Account, body: schemas.ReportSign
) -> models.PatientReport:
    """The doctor signs: the record is read one last time, everything is frozen, the signature recorded, an
    amended version supersedes the one it replaces; then (unless asked not to) it goes to the patient."""
    import esign

    problem = sign_blocker(r, w)
    if problem:
        raise HTTPException(status_code=403 if "Only a doctor" in problem else 409, detail=problem)
    missing = findings_problems(r)
    if missing:
        raise HTTPException(status_code=422, detail="Before signing: " + " ".join(missing))
    refresh(db, r)
    try:
        result, signed_at = esign.sign(
            body.signature.signer_name, body.signature.image_png, f"patient_report_{r.kind}", f"{r.number}-v{r.version}"
        )
    except esign.NotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except esign.ESignError as exc:
        raise HTTPException(status_code=422, detail=f"Your signature: {exc}") from exc
    r.status = "signed"
    r.signer_name = body.signature.signer_name
    r.signature_png = body.signature.image_png
    r.signed_at = signed_at
    r.signed_by = actor.id
    r.esign_reference, r.esign_provider = result.reference, result.provider
    db.flush()
    r.verification = _verification(r)
    if r.replaces_id:
        old = db.get(models.PatientReport, r.replaces_id)
        if old is not None and old.status == "signed":
            old.status, old.superseded_at, old.superseded_by_id = "superseded", signed_at, r.id
    db.flush()
    accounts.audit(db, actor, "patient.report.sign", None, report=r.number, version=r.version, esign=result.reference)
    log.info("%s v%d signed by %s (%s)", r.number, r.version, w.staff_number, result.reference)
    if body.send_to_patient:
        send(db, r, actor)
    return r


def decline(db: Session, r: models.PatientReport, w: models.Worker, actor: models.Account, reason: str) -> None:
    if r.status != "draft":
        raise HTTPException(status_code=409, detail="Only a report waiting for your signature can be returned.")
    r.status, r.decline_reason, r.declined_at = "declined", reason, _now()
    accounts.audit(db, actor, "patient.report.decline", None, report=r.number, reason=reason)
    notify_office(db, r, "declined")


def amend(
    db: Session, r: models.PatientReport, w: models.Worker, actor: models.Account, reason: str
) -> models.PatientReport:
    """A new version of a signed report, assigned to the same doctor, with their part copied."""
    if r.status != "signed":
        raise HTTPException(status_code=409, detail="Only a signed report can be amended.")
    if not can_sign(w):
        raise HTTPException(status_code=403, detail="Only a doctor signs these reports.")
    pending = db.execute(
        select(models.PatientReport).where(
            models.PatientReport.replaces_id == r.id, models.PatientReport.status.in_(models.PATIENT_REPORT_OPEN)
        )
    ).first()
    if pending:
        raise HTTPException(status_code=409, detail="An amendment of this report is already being prepared.")
    new = models.PatientReport(
        patient_id=r.patient_id,
        kind=r.kind,
        status="draft",
        version=r.version + 1,
        replaces_id=r.id,
        purpose=r.purpose,
        purpose_detail=r.purpose_detail,
        needed_by=r.needed_by,
        request_note=r.request_note,
        requested_via=r.requested_via,
        requested_by=r.requested_by,
        requested_at=r.requested_at,
        worker_id=w.id,
        appointment_id=r.appointment_id,
        findings=r.findings,
        doctor_note=r.doctor_note,
        amendment_reason=reason,
        generated_by=actor.id,
    )
    db.add(new)
    db.flush()
    new.content = build_content(db, new).model_dump_json()
    new.generated_at = _now()
    accounts.audit(db, actor, "patient.report.amend", None, report=r.number, amendment=new.number)
    return new


def cancel(db: Session, r: models.PatientReport, by: str, actor: models.Account | None, reason: str) -> None:
    """Withdraw an open report. Canceled by the office: the patient is told (with the reason)."""
    if r.status not in models.PATIENT_REPORT_OPEN:
        raise HTTPException(status_code=409, detail=f"This report is {r.status} — it cannot be canceled.")
    r.status, r.cancel_reason, r.canceled_at, r.canceled_by = "canceled", reason, _now(), by
    accounts.audit(db, actor, "patient.report.cancel", None, report=r.number, by=by, reason=reason)
    if by == "office":
        patient = db.get(models.Patient, r.patient_id)
        subject = f"Your {KIND_LABELS[r.kind].lower()} request — {config.OFFICE_NAME}"
        body = "\n".join(
            [
                f"Hi {patient.first_name},",
                "",
                f"the office could not prepare the {KIND_LABELS[r.kind].lower()} you asked for ({r.number}):",
                f"  {reason}",
                "",
                f"Questions? {config.OFFICE_NAME}{f', {config.OFFICE_PHONE}' if config.OFFICE_PHONE else ''}.",
            ]
        )
        sms = f"{config.OFFICE_NAME}: your request {r.number} was canceled — details by e-mail / in the app."
        sent, _to, notes = appointments.deliver_to_patient(patient, subject, body, sms)
        r.delivery_note = ("patient told: " + ",".join(sent) if sent else "; ".join(notes))[:500] or None


# ---------------------------------------------------------------------------- the hand-over


def filename(r: models.PatientReport) -> str:
    kind = "immunization-record" if r.kind == "immunization" else "annual-exam"
    return f"{r.number}-{kind}-v{r.version}.pdf"


def send(db: Session, r: models.PatientReport, actor: models.Account | None) -> models.PatientReport:
    """E-mail the signed report (the PDF attached) and text the patient that it is ready; recorded on the row,
    a provider problem is a note. ``409`` unless signed. Caller commits."""
    if r.status != "signed":
        raise HTTPException(status_code=409, detail="Only a signed report can be sent to the patient.")
    patient = db.get(models.Patient, r.patient_id)
    kind = KIND_LABELS[r.kind].lower()
    has_account = patient.account_id is not None
    subject = f"Your {kind} — {config.OFFICE_NAME}"
    body = "\n".join(
        [
            f"Hi {patient.first_name},",
            "",
            f"your {kind} ({purpose_label(r)}) is attached as a PDF, signed by {r.signer_name}.",
            "You can also download it in your patient account: My reports." if has_account else "",
            "",
            f"Questions? {config.OFFICE_NAME}{f', {config.OFFICE_PHONE}' if config.OFFICE_PHONE else ''}.",
            "",
            f"{r.number} v{r.version} — verification {r.verification}.",
        ]
    )
    sms = f"{config.OFFICE_NAME}: your {kind} ({r.number}) is ready — " + (
        "in your e-mail and your patient account." if has_account else "we sent it to your e-mail."
    )
    attachment = messaging.Attachment(filename=filename(r), content_type="application/pdf", data=render_pdf(db, r))
    sent, to, notes = appointments.deliver_document(patient, subject, body, sms, attachment)
    r.sent_channels = ",".join(sent)
    r.sent_to = ", ".join(to)
    r.delivery_note = ("; ".join(notes))[:500] or None
    if sent:
        r.sent_at = _now()
    accounts.audit(db, actor, "patient.report.send", None, report=r.number, channels=sent or ["none"])
    return r


# ---------------------------------------------------------------------------- output


def _split(value: str | None) -> list[str]:
    return [x for x in (value or "").split(",") if x]


def out(
    db: Session, r: models.PatientReport, *, worker: models.Worker | None = None, patient_view: bool = False
) -> schemas.PatientReportOut:
    """``worker``: the signed-in doctor (``can_sign``). ``patient_view``: the patient sees the content and the
    doctor's part of a signed report only, never the office's notes."""
    patient = db.get(models.Patient, r.patient_id)
    doctor = db.get(models.Worker, r.worker_id) if r.worker_id else None
    a = db.get(models.Appointment, r.appointment_id) if r.appointment_id else None
    show = not patient_view or r.status in ("signed", "superseded")
    blocker = sign_blocker(r, worker) if worker is not None else "Only the assigned doctor signs it."
    return schemas.PatientReportOut(
        id=r.id,
        number=r.number,
        kind=r.kind,
        kind_label=KIND_LABELS[r.kind],
        status=r.status,
        status_label=(PATIENT_STATUS_LABELS if patient_view else STATUS_LABELS)[r.status],
        version=r.version,
        patient_id=r.patient_id,
        patient_name=f"{patient.first_name} {patient.last_name}",
        patient_mrn=patient.mrn,
        purpose=r.purpose,
        purpose_label=purpose_label(r),
        purpose_detail=r.purpose_detail,
        needed_by=r.needed_by,
        request_note=r.request_note,
        requested_via=r.requested_via,
        requested_at=r.requested_at,
        worker_id=r.worker_id,
        doctor_name=appointments._provider_name(doctor) if doctor else None,
        appointment_id=None if patient_view else r.appointment_id,
        appointment_number=a.number if a else None,
        content=content_of(r) if show else None,
        generated_at=r.generated_at,
        findings=findings_of(r) if show else schemas.ReportFindings(),
        doctor_note=r.doctor_note if show else None,
        signer_name=r.signer_name,
        signed_at=r.signed_at,
        signature_png=r.signature_png if show else None,
        esign_reference=None if patient_view else r.esign_reference,
        verification=f"{r.number}-{r.verification}" if r.verification else None,
        decline_reason=None if patient_view else r.decline_reason,
        declined_at=None if patient_view else r.declined_at,
        cancel_reason=r.cancel_reason,
        canceled_at=r.canceled_at,
        amendment_reason=r.amendment_reason,
        superseded_at=r.superseded_at,
        replaces_id=r.replaces_id,
        doctor_notified_at=None if patient_view else r.doctor_notified_at,
        doctor_notify_channels=[] if patient_view else _split(r.doctor_notify_channels),
        sent_at=r.sent_at,
        sent_channels=_split(r.sent_channels),
        sent_to=[] if patient_view else [t for t in (r.sent_to or "").split(", ") if t],
        delivery_note=None if patient_view else r.delivery_note,
        patient_viewed_at=r.patient_viewed_at,
        can_sign=blocker is None,
        sign_blocker=blocker,
        missing=findings_problems(r) if r.status == "draft" and not patient_view else [],
        updated_at=r.updated_at,
    )


# ---------------------------------------------------------------------------- the PDF

FOOTER_TOP = 72


def render_pdf(db: Session, r: models.PatientReport) -> bytes:
    """The report as a Letter-size PDF (a draft is marked DRAFT; a replaced version SUPERSEDED)."""
    import referrals  # the shared footer

    c = content_of(r)
    if c is None:
        raise HTTPException(status_code=409, detail="The office has not prepared this report yet.")
    f = findings_of(r)
    tz = appointments.office_tz()
    doc = pdf.PdfDocument(title=f"{c.title} {r.number}", author=c.office_name, margin=FOOTER_TOP)
    left, right, width = 54, 558, 504
    col2 = 310

    def header() -> float:
        doc.text(left, 750, f"{c.office_name} · {c.title.lower()} {r.number} — {c.patient_name} (continued)", size=9)
        return 726

    def room(y: float, needed: float) -> float:
        if y - needed < FOOTER_TOP:
            doc.new_page()
            return header()
        return y

    y = 738
    doc.text(left, y, c.office_name, size=18, bold=True)
    doc.text_right(right, y, KIND_TITLES[r.kind], size=14, bold=True)
    y -= 16
    office = " · ".join(x for x in (c.office_address, c.office_phone and f"Phone {c.office_phone}") if x)
    if office:
        doc.text(left, y, office, size=9, gray=0.35)
    doc.text_right(right, y, f"{r.number} · version {r.version}", size=10, bold=True)
    y -= 12
    doc.line(left, y, right, y, width=1.2)
    y -= 20
    doc.text(left, y, "PATIENT", size=8, bold=True, gray=0.4)
    doc.text(col2, y, "REQUESTED FOR", size=8, bold=True, gray=0.4)
    y -= 14
    y = doc.columns(
        y,
        (
            left,
            col2 - left - 14,
            [
                (c.patient_name, True),
                (
                    f"Date of birth: {c.patient_date_of_birth:%m/%d/%Y}"
                    + (f" · {c.patient_sex}" if c.patient_sex else ""),
                    False,
                ),
                (f"Record number: {c.patient_mrn}", False),
            ],
        ),
        (
            col2,
            right - col2,
            [
                (c.purpose_label, True),
                (f"Needed by: {r.needed_by:%m/%d/%Y}" if r.needed_by else "", False),
                (f"Doctor: {c.generated_for}" if c.generated_for else "", False),
            ],
        ),
    )
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
        paragraphs = ([f"• {x}" for x in body] if isinstance(body, list) else body.split("\n")) if body else [empty]
        for para in paragraphs:
            if para.strip():
                y = doc.paragraph(left, y, para, width=width, size=10, on_new_page=header)
            else:
                y -= 5
        y -= 8

    def table(columns: list[tuple[str, float]], rows: list[list[str]]) -> None:
        nonlocal y

        def head() -> None:
            nonlocal y
            x = left
            for title, w in columns:
                doc.text(x, y, title.upper(), size=7.5, bold=True, gray=0.35)
                x += w
            y -= 4
            doc.line(left, y, right, y, width=0.5, gray=0.6)
            y -= 11

        y = room(y, 40)
        head()
        for row in rows:
            cells = [pdf.wrap(text or "—", w - 6, 9) for text, (_, w) in zip(row, columns, strict=True)]
            height = max(len(lines) for lines in cells) * 11.5
            if y - height < FOOTER_TOP:
                doc.new_page()
                y = header()
                head()
            x = left
            for lines, (_, w) in zip(cells, columns, strict=True):
                for i, text in enumerate(lines):
                    doc.text(x, y - i * 11.5, text, size=9)
                x += w
            y -= height + 3
            doc.line(left, y + 7, right, y + 7, width=0.3, gray=0.8)
        y -= 8

    if r.kind == "immunization":
        if c.immunizations:
            table(
                [
                    ("Vaccine", 150),
                    ("Date", 62),
                    ("Dose", 44),
                    ("Product · lot", 100),
                    ("Site · route", 72),
                    ("Given by", 76),
                ],
                [
                    [
                        s.vaccine,
                        f"{s.date_given:%m/%d/%Y}",
                        s.dose or "",
                        s.product or "",
                        s.site_route or "",
                        s.given_by or "",
                    ]
                    for s in c.immunizations
                ],
            )
        else:
            section("Immunizations", None, "No immunizations on record.")
        section("Allergies", c.allergies, "No known allergies recorded.")
        section("Contraindications / exemptions", f.contraindications)
        section("Vaccines due or recommended", f.vaccines_due)
        section("Comments", r.doctor_note)
        statement = (
            f"I certify that the immunizations listed above are those recorded in the medical record of "
            f"{c.patient_name} at {c.office_name}."
        )
    else:
        exam = [
            c.exam_date and f"{c.exam_date} ({tz})",
            c.exam_provider and f"— {c.exam_provider}",
            c.exam_visit_number and f"({c.exam_visit_number})",
        ]
        section("Examination", " ".join(x for x in exam if x) or "Documented by the signing doctor.")
        measures = [
            f.height and f"Height {f.height}",
            (f.weight or c.weight) and f"Weight {f.weight or c.weight}",
            (f.blood_pressure or c.blood_pressure) and f"Blood pressure {f.blood_pressure or c.blood_pressure}",
            c.pulse and f"Pulse {c.pulse}",
            c.temperature and f"Temperature {c.temperature}",
        ]
        section("Measurements", " · ".join(x for x in measures if x) or None)
        sight = [f.vision and f"Vision: {f.vision}", f.hearing and f"Hearing: {f.hearing}"]
        section("Vision and hearing", " · ".join(x for x in sight if x) or None)
        y = room(y, 40)
        doc.text(left, y, "EXAMINATION BY SYSTEM", size=8.5, bold=True, gray=0.35)
        y -= 16
        table(
            [("System", 170), ("Finding", 80), ("Details", 254)],
            [
                [
                    label,
                    FINDING_LABELS[(f.systems.get(key) or schemas.ExamFinding()).status],
                    (f.systems.get(key) or schemas.ExamFinding()).note or "",
                ]
                for key, label in EXAM_SYSTEMS
            ],
        )
        section("Medical history", c.conditions + [f"Surgery: {s}" for s in c.surgeries], "No conditions recorded.")
        section("Family history", c.family_history)
        section("Social history", c.social_history)
        section("Medications", c.medications, "None.")
        section("Allergies", c.allergies, "No known allergies recorded.")
        latest: dict[str, schemas.ReportImmunization] = {}
        for s in c.immunizations:
            latest[s.vaccine] = s  # oldest first → the last one wins
        section(
            "Immunizations (latest dose)",
            [f"{s.vaccine} — {s.date_given:%m/%d/%Y}{f' (dose {s.dose})' if s.dose else ''}" for s in latest.values()],
            "No immunizations on record.",
        )
        section("Recent results", c.recent_results)
        y = room(y, 60)
        doc.text(left, y, "ASSESSMENT", size=8.5, bold=True, gray=0.35)
        y -= 15
        y = doc.paragraph(
            left, y, FITNESS_LABELS.get(f.fitness or "", "—"), width=width, size=11, bold=True, on_new_page=header
        )
        y -= 4
        if f.restrictions:
            y = doc.paragraph(left, y, f"Restrictions: {f.restrictions}", width=width, size=10, on_new_page=header)
        y -= 8
        section("Recommendations", f.recommendations)
        section("Next examination due", f"{f.next_exam_due:%B %d, %Y}" if f.next_exam_due else None)
        section("Comments", r.doctor_note)
        statement = (
            f"I examined {c.patient_name}{f' on {c.exam_date}' if c.exam_date else ''} and certify that this report "
            "is correct to the best of my knowledge."
        )

    # Certification and signature.
    y = room(y, 120)
    doc.line(left, y, right, y)
    y -= 16
    y = doc.paragraph(left, y, statement, width=width, size=9.5)
    y -= 6
    if r.status in ("draft", "declined", "requested"):
        doc.text(left, y - 10, "DRAFT — NOT SIGNED YET", size=22, bold=True)
        doc.paragraph(
            left, y - 28, f"Waiting for the signature of {c.generated_for or 'a doctor'}.", width=width, size=9.5
        )
    else:
        signed = appointments.as_utc(r.signed_at).astimezone(tz)
        image = None
        if r.signature_png:
            try:
                image = pdf.decode_png(base64.b64decode(r.signature_png.split(",", 1)[1]))
            except (ValueError, IndexError):
                image = None
        if image is not None:
            doc.image(left, y - 52, 170, 50, image)
            y -= 58
        doctor = c.generated_for or r.signer_name
        signer = doctor if (r.signer_name or "").lower() in (doctor or "").lower() else f"{r.signer_name} ({doctor})"
        doc.text(left, y, f"Electronically signed by {signer}", size=10, bold=True)
        doc.text(left, y - 13, f"on {signed:%B} {signed.day}, {signed.year} at {signed:%H:%M} ({tz})", size=9.5)
        doc.text_right(right, y, f"Verification: {r.number}-{r.verification or ''}", size=9.5)
        if r.esign_reference:
            doc.text_right(right, y - 13, f"E-signature: {r.esign_reference}", size=8.5)
        y -= 32
        if r.amendment_reason:
            y = doc.paragraph(left, y, f"Amended version {r.version}: {r.amendment_reason}", width=width, size=9)
        if r.status == "superseded":
            y = room(y, 30)
            doc.text(left, y - 8, "SUPERSEDED — SEE THE AMENDED VERSION", size=18, bold=True)
    doc.on_each_page(
        lambda n, total: referrals.footer(
            doc,
            n,
            total,
            f"{c.title} {r.number} v{r.version}",
            "SAMPLE — dummy data (development): not a real medical record",
        )
    )
    return doc.output()


def _pdf_response(r: models.PatientReport, data: bytes) -> Response:
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename(r)}"', "Cache-Control": "private, no-store"},
    )


# ---------------------------------------------------------------------------- routes: the office


def _report(db: Session, report_id: int) -> models.PatientReport:
    r = db.get(models.PatientReport, report_id)
    if r is None:
        raise HTTPException(status_code=404, detail="Report not found.")
    return r


def _patient(db: Session, patient_id: int) -> models.Patient:
    p = db.get(models.Patient, patient_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Patient not found.")
    return p


QUEUE_FILTERS = {
    "open": models.PATIENT_REPORT_OPEN,
    "requested": ("requested",),
    "draft": ("draft",),
    "declined": ("declined",),
    "signed": ("signed",),
    "canceled": ("canceled", "superseded"),
}


@admin_router.get("/patient-reports", response_model=schemas.PatientReportPage)
def office_queue(
    status: str = Query(default="open", pattern="^(open|requested|draft|declined|signed|canceled|all)$"),
    kind: str | None = Query(default=None, pattern="^(immunization|annual_exam)$"),
    q: str = Query(default="", max_length=120),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    _p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    """The office's queue: requests to prepare, reports waiting for a doctor / returned by one, signed ones."""
    stmt = select(models.PatientReport).join(models.Patient, models.Patient.id == models.PatientReport.patient_id)
    if kind:
        stmt = stmt.where(models.PatientReport.kind == kind)
    text = q.strip()
    if text:
        m = re.match(r"^\s*(?:rpt-?)?0*(\d+)\s*$", text, re.IGNORECASE)
        mrn = re.match(r"^\s*p-?0*(\d+)\s*$", text, re.IGNORECASE)
        name = patients.normalize_name(text)
        conds = [models.Patient.last_name_norm.like(f"%{name}%"), models.Patient.first_name_norm.like(f"%{name}%")]
        if m:
            conds.append(models.PatientReport.id == int(m.group(1)))
        if mrn:
            conds.append(models.Patient.id == int(mrn.group(1)))
        stmt = stmt.where(or_(*conds))
    rows = (
        db.execute(stmt.order_by(models.PatientReport.requested_at.desc(), models.PatientReport.id.desc()))
        .scalars()
        .all()
    )
    counts = {key: sum(1 for r in rows if r.status in statuses) for key, statuses in QUEUE_FILTERS.items()}
    counts["all"] = len(rows)
    chosen = rows if status == "all" else [r for r in rows if r.status in QUEUE_FILTERS[status]]
    for r in chosen:
        refresh(db, r)
    db.commit()
    return schemas.PatientReportPage(
        items=[out(db, r) for r in chosen[offset : offset + limit]], total=len(chosen), counts=counts
    )


@admin_router.get("/patient-reports/{report_id}", response_model=schemas.PatientReportOut)
def office_report(report_id: int, _p: auth.Principal = Depends(auth.admin_principal), db: Session = Depends(get_db)):
    r = _report(db, report_id)
    refresh(db, r)
    db.commit()
    return out(db, r)


@admin_router.post("/patient-reports/{report_id}/generate", response_model=schemas.PatientReportOut)
def office_generate(
    report_id: int,
    body: schemas.ReportGenerateIn,
    p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    """Prepare it from the record for the chosen doctor (who is told)."""
    r = generate(db, _report(db, report_id), body.worker_id, body.appointment_id, p.account)
    db.commit()
    return out(db, r)


@admin_router.post("/patient-reports/{report_id}/cancel", response_model=schemas.PatientReportOut)
def office_cancel(
    report_id: int,
    body: schemas.ReportReason,
    p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    r = _report(db, report_id)
    cancel(db, r, "office", p.account, body.reason)
    db.commit()
    return out(db, r)


@admin_router.post("/patient-reports/{report_id}/send", response_model=schemas.PatientReportOut)
def office_send(report_id: int, p: auth.Principal = Depends(auth.admin_principal), db: Session = Depends(get_db)):
    r = send(db, _report(db, report_id), p.account)
    db.commit()
    return out(db, r)


@admin_router.get(
    "/patient-reports/{report_id}/pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}, "description": "The report"}},
)
def office_pdf(report_id: int, p: auth.Principal = Depends(auth.admin_principal), db: Session = Depends(get_db)):
    r = _report(db, report_id)
    refresh(db, r)
    data = render_pdf(db, r)
    accounts.audit(db, p.account, "patient.report.pdf", None, report=r.number, version=r.version)
    db.commit()
    return _pdf_response(r, data)


@admin_router.get("/patients/{patient_id}/reports", response_model=list[schemas.PatientReportOut])
def patient_reports(patient_id: int, _p: auth.Principal = Depends(auth.admin_principal), db: Session = Depends(get_db)):
    patient = _patient(db, patient_id)
    rows = (
        db.execute(
            select(models.PatientReport)
            .where(models.PatientReport.patient_id == patient.id)
            .order_by(models.PatientReport.requested_at.desc(), models.PatientReport.id.desc())
        )
        .scalars()
        .all()
    )
    return [out(db, r) for r in rows]


@admin_router.get("/patients/{patient_id}/reports/options", response_model=schemas.ReportOptions)
def patient_report_options(
    patient_id: int, _p: auth.Principal = Depends(auth.admin_principal), db: Session = Depends(get_db)
):
    return options(db, _patient(db, patient_id))


@admin_router.post("/patients/{patient_id}/reports", response_model=schemas.PatientReportOut, status_code=201)
def office_request(
    patient_id: int,
    body: schemas.OfficeReportRequestIn,
    p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    """Record a request made at the desk / on the phone — prepared at once when a doctor is given."""
    patient = _patient(db, patient_id)
    r = create_request(
        db,
        patient,
        kind=body.kind,
        purpose=body.purpose,
        purpose_detail=body.purpose_detail,
        needed_by=body.needed_by,
        note=body.note,
        via="office",
        actor=p.account,
    )
    if body.worker_id is not None:
        generate(db, r, body.worker_id, body.appointment_id, p.account)
    db.commit()
    return out(db, r)


# ---------------------------------------------------------------------------- routes: the doctor


def _mine(db: Session, s: staff_accounts.StaffPrincipal, report_id: int) -> models.PatientReport:
    r = db.get(models.PatientReport, report_id)
    if r is None or r.worker_id != s.worker.id or r.status == "requested":
        raise HTTPException(status_code=404, detail="Report not found.")
    return r


@staff_router.get("", response_model=list[schemas.PatientReportOut])
def my_reports(
    status: str = Query(default="todo", pattern="^(todo|signed|all)$"),
    s: staff_accounts.StaffPrincipal = Depends(staff_accounts.staff_principal),
    db: Session = Depends(get_db),
):
    """The reports assigned to the signed-in doctor: waiting for them (oldest request first), signed, all."""
    stmt = select(models.PatientReport).where(
        models.PatientReport.worker_id == s.worker.id, models.PatientReport.status != "requested"
    )
    if status == "todo":
        stmt = stmt.where(models.PatientReport.status == "draft").order_by(models.PatientReport.requested_at)
    else:
        if status == "signed":
            stmt = stmt.where(models.PatientReport.status.in_(("signed", "superseded")))
        stmt = stmt.order_by(models.PatientReport.updated_at.desc())
    rows = db.execute(stmt).scalars().all()
    for r in rows:
        refresh(db, r)
    db.commit()
    return [out(db, r, worker=s.worker) for r in rows]


@staff_router.get("/{report_id}", response_model=schemas.PatientReportOut)
def my_report(
    report_id: int,
    s: staff_accounts.StaffPrincipal = Depends(staff_accounts.staff_principal),
    db: Session = Depends(get_db),
):
    r = _mine(db, s, report_id)
    refresh(db, r)
    accounts.audit(db, s.principal.account, "patient.report.view", None, report=r.number)
    db.commit()
    return out(db, r, worker=s.worker)


@staff_router.put("/{report_id}", response_model=schemas.PatientReportOut)
def my_report_update(
    report_id: int,
    body: schemas.ReportUpdate,
    s: staff_accounts.StaffPrincipal = Depends(staff_accounts.staff_principal),
    db: Session = Depends(get_db),
):
    """The doctor's part: the examination, the decision, remarks."""
    r = _mine(db, s, report_id)
    update(r, body)
    refresh(db, r)
    db.commit()
    return out(db, r, worker=s.worker)


@staff_router.post("/{report_id}/sign", response_model=schemas.PatientReportOut)
def my_report_sign(
    report_id: int,
    body: schemas.ReportSign,
    s: staff_accounts.StaffPrincipal = Depends(staff_accounts.staff_principal),
    db: Session = Depends(get_db),
):
    r = _mine(db, s, report_id)
    sign(db, r, s.worker, s.principal.account, body)
    db.commit()
    return out(db, r, worker=s.worker)


@staff_router.post("/{report_id}/decline", response_model=schemas.PatientReportOut)
def my_report_decline(
    report_id: int,
    body: schemas.ReportReason,
    s: staff_accounts.StaffPrincipal = Depends(staff_accounts.staff_principal),
    db: Session = Depends(get_db),
):
    """Send it back to the office with the reason (e.g. no recent exam — the patient should book one)."""
    r = _mine(db, s, report_id)
    decline(db, r, s.worker, s.principal.account, body.reason)
    db.commit()
    return out(db, r, worker=s.worker)


@staff_router.post("/{report_id}/amend", response_model=schemas.PatientReportOut)
def my_report_amend(
    report_id: int,
    body: schemas.ReportReason,
    s: staff_accounts.StaffPrincipal = Depends(staff_accounts.staff_principal),
    db: Session = Depends(get_db),
):
    r = _mine(db, s, report_id)
    new = amend(db, r, s.worker, s.principal.account, body.reason)
    db.commit()
    return out(db, new, worker=s.worker)


@staff_router.post("/{report_id}/send", response_model=schemas.PatientReportOut)
def my_report_send(
    report_id: int,
    s: staff_accounts.StaffPrincipal = Depends(staff_accounts.staff_principal),
    db: Session = Depends(get_db),
):
    r = send(db, _mine(db, s, report_id), s.principal.account)
    db.commit()
    return out(db, r, worker=s.worker)


@staff_router.get(
    "/{report_id}/pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}, "description": "The report"}},
)
def my_report_pdf(
    report_id: int,
    s: staff_accounts.StaffPrincipal = Depends(staff_accounts.staff_principal),
    db: Session = Depends(get_db),
):
    r = _mine(db, s, report_id)
    refresh(db, r)
    data = render_pdf(db, r)
    db.commit()
    return _pdf_response(r, data)


# ---------------------------------------------------------------------------- routes: the patient


def _account_patients(db: Session, p: auth.Principal) -> dict[int, models.Patient]:
    return {r.id: r for r in appointments.my_records(db, p.account)}


def _my_report(db: Session, p: auth.Principal, report_id: int) -> models.PatientReport:
    r = db.get(models.PatientReport, report_id)
    if r is None or r.patient_id not in _account_patients(db, p) or r.status == "superseded":
        raise HTTPException(status_code=404, detail="Report not found.")
    return r


@me_router.get("", response_model=list[schemas.PatientReportOut])
def my_patient_reports(p: auth.Principal = Depends(auth.patient_principal), db: Session = Depends(get_db)):
    """The reports of the account's records (replaced versions left out), newest first."""
    ids = list(_account_patients(db, p))
    db.commit()
    if not ids:
        return []
    rows = (
        db.execute(
            select(models.PatientReport)
            .where(models.PatientReport.patient_id.in_(ids), models.PatientReport.status != "superseded")
            .order_by(models.PatientReport.requested_at.desc(), models.PatientReport.id.desc())
        )
        .scalars()
        .all()
    )
    return [out(db, r, patient_view=True) for r in rows]


@me_router.post("", response_model=schemas.PatientReportOut, status_code=201)
def my_report_request(
    body: schemas.ReportRequestIn, p: auth.Principal = Depends(auth.patient_principal), db: Session = Depends(get_db)
):
    """Ask the office for a report (it is prepared and signed by a doctor; you get it by e-mail and here)."""
    patient = _account_patients(db, p).get(body.patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient record not found on this account.")
    r = create_request(
        db,
        patient,
        kind=body.kind,
        purpose=body.purpose,
        purpose_detail=body.purpose_detail,
        needed_by=body.needed_by,
        note=body.note,
        via="patient",
        actor=p.account,
    )
    notify_office(db, r, "requested")
    db.commit()
    return out(db, r, patient_view=True)


@me_router.get("/{report_id}", response_model=schemas.PatientReportOut)
def my_patient_report(
    report_id: int, p: auth.Principal = Depends(auth.patient_principal), db: Session = Depends(get_db)
):
    r = _my_report(db, p, report_id)
    if r.status == "signed" and r.patient_viewed_at is None:
        r.patient_viewed_at = _now()
        accounts.audit(db, p.account, "patient.report.view", p.account, report=r.number)
    db.commit()
    return out(db, r, patient_view=True)


@me_router.get(
    "/{report_id}/pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}, "description": "The signed report"}},
)
def my_patient_report_pdf(
    report_id: int, p: auth.Principal = Depends(auth.patient_principal), db: Session = Depends(get_db)
):
    r = _my_report(db, p, report_id)
    if r.status != "signed":
        raise HTTPException(status_code=404, detail="The report is not signed yet.")
    if r.patient_viewed_at is None:
        r.patient_viewed_at = _now()
    data = render_pdf(db, r)
    db.commit()
    return _pdf_response(r, data)


@me_router.post("/{report_id}/cancel", response_model=schemas.PatientReportOut)
def my_report_cancel(
    report_id: int,
    body: schemas.ReportReason | None = Body(default=None),
    p: auth.Principal = Depends(auth.patient_principal),
    db: Session = Depends(get_db),
):
    """Withdraw a request that is still open."""
    r = _my_report(db, p, report_id)
    cancel(db, r, "patient", p.account, body.reason if body else "Withdrawn by the patient.")
    db.commit()
    return out(db, r, patient_view=True)
