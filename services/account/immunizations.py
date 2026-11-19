"""Immunizations — the vaccines in the patient's record.

    The office (administrators — the Clinical / Reports page of the patient on the office dashboard):
    GET   /api/v1/patients/{id}/immunizations         every entry, newest dose first (entered-in-error last)
    POST  /api/v1/patients/{id}/immunizations         enter a dose (given here, or reported by another clinic)
    PATCH /api/v1/patients/{id}/immunizations/{iid}   correct it, or mark it entered in error

The provider reads them on the patient's chart (clinical.chart → ``immunizations``); the immunization record
and the annual health examination report list them (patient_reports.py). An entry is never deleted: a wrong
one is marked ``entered_in_error`` (kept for the history, left out of every report).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

import accounts
import appointments
import auth
import models
import schemas
from database import get_db

log = logging.getLogger("account.immunizations")

router = APIRouter(prefix="/patients/{patient_id}/immunizations", tags=["patients"])


def of_patient(db: Session, patient: models.Patient, *, active_only: bool = False) -> list[models.PatientImmunization]:
    """Newest dose first; entries entered in error at the end."""
    stmt = select(models.PatientImmunization).where(models.PatientImmunization.patient_id == patient.id)
    if active_only:
        stmt = stmt.where(models.PatientImmunization.status == "active")
    rows = db.execute(stmt).scalars().all()
    return sorted(rows, key=lambda r: (r.status != "active", -r.date_given.toordinal(), -r.id))


def dose_label(row: models.PatientImmunization) -> str | None:
    if row.dose_number and row.series_doses:
        return f"{row.dose_number} of {row.series_doses}"
    if row.dose_number:
        return f"dose {row.dose_number}"
    return None


def given_by(db: Session, row: models.PatientImmunization) -> str | None:
    w = db.get(models.Worker, row.worker_id) if row.worker_id else None
    if w is not None:
        return appointments._provider_name(w)
    return row.administered_by


def product(row: models.PatientImmunization) -> str | None:
    parts = [row.manufacturer, f"lot {row.lot_number}" if row.lot_number else None]
    return " · ".join(p for p in parts if p) or None


def site_route(row: models.PatientImmunization) -> str | None:
    return " · ".join(p for p in (row.site, row.route) if p) or None


def line(db: Session, row: models.PatientImmunization) -> str:
    dose = dose_label(row)
    details = [
        f"{row.date_given:%m/%d/%Y}",
        (dose if dose and dose.startswith("dose") else f"dose {dose}") if dose else None,
        product(row),
        site_route(row),
        f"given by {given_by(db, row)}" if given_by(db, row) else None,
    ]
    return f"{row.vaccine} — " + " · ".join(d for d in details if d)


def out(db: Session, row: models.PatientImmunization) -> schemas.ImmunizationOut:
    return schemas.ImmunizationOut(
        id=row.id,
        vaccine=row.vaccine,
        cvx_code=row.cvx_code,
        date_given=row.date_given,
        dose_number=row.dose_number,
        series_doses=row.series_doses,
        dose_label=dose_label(row),
        manufacturer=row.manufacturer,
        lot_number=row.lot_number,
        site=row.site,
        route=row.route,
        administered_by=given_by(db, row),
        source=row.source,
        status=row.status,
        note=row.note,
        line=line(db, row),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def report_rows(db: Session, patient: models.Patient) -> list[schemas.ReportImmunization]:
    """The active entries as a report prints them (oldest first within each vaccine, vaccines A–Z)."""
    rows = sorted(of_patient(db, patient, active_only=True), key=lambda r: (r.vaccine.lower(), r.date_given, r.id))
    return [
        schemas.ReportImmunization(
            vaccine=r.vaccine,
            date_given=r.date_given,
            dose=dose_label(r),
            product=product(r),
            site_route=site_route(r),
            given_by=given_by(db, r),
        )
        for r in rows
    ]


def add(
    db: Session,
    patient: models.Patient,
    body: schemas.ImmunizationIn,
    *,
    entered_by: str | None,
    source: str | None = None,
) -> models.PatientImmunization:
    if body.worker_id is not None:
        w = db.get(models.Worker, body.worker_id)
        if w is None:
            raise HTTPException(status_code=422, detail="worker_id: no such member of staff")
    if patient.date_of_birth and body.date_given < patient.date_of_birth:
        raise HTTPException(status_code=422, detail="date_given: before the patient's date of birth")
    row = models.PatientImmunization(
        patient_id=patient.id,
        **body.model_dump(exclude={"source"}),
        source=source or body.source,
        entered_by=entered_by,
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


@router.get("", response_model=list[schemas.ImmunizationOut])
def list_immunizations(
    patient_id: int, _p: auth.Principal = Depends(auth.admin_principal), db: Session = Depends(get_db)
):
    return [out(db, r) for r in of_patient(db, _patient(db, patient_id))]


@router.post("", response_model=schemas.ImmunizationOut, status_code=201)
def create_immunization(
    patient_id: int,
    body: schemas.ImmunizationIn,
    p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    patient = _patient(db, patient_id)
    row = add(db, patient, body, entered_by=p.account.id)
    accounts.audit(db, p.account, "patient.immunization.add", None, patient=patient.mrn, vaccine=row.vaccine)
    db.commit()
    log.info("immunization %s entered for %s", row.vaccine, patient.mrn)
    return out(db, row)


@router.patch("/{immunization_id}", response_model=schemas.ImmunizationOut)
def update_immunization(
    patient_id: int,
    immunization_id: int,
    body: schemas.ImmunizationPatch,
    p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    patient = _patient(db, patient_id)
    row = db.get(models.PatientImmunization, immunization_id)
    if row is None or row.patient_id != patient.id:
        raise HTTPException(status_code=404, detail="Immunization not found.")
    changes = body.model_dump(exclude_unset=True)
    for key in ("vaccine", "date_given", "status"):
        if key in changes and changes[key] is None:
            changes.pop(key)  # required fields: null means "leave it"
    if "date_given" in changes and changes["date_given"] > models.utcnow().date():
        raise HTTPException(status_code=422, detail="date_given: cannot be in the future")
    for key, value in changes.items():
        setattr(row, key, value)
    if row.dose_number and row.series_doses and row.dose_number > row.series_doses:
        raise HTTPException(status_code=422, detail="dose_number: cannot be more than the doses of the series")
    accounts.audit(
        db,
        p.account,
        "patient.immunization.update",
        None,
        patient=patient.mrn,
        immunization=row.id,
        fields=sorted(changes),
    )
    db.commit()
    return out(db, row)
