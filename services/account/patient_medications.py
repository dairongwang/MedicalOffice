"""My medicines — the patients' app's page (/patients/medicines, next to My reports), one tab per patient
record: the prescription medicines to take, with the dosing, the directions and the prescriber's instructions.

    Patients (signed in with the patient account — one tab per record on the account, e.g. a parent and a child):
    GET /api/v1/me/medications      every record of the account: allergies · the medicines to take now ·
                                    the ones stopped · the prescriptions issued at the office (history) ·
                                    the patient's pharmacy

Where it comes from:

* the **medication list** of the chart (``patient_health_items``, kind ``medication``) says *what* the patient
  takes — kept current by the office, the medical assistant's review of every visit and the prescriptions
  (prescriptions.py puts every prescribed medicine on the list); an entry that is no longer current
  (``inactive``) is shown under "No longer taking" with the note saying why;
* the latest **prescription** (not voided) of that medicine says *how*: the strength and the form, the
  directions (the sig — "Take 1 tablet by mouth once daily"), the **instructions for the patient** the
  prescriber wrote ("Take it with food …" — ``prescription_items.patient_instructions``), what it is for
  (the prescription's diagnosis), the prescriber, the date, the quantity and the refills, the pharmacy it was
  sent to. A medicine on the list that was not prescribed here (another doctor's, over the counter) shows
  what the list says.

A voided prescription (a mistake, voided by its prescriber) is left out everywhere — and so is the list entry
it added. Only the records linked to the signed-in account are read (appointments.my_records); nothing here
changes the chart — changes go through the office or the next visit.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

import appointments
import auth
import config
import health_records
import models
import prescriptions
import schemas
from database import get_db

log = logging.getLogger("account.patient_medications")

me_router = APIRouter(prefix="/me/medications", tags=["me"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sent_to(db: Session, rx: models.Prescription) -> str | None:
    """The pharmacy the prescription was last e-mailed to (successfully)."""
    for d in reversed(prescriptions.deliveries_of(db, rx)):
        if d.method == "email" and d.status == "sent" and d.pharmacy_id is not None:
            pharmacy = db.get(models.Pharmacy, d.pharmacy_id)
            if pharmacy is not None:
                return pharmacy.name
    return None


def _indication(db: Session, rx: models.Prescription) -> str | None:
    d = db.get(models.VisitDiagnosis, rx.diagnosis_id) if rx.diagnosis_id else None
    return d.description if d else None


def _prescribed_entry(
    db: Session,
    rx: models.Prescription,
    item: models.PrescriptionItem,
    *,
    status: str,
    note: str | None,
    updated_at: datetime | None,
) -> schemas.MedicationEntry:
    return schemas.MedicationEntry(
        name=item.drug,
        strength=item.strength,
        form=item.form if item.form != "other" else None,
        line=prescriptions.item_line(item),
        directions=item.sig,
        patient_instructions=item.patient_instructions,
        indication=_indication(db, rx),
        status=status,
        prescribed=True,
        prescription_id=rx.id,
        prescription_number=rx.number,
        prescribed_on=rx.issued_at,
        prescriber_name=prescriptions._worker_name(db, rx.prescriber_id),
        quantity=item.quantity,
        quantity_unit=item.quantity_unit,
        refills=item.refills,
        sent_to_pharmacy=_sent_to(db, rx),
        note=note,
        updated_at=updated_at,
    )


def medications_of(db: Session, patient: models.Patient) -> schemas.PatientMedications:
    """One record's tab (see the module docstring)."""
    issued: list[tuple[models.Prescription, models.PrescriptionItem]] = []
    from_voided: set[int] = set()  # list entries a voided prescription added
    for rx in prescriptions.prescriptions_of(db, patient):  # newest first
        for item in prescriptions.items_of(db, rx):
            if rx.status == "voided":
                if item.added_health_item and item.health_item_id:
                    from_voided.add(item.health_item_id)
                continue
            issued.append((rx, item))
    by_entry: dict[int, tuple[models.Prescription, models.PrescriptionItem]] = {}
    by_name: dict[str, tuple[models.Prescription, models.PrescriptionItem]] = {}
    for rx, item in issued:
        if item.health_item_id is not None:
            by_entry.setdefault(item.health_item_id, (rx, item))
        by_name.setdefault(prescriptions._key(item.drug), (rx, item))

    current: list[schemas.MedicationEntry] = []
    stopped: list[schemas.MedicationEntry] = []
    reviewed: list[datetime] = []
    for entry in health_records.items_of(db, patient, "medication"):
        active = entry.status == "active"
        if not active and entry.id in from_voided:
            continue
        if entry.last_reviewed_at is not None:
            reviewed.append(appointments.as_utc(entry.last_reviewed_at))
        match = by_entry.get(entry.id) or by_name.get(prescriptions._key(entry.name))
        status = "current" if active else "stopped"
        if match is not None:
            rx, item = match
            out = _prescribed_entry(
                db, rx, item, status=status, note=None if active else entry.note, updated_at=entry.updated_at
            )
        else:
            out = schemas.MedicationEntry(
                name=entry.name,
                line=entry.name,
                directions=entry.detail,
                status=status,
                prescribed=False,
                note=entry.note,
                updated_at=entry.updated_at,
            )
        (current if active else stopped).append(out)

    history = [
        schemas.MedicationHistoryEntry(
            prescription_id=rx.id,
            prescription_number=rx.number,
            prescribed_on=rx.issued_at,
            prescriber_name=prescriptions._worker_name(db, rx.prescriber_id),
            line=prescriptions.item_line(item),
            directions=item.sig,
            patient_instructions=item.patient_instructions,
            quantity=item.quantity,
            quantity_unit=item.quantity_unit,
            refills=item.refills,
            indication=_indication(db, rx),
        )
        for rx, item in issued
    ]
    allergies = [
        schemas.MyMedicationAllergy(name=a.name, detail=a.detail)
        for a in health_records.items_of(db, patient, "allergy", active_only=True)
    ]
    pharmacy = db.get(models.Pharmacy, patient.preferred_pharmacy_id) if patient.preferred_pharmacy_id else None
    return schemas.PatientMedications(
        patient_id=patient.id,
        patient_name=f"{patient.first_name} {patient.last_name}",
        first_name=patient.first_name,
        mrn=patient.mrn,
        date_of_birth=patient.date_of_birth,
        allergies=allergies,
        current=current,
        stopped=stopped,
        history=history,
        pharmacy_name=pharmacy.name if pharmacy and pharmacy.active else None,
        pharmacy_phone=pharmacy.phone if pharmacy and pharmacy.active else None,
        last_reviewed_at=max(reviewed) if reviewed else None,
    )


@me_router.get("", response_model=schemas.MyMedications)
def my_medications(p: auth.Principal = Depends(auth.patient_principal), db: Session = Depends(get_db)):
    """The medicines of every patient record on the account — one tab each on My medicines."""
    records = appointments.my_records(db, p.account)
    db.commit()  # my_records may have linked a record registered with the account's e-mail
    return schemas.MyMedications(
        records=[medications_of(db, r) for r in records],
        office_name=config.OFFICE_NAME,
        office_phone=config.OFFICE_PHONE or None,
        generated_at=_now(),
    )
