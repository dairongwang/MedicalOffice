"""Medications, allergies and the medical history — the patient's lists on the chart.

    Administrators (the office administration dashboard, on the patient record):
    GET   /api/v1/patients/{patient_id}/health-items           both lists (current entries first)
    POST  /api/v1/patients/{patient_id}/health-items           add an entry {kind, name, detail, note}
    PATCH /api/v1/patients/{patient_id}/health-items/{id}      correct it, or mark it no longer current
                                                               ({status: "inactive", note: why})

The lists are kept current by the **medical assistant's review** of each visit (assistant_review.py):
the review starts from the active entries here plus what the patient wrote at check-in, and every
item the assistant clears updates the chart — confirmed entries are kept (corrected, re-dated),
removed ones become inactive with the note saying why, new ones are added. Nothing is ever deleted:
an inactive entry stays visible to the office as history.

``detail`` is free text: dose and how often for a medication ("10 mg once daily"), the reaction and
severity for an allergy ("rash"), since when / how it is managed for a condition ("since 2019 —
controlled"), the year of a surgery, the relative for a family history ("father"), and so on. The medical
history (kinds condition · surgery · family_history · social_history) is kept by the office and read by
the provider in the patient's chart (clinical.py); the assistant reviews medications and allergies.

The lists are clinical data of the office's patients — changed by administrators here and by the assigned
medical assistant through the review, read by the visit's provider in the chart; every office change
lands in the audit log.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

import accounts
import auth
import models
import patients
import schemas
from database import get_db

log = logging.getLogger("account.health_records")

router = APIRouter(prefix="/patients/{patient_id}/health-items", tags=["patients"])

MAX_ITEMS_PER_PATIENT = 200


def items_of(
    db: Session, patient: models.Patient, kind: str | None = None, *, active_only: bool = False
) -> list[models.PatientHealthItem]:
    """The patient's entries — active first, then by name."""
    stmt = select(models.PatientHealthItem).where(models.PatientHealthItem.patient_id == patient.id)
    if kind:
        stmt = stmt.where(models.PatientHealthItem.kind == kind)
    if active_only:
        stmt = stmt.where(models.PatientHealthItem.status == "active")
    rows = db.execute(stmt).scalars().all()
    return sorted(rows, key=lambda r: (r.status != "active", r.name.lower(), r.id))


def item_out(db: Session, row: models.PatientHealthItem) -> schemas.HealthItemOut:
    reviewer = db.get(models.Worker, row.last_reviewed_by) if row.last_reviewed_by else None
    return schemas.HealthItemOut(
        id=row.id,
        kind=row.kind,
        name=row.name,
        detail=row.detail,
        status=row.status,
        source=row.source,
        note=row.note,
        last_reviewed_at=row.last_reviewed_at,
        last_reviewed_by=f"{reviewer.first_name} {reviewer.last_name}" if reviewer else None,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def lists_out(db: Session, patient: models.Patient) -> dict[str, list[schemas.HealthItemOut]]:
    """{"medications": [...], "allergies": [...], "medical_history": [...]} — what patients.full() and the
    provider's chart put on the record (the history: conditions, surgeries, family / social history)."""
    rows = items_of(db, patient)
    return {
        "medications": [item_out(db, r) for r in rows if r.kind == "medication"],
        "allergies": [item_out(db, r) for r in rows if r.kind == "allergy"],
        "medical_history": [
            item_out(db, r)
            for r in sorted(
                rows, key=lambda r: models.HISTORY_KINDS.index(r.kind) if r.kind in models.HISTORY_KINDS else 9
            )
            if r.kind in models.HISTORY_KINDS
        ],
    }


def add(
    db: Session,
    patient: models.Patient,
    kind: str,
    name: str,
    detail: str | None,
    *,
    source: str,
    note: str | None = None,
) -> models.PatientHealthItem:
    row = models.PatientHealthItem(
        patient_id=patient.id, kind=kind, name=name, detail=detail, status="active", source=source, note=note
    )
    db.add(row)
    db.flush()
    return row


# ---------------------------------------------------------------------------- routes (administrators)


def _patient(db: Session, patient_id: int) -> models.Patient:
    p = db.get(models.Patient, patient_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Patient not found.")
    return p


@router.get("", response_model=list[schemas.HealthItemOut])
def list_items(patient_id: int, _p: auth.Principal = Depends(auth.admin_principal), db: Session = Depends(get_db)):
    """The patient's medications and allergies — current entries first, then the ones no longer current."""
    return [item_out(db, r) for r in items_of(db, _patient(db, patient_id))]


@router.post("", response_model=schemas.HealthItemOut, status_code=201)
def add_item(
    patient_id: int,
    body: schemas.HealthItemIn,
    p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    patient = _patient(db, patient_id)
    if len(items_of(db, patient)) >= MAX_ITEMS_PER_PATIENT:
        raise HTTPException(status_code=409, detail="This patient's lists are full — mark old entries inactive.")
    same = patients.normalize_name(body.name)
    if any(patients.normalize_name(r.name) == same for r in items_of(db, patient, body.kind, active_only=True)):
        raise HTTPException(status_code=409, detail=f"{body.name} is already on the {body.kind} list.")
    row = add(db, patient, body.kind, body.name, body.detail, source="office", note=body.note)
    accounts.audit(db, p.account, "patient.health_item.add", None, patient=patient.mrn, kind=body.kind, item=row.id)
    db.commit()
    log.info("%s added to %s's %s list by %s", body.name, patient.mrn, body.kind, p.account.email)
    return item_out(db, row)


@router.patch("/{item_id}", response_model=schemas.HealthItemOut)
def update_item(
    patient_id: int,
    item_id: int,
    body: schemas.HealthItemPatch,
    p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    patient = _patient(db, patient_id)
    row = db.get(models.PatientHealthItem, item_id)
    if row is None or row.patient_id != patient.id:
        raise HTTPException(status_code=404, detail="Entry not found.")
    if body.status == "inactive" and row.status == "active" and not body.note:
        raise HTTPException(status_code=422, detail="note: please say why the entry is no longer current")
    changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if getattr(row, k) != v}
    for key, value in changes.items():
        setattr(row, key, value)
    if changes:
        accounts.audit(
            db, p.account, "patient.health_item.update", None, patient=patient.mrn, item=row.id, fields=sorted(changes)
        )
    db.commit()
    return item_out(db, row)
