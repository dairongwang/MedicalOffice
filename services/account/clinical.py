"""The clinical record and the provider's chart.

When the medical assistant has finished preparing a visit, the visit's provider is told the patient is
ready (assignments.notify_provider) and opens the patient's **chart** on the staff dashboard:

    GET /api/v1/staff/me/appointments/{id}/chart        everything to see the patient (below)
    PUT /api/v1/staff/me/appointments/{id}/visit-note   "Start visit" (seen) + the note, assessment, plan

    today's visit      the appointment, the patient's check-in answers, the medical assistant's work (vital
                       signs, every medication / allergy reviewed with its decision and note, the notes),
                       the provider's own visit note, diagnoses and prescriptions (prescriptions.py)
    medications, allergies   the chart's lists (current first; what is no longer current, and why)
    medical history    conditions, surgeries, family history, social history (health_records.py)
    results            lab results (values with reference ranges and flags) and imaging reports, newest first
    referral notes     to specialists (outgoing) and from other practices (incoming) — valid until, status;
                       the provider writes new ones with a signed letter (referrals.py)
    the pharmacy       the pharmacy the patient uses (asked at the visit — prescriptions.py)
    previous visits    the patient's earlier visits with any provider: reason, vital signs, the assistant,
                       the check-in reason and the provider's note (assessment, plan)

Only the **provider of that visit** may open it (anyone else gets ``404``, as for an unknown visit), and
every opening lands in the audit log (``patient.chart.view``).

The office (administrators) keeps the record on the patient page of the office administration
dashboard (*Clinical*):

    GET/POST  /api/v1/patients/{id}/results        lab results / imaging reports as they arrive
    PATCH     /api/v1/patients/{id}/results/{rid}  correct / amend one
    GET/POST  /api/v1/patients/{id}/referrals      referral notes
    PATCH     /api/v1/patients/{id}/referrals/{rid}   status, validity, the note
    (the medical history: /api/v1/patients/{id}/health-items with the kinds condition · surgery ·
    family_history · social_history — health_records.py)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

import accounts
import appointments
import assignments
import assistant_review
import auth
import health_records
import hiring
import immunizations
import models
import patients
import prescriptions
import referrals
import schemas
import staff_accounts
import twofactor
import visit_summaries
from database import get_db

log = logging.getLogger("account.clinical")

admin_router = APIRouter(prefix="/patients/{patient_id}", tags=["patients"])
staff_router = APIRouter(prefix="/staff/me/appointments", tags=["staff"])

MAX_PREVIOUS_VISITS = 50


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _worker_name(db: Session, worker_id: int | None) -> str | None:
    w = db.get(models.Worker, worker_id) if worker_id else None
    return appointments._provider_name(w) if w is not None else None


# ---------------------------------------------------------------------------- results


def _values(row: models.PatientResult) -> list[schemas.ResultValue]:
    try:
        data = json.loads(row.lab_values or "[]")
    except ValueError:
        return []
    return [schemas.ResultValue(**v) for v in data if isinstance(v, dict)]


def result_out(db: Session, row: models.PatientResult) -> schemas.ResultOut:
    values = _values(row)
    a = db.get(models.Appointment, row.appointment_id) if row.appointment_id else None
    return schemas.ResultOut(
        id=row.id,
        kind=row.kind,
        name=row.name,
        status=row.status,
        ordered_on=row.ordered_on,
        resulted_on=row.resulted_on,
        performed_by=row.performed_by,
        summary=row.summary,
        values=values,
        abnormal=any(v.flag for v in values),
        appointment_id=row.appointment_id,
        appointment_number=a.number if a else None,
        ordered_by=_worker_name(db, row.ordered_by),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def results_of(db: Session, patient: models.Patient) -> list[models.PatientResult]:
    """Newest first (by the result date, else the order date, else when it was entered)."""
    rows = db.execute(select(models.PatientResult).where(models.PatientResult.patient_id == patient.id)).scalars().all()
    return sorted(
        rows,
        key=lambda r: (r.resulted_on or r.ordered_on or r.created_at.date(), r.id),
        reverse=True,
    )


def add_result(
    db: Session, patient: models.Patient, body: schemas.ResultIn, *, entered_by: str | None
) -> models.PatientResult:
    a = _own_appointment(db, patient, body.appointment_id)
    row = models.PatientResult(
        patient_id=patient.id,
        kind=body.kind,
        name=body.name,
        status=body.status,
        ordered_on=body.ordered_on,
        resulted_on=body.resulted_on,
        performed_by=body.performed_by,
        summary=body.summary,
        lab_values=json.dumps([v.model_dump() for v in body.values]),
        appointment_id=a.id if a else None,
        ordered_by=a.provider_id if a else None,
        entered_by=entered_by,
    )
    db.add(row)
    db.flush()
    return row


def _own_appointment(db: Session, patient: models.Patient, appointment_id: int | None) -> models.Appointment | None:
    if appointment_id is None:
        return None
    a = db.get(models.Appointment, appointment_id)
    if a is None or a.patient_id != patient.id:
        raise HTTPException(status_code=422, detail="appointment_id: not a visit of this patient")
    return a


# ---------------------------------------------------------------------------- referrals


def referrals_of(db: Session, patient: models.Patient) -> list[models.PatientReferral]:
    return (
        db.execute(
            select(models.PatientReferral)
            .where(models.PatientReferral.patient_id == patient.id)
            .order_by(models.PatientReferral.referral_date.desc(), models.PatientReferral.id.desc())
        )
        .scalars()
        .all()
    )


def add_referral(
    db: Session, patient: models.Patient, body: schemas.ReferralIn, *, entered_by: str | None
) -> models.PatientReferral:
    a = _own_appointment(db, patient, body.appointment_id)
    row = models.PatientReferral(
        patient_id=patient.id,
        **body.model_dump(exclude={"appointment_id"}),
        appointment_id=a.id if a else None,
        entered_by=entered_by,
    )
    db.add(row)
    db.flush()
    return row


# ---------------------------------------------------------------------------- visit notes


def note_of(db: Session, a: models.Appointment) -> models.VisitNote | None:
    return db.execute(select(models.VisitNote).where(models.VisitNote.appointment_id == a.id)).scalar_one_or_none()


def note_out(db: Session, n: models.VisitNote | None) -> schemas.VisitNoteOut | None:
    if n is None:
        return None
    return schemas.VisitNoteOut(
        appointment_id=n.appointment_id,
        provider_name=_worker_name(db, n.worker_id),
        seen_at=n.seen_at,
        note=n.note,
        assessment=n.assessment,
        plan=n.plan,
        updated_at=n.updated_at,
    )


def save_note(db: Session, a: models.Appointment, worker: models.Worker, body: schemas.VisitNoteIn) -> models.VisitNote:
    if a.status in ("canceled", "no_show"):
        raise HTTPException(
            status_code=409, detail=f"This visit is {a.status.replace('_', ' ')} — there is nothing to note."
        )
    n = note_of(db, a)
    if n is None:
        n = models.VisitNote(appointment_id=a.id, worker_id=worker.id)
        db.add(n)
    if body.seen and n.seen_at is None:
        n.seen_at = _now()
    for key in ("note", "assessment", "plan"):
        if key in body.model_fields_set:
            value = getattr(body, key)
            setattr(n, key, (value.strip() or None) if isinstance(value, str) else value)
    n.worker_id = worker.id
    n.updated_at = _now()
    db.flush()
    return n


# ---------------------------------------------------------------------------- the chart


def _completed_task(db: Session, a: models.Appointment) -> models.AppointmentAssignment | None:
    return next((r for r in assignments.history(db, a) if r.status == "completed"), None)


def previous_visits(db: Session, patient: models.Patient, current: models.Appointment) -> list[schemas.PreviousVisit]:
    """The patient's earlier visits that took place (or were missed) — completed, no-show, or scheduled and
    already over — newest first; a visit still to come (later today too) is not a "previous" one."""
    rows = (
        db.execute(
            select(models.Appointment)
            .where(
                models.Appointment.patient_id == patient.id,
                models.Appointment.id != current.id,
                models.Appointment.start_at < current.start_at,
                or_(
                    models.Appointment.status.in_(("completed", "no_show")),
                    (models.Appointment.status == "scheduled") & (models.Appointment.end_at <= _now()),
                ),
            )
            .order_by(models.Appointment.start_at.desc())
            .limit(MAX_PREVIOUS_VISITS)
        )
        .scalars()
        .all()
    )
    out = []
    for a in rows:
        provider = db.get(models.Worker, a.provider_id)
        task = _completed_task(db, a) or assignments.current_for(db, a)
        check_in = assistant_review.completed_check_in(db, a)
        reason = assistant_review._answers(check_in).get("reason") if check_in else None
        out.append(
            schemas.PreviousVisit(
                id=a.id,
                number=a.number,
                status=a.status,
                start_at=appointments.as_utc(a.start_at),
                start_local=appointments.format_local(a.start_at),
                provider_name=appointments._provider_name(provider),
                provider_role=provider.role,
                reason=a.reason,
                checked_in_at=a.checked_in_at,
                vitals=assistant_review.vitals_out(assistant_review.vitals_of(task)) if task else None,
                assistant_name=_worker_name(db, task.worker_id) if task else None,
                assistant_note=task.completion_note if task else None,
                check_in_reason=reason if isinstance(reason, str) else None,
                visit_note=note_out(db, note_of(db, a)),
                diagnoses=[prescriptions.diagnosis_out(db, d) for d in prescriptions.diagnoses_of(db, a)],
                prescriptions=[
                    f"{rx.number}{' (voided)' if rx.status == 'voided' else ''} · "
                    + ", ".join(prescriptions.item_line(i) for i in prescriptions.items_of(db, rx))
                    for rx in db.execute(
                        select(models.Prescription)
                        .where(models.Prescription.appointment_id == a.id)
                        .order_by(models.Prescription.id)
                    ).scalars()
                ],
                visit_summary=visit_summaries.brief_for(db, a, patient=False),
            )
        )
    return out


def chart(db: Session, a: models.Appointment) -> schemas.ProviderChart:
    patient = db.get(models.Patient, a.patient_id)
    lists = health_records.lists_out(db, patient)
    task = _completed_task(db, a) or assignments.current_for(db, a)
    today = referrals.office_today()
    return schemas.ProviderChart(
        appointment=appointments.appointment_out(db, a, with_link=False, staff=True).model_copy(
            update={"charge": None}
        ),
        patient=schemas.ChartPatient(
            **patients.summary(patient).model_dump(),
            sex=patient.sex,
            age=hiring.age_on(patient.date_of_birth),
            email_masked=twofactor.mask_email(patient.email) if patient.email else None,
            insurance_provider=patient.insurance_provider,
            insurance_plan_name=patient.insurance_plan_name,
            insurance_verification=patients.latest_verification(db, patient),
        ),
        check_in=assistant_review.check_in_answers(db, a),
        assistant_work=assignments.out(db, task) if task else None,
        visit_note=note_out(db, note_of(db, a)),
        medications=lists["medications"],
        allergies=lists["allergies"],
        medical_history=lists["medical_history"],
        results=[result_out(db, r) for r in results_of(db, patient)],
        referrals=[referrals.referral_out(db, r, today) for r in referrals_of(db, patient)],
        previous_visits=previous_visits(db, patient, a),
        diagnoses=[prescriptions.diagnosis_out(db, d) for d in prescriptions.diagnoses_of(db, a)],
        prescriptions=[prescriptions.prescription_out(db, rx) for rx in prescriptions.prescriptions_of(db, patient)],
        pharmacies=[prescriptions.pharmacy_out(p) for p in prescriptions.active_pharmacies(db)],
        patient_pharmacy=prescriptions.patient_pharmacy_out(db, patient),
        can_prescribe=prescriptions.can_prescribe(db.get(models.Worker, a.provider_id)),
        can_refer=referrals.can_refer(db.get(models.Worker, a.provider_id)),
        immunizations=[immunizations.out(db, r) for r in immunizations.of_patient(db, patient)],
        generated_at=_now(),
    )


# ---------------------------------------------------------------------------- routes: the provider


def _my_visit(db: Session, s: staff_accounts.StaffPrincipal, appointment_id: int) -> models.Appointment:
    """A visit where the signed-in member of staff is the provider — anyone else: 404 (as if unknown)."""
    a = db.get(models.Appointment, appointment_id)
    if a is None or a.provider_id != s.worker.id:
        raise HTTPException(status_code=404, detail="Appointment not found.")
    return a


@staff_router.get("/{appointment_id}/chart", response_model=schemas.ProviderChart)
def provider_chart(
    appointment_id: int,
    s: staff_accounts.StaffPrincipal = Depends(staff_accounts.staff_principal),
    db: Session = Depends(get_db),
):
    """The patient's chart for one of the provider's own visits (audited)."""
    a = _my_visit(db, s, appointment_id)
    out = chart(db, a)
    patient = db.get(models.Patient, a.patient_id)
    accounts.audit(db, s.principal.account, "patient.chart.view", None, patient=patient.mrn, appointment=a.number)
    db.commit()
    return out


@staff_router.put("/{appointment_id}/visit-note", response_model=schemas.VisitNoteOut)
def put_visit_note(
    appointment_id: int,
    body: schemas.VisitNoteIn,
    s: staff_accounts.StaffPrincipal = Depends(staff_accounts.staff_principal),
    db: Session = Depends(get_db),
):
    """ "Start visit" (``seen: true`` — once) and the provider's note, assessment and plan."""
    a = _my_visit(db, s, appointment_id)
    n = save_note(db, a, s.worker, body)
    accounts.audit(db, s.principal.account, "visit.note", None, appointment=a.number, seen=bool(n.seen_at))
    db.commit()
    return note_out(db, n)


# ---------------------------------------------------------------------------- routes: the office


def _patient(db: Session, patient_id: int) -> models.Patient:
    p = db.get(models.Patient, patient_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Patient not found.")
    return p


@admin_router.get("/results", response_model=list[schemas.ResultOut])
def list_results(patient_id: int, _p: auth.Principal = Depends(auth.admin_principal), db: Session = Depends(get_db)):
    """The patient's lab results and imaging reports, newest first."""
    return [result_out(db, r) for r in results_of(db, _patient(db, patient_id))]


@admin_router.post("/results", response_model=schemas.ResultOut, status_code=201)
def create_result(
    patient_id: int,
    body: schemas.ResultIn,
    p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    patient = _patient(db, patient_id)
    row = add_result(db, patient, body, entered_by=p.account.id)
    accounts.audit(db, p.account, "patient.result.add", None, patient=patient.mrn, kind=row.kind, result=row.id)
    db.commit()
    return result_out(db, row)


@admin_router.patch("/results/{result_id}", response_model=schemas.ResultOut)
def update_result(
    patient_id: int,
    result_id: int,
    body: schemas.ResultPatch,
    p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    patient = _patient(db, patient_id)
    row = db.get(models.PatientResult, result_id)
    if row is None or row.patient_id != patient.id:
        raise HTTPException(status_code=404, detail="Result not found.")
    fields = body.model_fields_set
    for key in ("name", "status", "resulted_on", "performed_by", "summary"):
        if key in fields and (getattr(body, key) is not None or key not in ("name", "status")):
            setattr(row, key, getattr(body, key))
    if "values" in fields and body.values is not None:
        if row.kind == "imaging" and body.values:
            raise HTTPException(status_code=422, detail="values: an imaging report has an impression, not values")
        row.lab_values = json.dumps([v.model_dump() for v in body.values])
    accounts.audit(
        db, p.account, "patient.result.update", None, patient=patient.mrn, result=row.id, fields=sorted(fields)
    )
    db.commit()
    return result_out(db, row)


@admin_router.get("/referrals", response_model=list[schemas.ReferralOut])
def list_referrals(patient_id: int, _p: auth.Principal = Depends(auth.admin_principal), db: Session = Depends(get_db)):
    """The patient's referral notes, newest first."""
    return [referrals.referral_out(db, r) for r in referrals_of(db, _patient(db, patient_id))]


@admin_router.post("/referrals", response_model=schemas.ReferralOut, status_code=201)
def create_referral(
    patient_id: int,
    body: schemas.ReferralIn,
    p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    patient = _patient(db, patient_id)
    row = add_referral(db, patient, body, entered_by=p.account.id)
    accounts.audit(db, p.account, "patient.referral.add", None, patient=patient.mrn, referral=row.id)
    db.commit()
    return referrals.referral_out(db, row)


@admin_router.patch("/referrals/{referral_id}", response_model=schemas.ReferralOut)
def update_referral(
    patient_id: int,
    referral_id: int,
    body: schemas.ReferralPatch,
    p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    patient = _patient(db, patient_id)
    row = db.get(models.PatientReferral, referral_id)
    if row is None or row.patient_id != patient.id:
        raise HTTPException(status_code=404, detail="Referral not found.")
    fields = body.model_fields_set
    if row.status == "withdrawn":
        raise HTTPException(status_code=409, detail="This referral was withdrawn by the provider who wrote it.")
    if "status" in fields and body.status is not None:
        row.status = body.status
    if "valid_until" in fields:
        if body.valid_until and body.valid_until < row.referral_date:
            raise HTTPException(status_code=422, detail="valid_until: must not be before the referral date")
        row.valid_until = body.valid_until
    if "note" in fields:
        row.note = body.note
    accounts.audit(
        db, p.account, "patient.referral.update", None, patient=patient.mrn, referral=row.id, fields=sorted(fields)
    )
    db.commit()
    return referrals.referral_out(db, row)
