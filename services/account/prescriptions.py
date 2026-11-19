"""Diagnoses and prescriptions — written by the provider on the patient's chart (staff dashboard).

    The visit's provider — a doctor or physician assistant (models.PRESCRIBER_ROLES):
    POST   /api/v1/staff/me/appointments/{id}/diagnoses             record a diagnosis {description, code
                                                                    (ICD-10), primary, note, add_to_history}
    DELETE /api/v1/staff/me/appointments/{id}/diagnoses/{did}       take it back
    POST   /api/v1/staff/me/appointments/{id}/prescriptions         sign & issue {items, diagnosis_id,
                                                                    note_to_pharmacist, pharmacy_id (e-mail it
                                                                    now), allergy_override_reason}
    GET    /api/v1/staff/me/appointments/{id}/prescriptions/{rx}/pdf    the prescription as a PDF
    POST   /api/v1/staff/me/appointments/{id}/prescriptions/{rx}/send   e-mail it to a pharmacy (PDF attached)
    POST   /api/v1/staff/me/appointments/{id}/prescriptions/{rx}/void   void it {reason} (the prescriber)

    The visit's provider (any role) — the pharmacy the patient uses, as the patient tells it:
    PUT    /api/v1/staff/me/appointments/{id}/pharmacy              {pharmacy_id} from the directory, or
                                                                    {pharmacy: {name, email, …}} a new one
                                                                    (added to the directory), or {} = none
    PATCH  /api/v1/staff/me/appointments/{id}/pharmacy              update its details {email, phone, fax,
                                                                    address} (what the patient says)

    The office (administrators):
    GET/POST /api/v1/pharmacies, PATCH /api/v1/pharmacies/{id}      the pharmacy directory
    GET/PUT  /api/v1/patients/{id}/pharmacy                         the patient's pharmacy
    GET      /api/v1/patients/{id}/prescriptions (+ /{rx}/pdf)      to print one for the patient

**The patient's pharmacy** (Patient.preferred_pharmacy_id) is where prescriptions go by default: the new
prescription's "Send to" starts on it (when it takes e-mail). The provider asks the patient at the visit
and confirms, changes or adds it — a pharmacy that is not in the directory yet is added (source
"provider", with who added it, for the office to review); one that is already there (same name and
address, or same e-mail) is reused, its blanks filled in.

**Issuing** signs the prescription electronically (the signed-in prescriber, the time, a verification
code printed on it); it is never edited afterwards — a mistake is *voided* (with the reason; a pharmacy
it was e-mailed to gets a "do not fill" notice with the voided PDF) and a new one written. Each
medication goes on the patient's medication list (health_records.py — updated when it is already there).

**Safety checks** before issuing: only a doctor or physician assistant may prescribe; a medication that
matches one of the patient's active allergies (by name, or by a few well-known classes — penicillins,
sulfonamides, NSAIDs, …; *not* a drug-interaction database) needs an override reason; controlled
substances are refused — they need certified electronic prescribing (EPCS), which this office does not do.

**Hand-over** "as needed": the PDF (printed / given to the patient, or downloaded by the office) and / or
an e-mail to a pharmacy with the PDF attached (messaging.py — the test inbox in development). Every
hand-over is kept (PrescriptionDelivery) and audited. Outside production every PDF carries a
"SAMPLE — NOT A VALID PRESCRIPTION" banner: the data is dummy data.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

import accounts
import appointments
import auth
import config
import health_records
import messaging
import models
import patients
import pdf
import schemas
import staff_accounts
import twofactor
from database import get_db

log = logging.getLogger("account.prescriptions")

staff_router = APIRouter(prefix="/staff/me/appointments", tags=["staff"])
admin_router = APIRouter(tags=["patients"])

MAX_DIAGNOSES_PER_VISIT = 20

# The usual unit a quantity is counted in, per dosage form.
UNITS = {
    "tablet": "tablets",
    "capsule": "capsules",
    "oral solution": "mL",
    "oral suspension": "mL",
    "inhaler": "inhalers",
    "cream": "g",
    "ointment": "g",
    "drops": "mL",
    "patch": "patches",
    "injection": "doses",
    "other": "units",
}

# Allergy classes → members that cross-react (a safety net for the obvious cases — not a drug database).
ALLERGY_CLASSES = {
    "penicillin": (
        "penicillin",
        "amoxicillin",
        "ampicillin",
        "augmentin",
        "dicloxacillin",
        "nafcillin",
        "piperacillin",
    ),
    "sulfa": ("sulfamethoxazole", "bactrim", "sulfasalazine", "sulfadiazine"),
    "nsaid": ("ibuprofen", "naproxen", "diclofenac", "ketorolac", "celecoxib", "meloxicam", "aspirin"),
    "aspirin": ("aspirin", "ibuprofen", "naproxen", "diclofenac", "ketorolac"),
    "codeine": ("codeine", "hydrocodone", "oxycodone", "morphine"),
    "statin": ("atorvastatin", "simvastatin", "rosuvastatin", "pravastatin", "lovastatin"),
    "cephalosporin": ("cephalexin", "cefuroxime", "ceftriaxone", "cefdinir", "cefazolin"),
}
# Controlled substances (US schedules II–V, the common ones): refused — they need EPCS.
CONTROLLED = (
    "oxycodone",
    "hydrocodone",
    "morphine",
    "fentanyl",
    "methadone",
    "hydromorphone",
    "codeine",
    "tramadol",
    "tapentadol",
    "buprenorphine",
    "amphetamine",
    "dextroamphetamine",
    "lisdexamfetamine",
    "methylphenidate",
    "alprazolam",
    "lorazepam",
    "diazepam",
    "clonazepam",
    "temazepam",
    "zolpidem",
    "pregabalin",
    "testosterone",
    "phenobarbital",
    "ketamine",
    "cocaine",
    "carisoprodol",
    "modafinil",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _key(text: str | None) -> str:
    return patients.normalize_name(text or "")


def _worker_name(db: Session, worker_id: int | None) -> str | None:
    w = db.get(models.Worker, worker_id) if worker_id else None
    return appointments._provider_name(w) if w is not None else None


def can_prescribe(w: models.Worker | None) -> bool:
    return w is not None and w.active and w.role in models.PRESCRIBER_ROLES


def _require_prescriber(w: models.Worker) -> None:
    if not can_prescribe(w):
        raise HTTPException(
            status_code=403, detail="Only a doctor or a physician assistant can record diagnoses and prescribe."
        )


# ---------------------------------------------------------------------------- diagnoses


def diagnoses_of(db: Session, a: models.Appointment) -> list[models.VisitDiagnosis]:
    rows = db.execute(select(models.VisitDiagnosis).where(models.VisitDiagnosis.appointment_id == a.id)).scalars().all()
    return sorted(rows, key=lambda d: (not d.is_primary, d.id))


def diagnosis_out(db: Session, d: models.VisitDiagnosis) -> schemas.DiagnosisOut:
    a = db.get(models.Appointment, d.appointment_id)
    return schemas.DiagnosisOut(
        id=d.id,
        appointment_id=d.appointment_id,
        appointment_number=a.number if a else "",
        description=d.description,
        code=d.code,
        primary=d.is_primary,
        note=d.note,
        provider_name=_worker_name(db, d.worker_id),
        in_history=d.health_item_id is not None,
        created_at=d.created_at,
    )


def add_diagnosis(
    db: Session, a: models.Appointment, w: models.Worker, body: schemas.DiagnosisIn
) -> models.VisitDiagnosis:
    _require_prescriber(w)
    if a.status in ("canceled", "no_show"):
        raise HTTPException(status_code=409, detail=f"This visit is {a.status.replace('_', ' ')}.")
    existing = diagnoses_of(db, a)
    if len(existing) >= MAX_DIAGNOSES_PER_VISIT:
        raise HTTPException(status_code=409, detail="This visit already has the maximum number of diagnoses.")
    if any(_key(d.description) == _key(body.description) and d.code == body.code for d in existing):
        raise HTTPException(status_code=409, detail=f"“{body.description}” is already a diagnosis of this visit.")
    primary = body.primary or not existing  # the first one is the primary one
    if primary:
        for d in existing:
            d.is_primary = False
    d = models.VisitDiagnosis(
        appointment_id=a.id,
        patient_id=a.patient_id,
        worker_id=w.id,
        description=body.description,
        code=body.code,
        is_primary=primary,
        note=body.note,
    )
    if body.add_to_history:
        patient = db.get(models.Patient, a.patient_id)
        entry = next(
            (
                e
                for e in health_records.items_of(db, patient, "condition", active_only=True)
                if _key(e.name) == _key(body.description)
            ),
            None,
        )
        if entry is None:
            detail = " · ".join(x for x in (body.code, f"diagnosed {a.number}") if x)
            entry = health_records.add(db, patient, "condition", body.description, detail, source="provider")
        d.health_item_id = entry.id
    db.add(d)
    db.flush()
    return d


def remove_diagnosis(db: Session, a: models.Appointment, w: models.Worker, diagnosis_id: int) -> None:
    _require_prescriber(w)
    d = db.get(models.VisitDiagnosis, diagnosis_id)
    if d is None or d.appointment_id != a.id:
        raise HTTPException(status_code=404, detail="Diagnosis not found.")
    used = db.execute(select(models.Prescription.id).where(models.Prescription.diagnosis_id == d.id)).first()
    if used:
        raise HTTPException(status_code=409, detail="A prescription names this diagnosis — void it first.")
    referred = db.execute(select(models.PatientReferral.id).where(models.PatientReferral.diagnosis_id == d.id)).first()
    if referred:
        raise HTTPException(status_code=409, detail="A referral letter names this diagnosis — it cannot be removed.")
    entry = db.get(models.PatientHealthItem, d.health_item_id) if d.health_item_id else None
    others = db.execute(
        select(models.VisitDiagnosis.id).where(
            models.VisitDiagnosis.health_item_id == d.health_item_id, models.VisitDiagnosis.id != d.id
        )
    ).first()
    if entry is not None and entry.source == "provider" and entry.status == "active" and not others:
        entry.status, entry.note = "inactive", f"Diagnosis removed at {a.number}."
    was_primary = d.is_primary
    db.delete(d)
    db.flush()
    rest = diagnoses_of(db, a)
    if was_primary and rest:
        rest[0].is_primary = True
    db.flush()


# ---------------------------------------------------------------------------- prescriptions: checks


def controlled_substances(items: list[schemas.PrescriptionItemIn]) -> list[str]:
    out = []
    for item in items:
        drug = _key(item.drug)
        if any(c in drug for c in CONTROLLED):
            out.append(item.drug)
    return out


def allergy_conflicts(
    db: Session, patient: models.Patient, items: list[schemas.PrescriptionItemIn]
) -> list[tuple[str, str]]:
    """(medication, allergy) pairs: the drug names the allergy, or belongs to a class the allergy names."""
    allergies = health_records.items_of(db, patient, "allergy", active_only=True)
    out = []
    for item in items:
        drug = _key(item.drug)
        for allergy in allergies:
            name = _key(allergy.name)
            if not name:
                continue
            related = any(cls in name and any(m in drug for m in members) for cls, members in ALLERGY_CLASSES.items())
            if name in drug or drug in name or related:
                out.append((item.drug, allergy.name))
    return out


# ---------------------------------------------------------------------------- prescriptions: issuing


def _verification(rx: models.Prescription) -> str:
    msg = f"rx:{rx.id}:{rx.patient_id}:{rx.issued_at.isoformat()}".encode()
    return hmac.new(config.TOKEN_SECRET.encode("utf-8"), msg, hashlib.sha256).hexdigest()[:10].upper()


def items_of(db: Session, rx: models.Prescription) -> list[models.PrescriptionItem]:
    return (
        db.execute(
            select(models.PrescriptionItem)
            .where(models.PrescriptionItem.prescription_id == rx.id)
            .order_by(models.PrescriptionItem.position)
        )
        .scalars()
        .all()
    )


def item_line(i: models.PrescriptionItem) -> str:
    return " ".join(x for x in (i.drug, i.strength, i.form if i.form != "other" else "") if x)


def issue(
    db: Session, a: models.Appointment, w: models.Worker, body: schemas.PrescriptionIn, actor: models.Account
) -> models.Prescription:
    """Sign and issue — after the checks (see the module docstring). Caller commits."""
    _require_prescriber(w)
    if a.status in ("canceled", "no_show"):
        raise HTTPException(status_code=409, detail=f"This visit is {a.status.replace('_', ' ')}.")
    patient = db.get(models.Patient, a.patient_id)
    controlled = controlled_substances(body.items)
    if controlled:
        raise HTTPException(
            status_code=422,
            detail=f"{', '.join(controlled)}: controlled substances need certified electronic prescribing (EPCS), "
            "which this office does not do — please use the practice's EPCS system.",
        )
    conflicts = allergy_conflicts(db, patient, body.items)
    if conflicts and not body.allergy_override_reason:
        raise HTTPException(
            status_code=409,
            detail="Allergy warning: "
            + "; ".join(f"{drug} — the patient is allergic to {allergy}" for drug, allergy in conflicts)
            + ". To prescribe anyway, give the reason (allergy_override_reason).",
        )
    diagnosis = None
    if body.diagnosis_id is not None:
        diagnosis = db.get(models.VisitDiagnosis, body.diagnosis_id)
        if diagnosis is None or diagnosis.appointment_id != a.id:
            raise HTTPException(status_code=422, detail="diagnosis_id: not a diagnosis of this visit")
    pharmacy = _pharmacy_for_sending(db, body.pharmacy_id) if body.pharmacy_id is not None else None
    now = _now()
    rx = models.Prescription(
        patient_id=patient.id,
        appointment_id=a.id,
        prescriber_id=w.id,
        diagnosis_id=diagnosis.id if diagnosis else None,
        status="issued",
        note_to_pharmacist=body.note_to_pharmacist,
        allergy_override=body.allergy_override_reason if conflicts else None,
        issued_at=now,
    )
    db.add(rx)
    db.flush()
    rx.verification = _verification(rx)
    for position, item in enumerate(body.items, start=1):
        row = models.PrescriptionItem(
            prescription_id=rx.id,
            position=position,
            drug=item.drug,
            strength=item.strength,
            form=item.form,
            sig=item.sig,
            quantity=item.quantity,
            quantity_unit=item.quantity_unit or UNITS.get(item.form, "units"),
            refills=item.refills,
            substitution_allowed=item.substitution_allowed,
            patient_instructions=item.patient_instructions,
        )
        db.add(row)
        _onto_medication_list(db, patient, rx, row, w)
    db.flush()
    accounts.audit(
        db,
        actor,
        "prescription.issue",
        None,
        patient=patient.mrn,
        prescription=rx.number,
        items=len(body.items),
        allergy_override=bool(rx.allergy_override),
    )
    log.info("%s issued for %s by %s (%d item(s))", rx.number, patient.mrn, w.staff_number, len(body.items))
    if pharmacy is not None:
        send(db, rx, pharmacy, actor)
    return rx


def _onto_medication_list(
    db: Session, patient: models.Patient, rx: models.Prescription, item: models.PrescriptionItem, w: models.Worker
) -> None:
    detail = " — ".join(x for x in (" ".join(y for y in (item.strength, item.form) if y), item.sig) if x)[:300]
    note = f"Prescribed {rx.number} by {appointments._provider_name(w)}"
    entry = next(
        (
            e
            for e in health_records.items_of(db, patient, "medication", active_only=True)
            if _key(e.name) == _key(item.drug)
        ),
        None,
    )
    if entry is None:
        entry = health_records.add(db, patient, "medication", item.drug, detail, source="provider", note=note)
        item.added_health_item = True
    else:
        entry.detail, entry.note = detail, note
    item.health_item_id = entry.id


def void(db: Session, rx: models.Prescription, w: models.Worker, reason: str, actor: models.Account) -> None:
    """Void an issued prescription (only its prescriber): medications it put on the list come off again,
    and each pharmacy it was e-mailed to gets a "do not fill" notice with the voided PDF. Caller commits."""
    if rx.status != "issued":
        raise HTTPException(status_code=409, detail="This prescription is already voided.")
    if rx.prescriber_id != w.id:
        raise HTTPException(status_code=403, detail="Only the prescriber can void this prescription.")
    rx.status, rx.voided_at, rx.void_reason = "voided", _now(), reason
    for item in items_of(db, rx):
        entry = db.get(models.PatientHealthItem, item.health_item_id) if item.health_item_id else None
        if item.added_health_item and entry is not None and entry.status == "active":
            entry.status, entry.note = "inactive", f"{rx.number} voided: {reason}"
    db.flush()
    sent_to = {
        d.pharmacy_id
        for d in deliveries_of(db, rx)
        if d.method == "email" and d.status == "sent" and d.pharmacy_id is not None
    }
    for pharmacy_id in sorted(sent_to):
        pharmacy = db.get(models.Pharmacy, pharmacy_id)
        if pharmacy is not None and pharmacy.email:
            send(db, rx, pharmacy, actor, void_notice=True)
    accounts.audit(db, actor, "prescription.void", None, prescription=rx.number, reason=reason)


# ---------------------------------------------------------------------------- hand-over


def deliveries_of(db: Session, rx: models.Prescription) -> list[models.PrescriptionDelivery]:
    return (
        db.execute(
            select(models.PrescriptionDelivery)
            .where(models.PrescriptionDelivery.prescription_id == rx.id)
            .order_by(models.PrescriptionDelivery.id)
        )
        .scalars()
        .all()
    )


def _pharmacy_for_sending(db: Session, pharmacy_id: int) -> models.Pharmacy:
    pharmacy = db.get(models.Pharmacy, pharmacy_id)
    if pharmacy is None or not pharmacy.active:
        raise HTTPException(status_code=422, detail="pharmacy_id: no such pharmacy in the directory")
    if not pharmacy.email:
        raise HTTPException(
            status_code=422, detail=f"{pharmacy.name} has no e-mail address — print the PDF or call the pharmacy."
        )
    return pharmacy


def send(
    db: Session,
    rx: models.Prescription,
    pharmacy: models.Pharmacy,
    actor: models.Account | None,
    *,
    void_notice: bool = False,
) -> models.PrescriptionDelivery:
    """E-mail the prescription (the PDF attached) to the pharmacy; the outcome is recorded either way."""
    if rx.status == "voided" and not void_notice:
        raise HTTPException(status_code=409, detail="This prescription is voided — it cannot be sent.")
    patient = db.get(models.Patient, rx.patient_id)
    prescriber = _worker_name(db, rx.prescriber_id) or "the prescriber"
    dob = f"{patient.date_of_birth:%m/%d/%Y}"
    lines = [f"Dear {pharmacy.name},", ""]
    if void_notice:
        subject = f"VOID — do not fill: prescription {rx.number} for {patient.first_name} {patient.last_name}"
        lines += [
            f"prescription {rx.number} for {patient.first_name} {patient.last_name} (date of birth {dob}), sent to",
            f"you earlier, has been VOIDED by {prescriber}. Please do not fill it.",
            f"Reason: {rx.void_reason}",
        ]
    else:
        subject = f"Prescription {rx.number} for {patient.first_name} {patient.last_name} ({dob})"
        lines += [
            f"please find attached prescription {rx.number} for {patient.first_name} {patient.last_name}",
            f"(date of birth {dob}), issued by {prescriber} on {rx.issued_at:%b %d, %Y}:",
            "",
            *[
                f"  • {item_line(i)} — {i.sig} — quantity {i.quantity} {i.quantity_unit}, refills {i.refills}"
                for i in items_of(db, rx)
            ],
        ]
    lines += [
        "",
        f"Verification code: {rx.number}-{rx.verification}. Questions: {config.OFFICE_NAME}"
        + (f", {config.OFFICE_PHONE}" if config.OFFICE_PHONE else "")
        + ".",
    ]
    attachment = messaging.Attachment(
        filename=f"{rx.number}{'-VOID' if void_notice else ''}.pdf",
        content_type="application/pdf",
        data=render_pdf(db, rx),
    )
    channel = messaging.channel(messaging.EMAIL)
    delivery = models.PrescriptionDelivery(
        prescription_id=rx.id,
        method="email",
        pharmacy_id=pharmacy.id,
        destination=twofactor.mask_email(pharmacy.email),
        by_account=actor.id if actor else None,
    )
    try:
        if not channel.available:
            raise messaging.MessagingError("e-mail is not set up on this server")
        channel.send(pharmacy.email, "\n".join(lines), subject=subject, attachments=[attachment])
        delivery.status = "sent"
        notes = ["void notice"] if void_notice else []
        if channel.fake:
            notes.append(f"built-in fake — read it at {messaging.OUTBOX_PATH}")
        delivery.note = "; ".join(notes) or None
    except messaging.MessagingError as exc:
        delivery.status, delivery.note = "failed", str(exc)[:500]
    db.add(delivery)
    db.flush()
    accounts.audit(
        db, actor, "prescription.send", None, prescription=rx.number, pharmacy=pharmacy.name, status=delivery.status
    )
    log.info("%s e-mailed to %s: %s", rx.number, pharmacy.name, delivery.status)
    return delivery


def record_download(db: Session, rx: models.Prescription, actor: models.Account) -> None:
    db.add(models.PrescriptionDelivery(prescription_id=rx.id, method="pdf", status="downloaded", by_account=actor.id))
    accounts.audit(db, actor, "prescription.pdf", None, prescription=rx.number)


# ---------------------------------------------------------------------------- output


def prescription_out(db: Session, rx: models.Prescription) -> schemas.PrescriptionOut:
    a = db.get(models.Appointment, rx.appointment_id) if rx.appointment_id else None
    d = db.get(models.VisitDiagnosis, rx.diagnosis_id) if rx.diagnosis_id else None
    deliveries = []
    for x in deliveries_of(db, rx):
        pharmacy = db.get(models.Pharmacy, x.pharmacy_id) if x.pharmacy_id else None
        by = db.get(models.Account, x.by_account) if x.by_account else None
        deliveries.append(
            schemas.PrescriptionDeliveryOut(
                method=x.method,
                pharmacy_id=x.pharmacy_id,
                pharmacy_name=pharmacy.name if pharmacy else None,
                destination=x.destination,
                status=x.status,
                note=x.note,
                by=by.email if by else None,
                created_at=x.created_at,
            )
        )
    return schemas.PrescriptionOut(
        id=rx.id,
        number=rx.number,
        status=rx.status,
        issued_at=rx.issued_at,
        prescriber_id=rx.prescriber_id,
        prescriber_name=_worker_name(db, rx.prescriber_id),
        patient_id=rx.patient_id,
        appointment_id=rx.appointment_id,
        appointment_number=a.number if a else None,
        diagnosis=diagnosis_out(db, d) if d else None,
        items=[
            schemas.PrescriptionItemOut(
                drug=i.drug,
                strength=i.strength,
                form=i.form,
                sig=i.sig,
                quantity=i.quantity,
                quantity_unit=i.quantity_unit,
                refills=i.refills,
                substitution_allowed=i.substitution_allowed,
                line=item_line(i),
                patient_instructions=i.patient_instructions,
            )
            for i in items_of(db, rx)
        ],
        note_to_pharmacist=rx.note_to_pharmacist,
        allergy_override=rx.allergy_override,
        voided_at=rx.voided_at,
        void_reason=rx.void_reason,
        verification=f"{rx.number}-{rx.verification}",
        deliveries=deliveries,
    )


def prescriptions_of(db: Session, patient: models.Patient) -> list[models.Prescription]:
    return (
        db.execute(
            select(models.Prescription)
            .where(models.Prescription.patient_id == patient.id)
            .order_by(models.Prescription.issued_at.desc(), models.Prescription.id.desc())
        )
        .scalars()
        .all()
    )


def pharmacy_out(p: models.Pharmacy) -> schemas.PharmacyOut:
    return schemas.PharmacyOut(
        id=p.id,
        name=p.name,
        email=p.email,
        phone=p.phone,
        fax=p.fax,
        address=p.address,
        active=p.active,
        source=p.source or "office",
        added_by=p.added_by,
    )


# ---------------------------------------------------------------------------- the patient's pharmacy


def patient_pharmacy_out(db: Session, patient: models.Patient) -> schemas.PatientPharmacyOut:
    pharmacy = db.get(models.Pharmacy, patient.preferred_pharmacy_id) if patient.preferred_pharmacy_id else None
    return schemas.PatientPharmacyOut(
        pharmacy=pharmacy_out(pharmacy) if pharmacy else None,
        set_at=patient.preferred_pharmacy_set_at,
        set_by=patient.preferred_pharmacy_set_by,
    )


def _email_key(email: str | None) -> str:
    return (email or "").strip().lower()


def same_pharmacy(db: Session, body: schemas.PharmacyIn) -> models.Pharmacy | None:
    """The directory's entry for this pharmacy, if there is one: the same name and address (a chain has
    many stores under one name), or the same e-mail address."""
    name, address, email = _key(body.name), _key(body.address), _email_key(body.email)
    for p in db.execute(select(models.Pharmacy).order_by(models.Pharmacy.id)).scalars():
        if email and _email_key(p.email) == email:
            return p
        if _key(p.name) == name and _key(p.address) == address:
            return p
    return None


def set_patient_pharmacy(
    db: Session, patient: models.Patient, body: schemas.PatientPharmacyIn, *, by: str, source: str
) -> models.Pharmacy | None:
    """Choose (or add) the pharmacy the patient uses — none when neither is given. Caller commits."""
    pharmacy = None
    if body.pharmacy is not None:
        pharmacy = same_pharmacy(db, body.pharmacy)
        if pharmacy is not None:
            if not pharmacy.active:
                raise HTTPException(
                    status_code=409,
                    detail=f"{pharmacy.name} is in the directory but inactive — the office can reactivate it.",
                )
            for key, value in body.pharmacy.model_dump(mode="json").items():
                if value and not getattr(pharmacy, key):  # fill in what the directory did not know
                    setattr(pharmacy, key, value)
        else:
            pharmacy = models.Pharmacy(**body.pharmacy.model_dump(mode="json"), source=source, added_by=by)
            db.add(pharmacy)
            db.flush()
    elif body.pharmacy_id is not None:
        pharmacy = db.get(models.Pharmacy, body.pharmacy_id)
        if pharmacy is None or not pharmacy.active:
            raise HTTPException(status_code=422, detail="pharmacy_id: no such pharmacy in the directory")
    patient.preferred_pharmacy_id = pharmacy.id if pharmacy else None
    patient.preferred_pharmacy_set_at = _now()
    patient.preferred_pharmacy_set_by = by[:200]
    db.flush()
    return pharmacy


def update_patient_pharmacy(
    db: Session, patient: models.Patient, body: schemas.PharmacyDetailsPatch, *, by: str
) -> models.Pharmacy:
    """Correct the details of the patient's pharmacy (a new e-mail address, phone, …). Caller commits."""
    pharmacy = db.get(models.Pharmacy, patient.preferred_pharmacy_id) if patient.preferred_pharmacy_id else None
    if pharmacy is None:
        raise HTTPException(status_code=409, detail="The patient has no pharmacy on file — choose or add one first.")
    changes = body.model_dump(mode="json", exclude_unset=True)
    if changes.get("email"):
        other = next(
            (
                x
                for x in db.execute(select(models.Pharmacy).where(models.Pharmacy.id != pharmacy.id)).scalars()
                if _email_key(x.email) == _email_key(changes["email"])
            ),
            None,
        )
        if other is not None:
            raise HTTPException(status_code=409, detail=f"{changes['email']} belongs to {other.name} in the directory.")
    for key, value in changes.items():
        if key == "name" and not value:
            continue
        setattr(pharmacy, key, value)
    patient.preferred_pharmacy_set_at = _now()
    patient.preferred_pharmacy_set_by = by[:200]
    db.flush()
    return pharmacy


def active_pharmacies(db: Session) -> list[models.Pharmacy]:
    return (
        db.execute(select(models.Pharmacy).where(models.Pharmacy.active.is_(True)).order_by(models.Pharmacy.name))
        .scalars()
        .all()
    )


_ONES = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen".split()
)
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def number_words(n: int) -> str:
    """30 → "thirty" — quantities are written out on a prescription so they cannot be altered."""
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + (f"-{_ONES[n % 10]}" if n % 10 else "")
    if n < 1000:
        rest = n % 100
        return f"{_ONES[n // 100]} hundred" + (f" {number_words(rest)}" if rest else "")
    rest = n % 1000
    return f"{number_words(n // 1000)} thousand" + (f" {number_words(rest)}" if rest else "")


def render_pdf(db: Session, rx: models.Prescription) -> bytes:
    """The prescription as a one-page (or longer) Letter PDF."""
    patient = db.get(models.Patient, rx.patient_id)
    prescriber = db.get(models.Worker, rx.prescriber_id) if rx.prescriber_id else None
    d = db.get(models.VisitDiagnosis, rx.diagnosis_id) if rx.diagnosis_id else None
    tz = appointments.office_tz()
    issued = appointments.as_utc(rx.issued_at).astimezone(tz)
    doc = pdf.PdfDocument(title=f"Prescription {rx.number}", author=config.OFFICE_NAME, margin=72)
    left, right, width = 54, 558, 504
    y = 738

    def header() -> float:
        doc.text(left, 750, f"{config.OFFICE_NAME} · prescription {rx.number} (continued)", size=9, gray=0.4)
        return 726

    doc.text(left, y, config.OFFICE_NAME, size=18, bold=True)
    doc.text_right(right, y, "PRESCRIPTION", size=14, bold=True)
    y -= 16
    office = " · ".join(x for x in (config.OFFICE_ADDRESS, config.OFFICE_PHONE and f"Phone {config.OFFICE_PHONE}") if x)
    if office:
        doc.text(left, y, office, size=9, gray=0.35)
    doc.text_right(right, y, rx.number, size=10, bold=True)
    y -= 12
    doc.line(left, y, right, y, width=1.2)
    y -= 20

    # Prescriber (left) and patient (right).
    col2 = 310
    doc.text(left, y, "PRESCRIBER", size=8, bold=True, gray=0.4)
    doc.text(col2, y, "PATIENT", size=8, bold=True, gray=0.4)
    y -= 14
    role = appointments.workers_role_label(prescriber.role) if prescriber else ""
    prescriber_lines = [
        (appointments._provider_name(prescriber) if prescriber else "—", True),
        (" · ".join(x for x in (role, prescriber.specialty if prescriber else None) if x), False),
        (
            f"Licence: {prescriber.license_number}"
            if prescriber and prescriber.license_number
            else "Licence: not on file",
            False,
        ),
        (config.OFFICE_NAME, False),
    ]
    address = ", ".join(
        x
        for x in (
            patient.address_line,
            patient.postal_code and patient.city and f"{patient.postal_code} {patient.city}" or patient.city,
            patient.state,
        )
        if x
    )
    patient_lines = [
        (f"{patient.first_name} {patient.last_name}", True),
        (f"Date of birth: {patient.date_of_birth:%m/%d/%Y} · {patient.mrn}", False),
        (address or "Address: not on file", False),
        (f"Phone: {patient.phone}" if patient.phone else "", False),
    ]
    y = doc.columns(y, (left, col2 - left - 14, prescriber_lines), (col2, right - col2, patient_lines))
    y -= 4
    doc.text(left, y, f"Date issued: {issued:%B} {issued.day}, {issued.year}", size=10)
    if d is not None:
        doc.text(col2, y, f"Diagnosis: {(d.code + ' — ') if d.code else ''}{d.description}"[:80], size=10)
    y -= 12
    doc.line(left, y, right, y)
    y -= 22

    for n, item in enumerate(items_of(db, rx), start=1):
        if y < 160:
            doc.new_page()
            y = header()
        doc.box(left, y - 4, 34, 18, gray=0.9)
        doc.text(left + 6, y, f"Rx {n}", size=10, bold=True)
        doc.text(left + 44, y, item_line(item), size=12, bold=True)
        y -= 18
        y = doc.paragraph(left + 44, y, f"Sig: {item.sig}", width=width - 44, size=10, on_new_page=header)
        if item.patient_instructions:
            y = doc.paragraph(
                left + 44,
                y,
                f"Patient instructions: {item.patient_instructions}",
                width=width - 44,
                size=9.5,
                on_new_page=header,
            )
        dispense = f"Dispense: {item.quantity} ({number_words(item.quantity)}) {item.quantity_unit}"
        refills = f"Refills: {item.refills} ({number_words(item.refills)})"
        substitution = (
            "Substitution permitted" if item.substitution_allowed else "DISPENSE AS WRITTEN — no substitution"
        )
        doc.text(left + 44, y, f"{dispense}    {refills}    {substitution}", size=9.5)
        y -= 24

    if rx.note_to_pharmacist:
        doc.text(left, y, "Note to the pharmacist", size=9, bold=True)
        y = doc.paragraph(left, y - 13, rx.note_to_pharmacist, width=width, size=9.5, on_new_page=header)
        y -= 6
    if rx.allergy_override:
        y = doc.paragraph(
            left,
            y,
            f"Prescribed despite a recorded allergy — reason: {rx.allergy_override}",
            width=width,
            size=9,
            bold=True,
            on_new_page=header,
        )
        y -= 6
    if y < 120:
        doc.new_page()
        y = header()
    doc.line(left, y, right, y)
    y -= 18
    signer = appointments._provider_name(prescriber) if prescriber else "the prescriber"
    doc.text(left, y, f"Electronically signed by {signer}", size=10, bold=True)
    doc.text(left, y - 13, f"on {issued:%B} {issued.day}, {issued.year} at {issued:%H:%M} ({tz})", size=9.5)
    doc.text_right(right, y, f"Verification: {rx.number}-{rx.verification}", size=9.5)
    doc.text_right(right, y - 13, "To verify, call the office.", size=8.5)
    y -= 40
    if rx.status == "voided":
        voided = appointments.as_utc(rx.voided_at).astimezone(tz)
        doc.text(left, y, "VOID — DO NOT FILL", size=28, bold=True, gray=0.15)
        doc.paragraph(
            left,
            y - 18,
            f"Voided on {voided:%B} {voided.day}, {voided.year} at {voided:%H:%M}: {rx.void_reason}",
            width=width,
            size=10,
        )
        y -= 50
    doc.on_each_page(lambda n, total: _footer(doc, n, total, rx))
    return doc.output()


def _footer(doc: pdf.PdfDocument, n: int, total: int, rx: models.Prescription) -> None:
    """Page n of total — and, outside production, the SAMPLE banner — on every page."""
    left, right, width = 54, 558, 504
    if not config.IS_PROD:
        doc.box(left, 40, width, 22, gray=0.9)
        doc.text(left + 10, 47, "SAMPLE — NOT A VALID PRESCRIPTION (development / dummy data)", size=11, bold=True)
    doc.text(left, 26, f"{config.OFFICE_NAME} · prescription {rx.number}", size=8, gray=0.45)
    doc.text_right(right, 26, f"Page {n} of {total}", size=8)


# ---------------------------------------------------------------------------- routes: the provider


def _my_visit(db: Session, s: staff_accounts.StaffPrincipal, appointment_id: int) -> models.Appointment:
    a = db.get(models.Appointment, appointment_id)
    if a is None or a.provider_id != s.worker.id:
        raise HTTPException(status_code=404, detail="Appointment not found.")
    return a


def _patients_rx(db: Session, a: models.Appointment, rx_id: int) -> models.Prescription:
    rx = db.get(models.Prescription, rx_id)
    if rx is None or rx.patient_id != a.patient_id:
        raise HTTPException(status_code=404, detail="Prescription not found.")
    return rx


def _pdf_response(rx: models.Prescription, data: bytes) -> Response:
    return Response(
        content=data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{rx.number}.pdf"',
            "Cache-Control": "private, no-store",
        },
    )


@staff_router.post("/{appointment_id}/diagnoses", response_model=schemas.DiagnosisOut, status_code=201)
def post_diagnosis(
    appointment_id: int,
    body: schemas.DiagnosisIn,
    s: staff_accounts.StaffPrincipal = Depends(staff_accounts.staff_principal),
    db: Session = Depends(get_db),
):
    a = _my_visit(db, s, appointment_id)
    d = add_diagnosis(db, a, s.worker, body)
    accounts.audit(db, s.principal.account, "visit.diagnosis", None, appointment=a.number, code=d.code)
    db.commit()
    return diagnosis_out(db, d)


@staff_router.delete("/{appointment_id}/diagnoses/{diagnosis_id}", status_code=204)
def delete_diagnosis(
    appointment_id: int,
    diagnosis_id: int,
    s: staff_accounts.StaffPrincipal = Depends(staff_accounts.staff_principal),
    db: Session = Depends(get_db),
):
    a = _my_visit(db, s, appointment_id)
    remove_diagnosis(db, a, s.worker, diagnosis_id)
    accounts.audit(db, s.principal.account, "visit.diagnosis.remove", None, appointment=a.number)
    db.commit()
    return Response(status_code=204)


@staff_router.post("/{appointment_id}/prescriptions", response_model=schemas.PrescriptionOut, status_code=201)
def post_prescription(
    appointment_id: int,
    body: schemas.PrescriptionIn,
    s: staff_accounts.StaffPrincipal = Depends(staff_accounts.staff_principal),
    db: Session = Depends(get_db),
):
    """Sign and issue a prescription (and e-mail it to ``pharmacy_id`` if given). ``409`` allergy warning
    without an override reason · ``422`` controlled substance · ``403`` not a prescriber."""
    a = _my_visit(db, s, appointment_id)
    rx = issue(db, a, s.worker, body, s.principal.account)
    db.commit()
    return prescription_out(db, rx)


@staff_router.get(
    "/{appointment_id}/prescriptions/{rx_id}/pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}, "description": "The prescription"}},
)
def get_prescription_pdf(
    appointment_id: int,
    rx_id: int,
    s: staff_accounts.StaffPrincipal = Depends(staff_accounts.staff_principal),
    db: Session = Depends(get_db),
):
    a = _my_visit(db, s, appointment_id)
    rx = _patients_rx(db, a, rx_id)
    data = render_pdf(db, rx)
    record_download(db, rx, s.principal.account)
    db.commit()
    return _pdf_response(rx, data)


@staff_router.post("/{appointment_id}/prescriptions/{rx_id}/send", response_model=schemas.PrescriptionOut)
def post_send(
    appointment_id: int,
    rx_id: int,
    body: schemas.PrescriptionSend,
    s: staff_accounts.StaffPrincipal = Depends(staff_accounts.staff_principal),
    db: Session = Depends(get_db),
):
    """E-mail the prescription (PDF attached) to a pharmacy of the directory."""
    a = _my_visit(db, s, appointment_id)
    rx = _patients_rx(db, a, rx_id)
    _require_prescriber(s.worker)
    send(db, rx, _pharmacy_for_sending(db, body.pharmacy_id), s.principal.account)
    db.commit()
    return prescription_out(db, rx)


@staff_router.post("/{appointment_id}/prescriptions/{rx_id}/void", response_model=schemas.PrescriptionOut)
def post_void(
    appointment_id: int,
    rx_id: int,
    body: schemas.PrescriptionVoid,
    s: staff_accounts.StaffPrincipal = Depends(staff_accounts.staff_principal),
    db: Session = Depends(get_db),
):
    a = _my_visit(db, s, appointment_id)
    rx = _patients_rx(db, a, rx_id)
    void(db, rx, s.worker, body.reason, s.principal.account)
    db.commit()
    return prescription_out(db, rx)


def _at_visit(w: models.Worker, a: models.Appointment) -> str:
    return f"{appointments._provider_name(w)} at {a.number}"


@staff_router.put("/{appointment_id}/pharmacy", response_model=schemas.PatientPharmacyOut)
def put_patient_pharmacy(
    appointment_id: int,
    body: schemas.PatientPharmacyIn,
    s: staff_accounts.StaffPrincipal = Depends(staff_accounts.staff_principal),
    db: Session = Depends(get_db),
):
    """The pharmacy the patient uses, as they tell it at the visit: one of the directory, a new one (added to
    the directory), or none."""
    a = _my_visit(db, s, appointment_id)
    patient = db.get(models.Patient, a.patient_id)
    pharmacy = set_patient_pharmacy(db, patient, body, by=_at_visit(s.worker, a), source="provider")
    accounts.audit(
        db,
        s.principal.account,
        "patient.pharmacy.set",
        None,
        patient=patient.mrn,
        pharmacy=pharmacy.name if pharmacy else None,
        added=body.pharmacy is not None,
    )
    db.commit()
    return patient_pharmacy_out(db, patient)


@staff_router.patch("/{appointment_id}/pharmacy", response_model=schemas.PatientPharmacyOut)
def patch_patient_pharmacy(
    appointment_id: int,
    body: schemas.PharmacyDetailsPatch,
    s: staff_accounts.StaffPrincipal = Depends(staff_accounts.staff_principal),
    db: Session = Depends(get_db),
):
    """Update the details of the patient's pharmacy with what the patient says (e-mail, phone, fax, …)."""
    a = _my_visit(db, s, appointment_id)
    patient = db.get(models.Patient, a.patient_id)
    pharmacy = update_patient_pharmacy(db, patient, body, by=_at_visit(s.worker, a))
    accounts.audit(
        db,
        s.principal.account,
        "pharmacy.update",
        None,
        pharmacy=pharmacy.name,
        fields=sorted(body.model_fields_set),
        at=a.number,
    )
    db.commit()
    return patient_pharmacy_out(db, patient)


# ---------------------------------------------------------------------------- routes: the office


@admin_router.get("/pharmacies", response_model=list[schemas.PharmacyOut])
def list_pharmacies(
    include_inactive: bool = False, _p: auth.Principal = Depends(auth.admin_principal), db: Session = Depends(get_db)
):
    stmt = select(models.Pharmacy).order_by(models.Pharmacy.name)
    if not include_inactive:
        stmt = stmt.where(models.Pharmacy.active.is_(True))
    return [pharmacy_out(p) for p in db.execute(stmt).scalars()]


@admin_router.post("/pharmacies", response_model=schemas.PharmacyOut, status_code=201)
def create_pharmacy(
    body: schemas.PharmacyIn, p: auth.Principal = Depends(auth.admin_principal), db: Session = Depends(get_db)
):
    row = models.Pharmacy(**body.model_dump(mode="json"))
    db.add(row)
    db.flush()
    accounts.audit(db, p.account, "pharmacy.add", None, pharmacy=row.name)
    db.commit()
    return pharmacy_out(row)


@admin_router.patch("/pharmacies/{pharmacy_id}", response_model=schemas.PharmacyOut)
def update_pharmacy(
    pharmacy_id: int,
    body: schemas.PharmacyPatch,
    p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    row = db.get(models.Pharmacy, pharmacy_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Pharmacy not found.")
    for key, value in body.model_dump(mode="json", exclude_unset=True).items():
        if key == "name" and not value:
            continue
        setattr(row, key, value)
    accounts.audit(db, p.account, "pharmacy.update", None, pharmacy=row.name, fields=sorted(body.model_fields_set))
    db.commit()
    return pharmacy_out(row)


@admin_router.get("/patients/{patient_id}/pharmacy", response_model=schemas.PatientPharmacyOut)
def get_patients_pharmacy(
    patient_id: int, _p: auth.Principal = Depends(auth.admin_principal), db: Session = Depends(get_db)
):
    patient = db.get(models.Patient, patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found.")
    return patient_pharmacy_out(db, patient)


@admin_router.put("/patients/{patient_id}/pharmacy", response_model=schemas.PatientPharmacyOut)
def put_patients_pharmacy(
    patient_id: int,
    body: schemas.PatientPharmacyIn,
    p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    """The office sets the patient's pharmacy (one of the directory — or a new one — or none)."""
    patient = db.get(models.Patient, patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found.")
    pharmacy = set_patient_pharmacy(db, patient, body, by=f"the office ({p.account.email})", source="office")
    accounts.audit(
        db, p.account, "patient.pharmacy.set", None, patient=patient.mrn, pharmacy=pharmacy.name if pharmacy else None
    )
    db.commit()
    return patient_pharmacy_out(db, patient)


@admin_router.get("/patients/{patient_id}/prescriptions", response_model=list[schemas.PrescriptionOut])
def patient_prescriptions(
    patient_id: int, _p: auth.Principal = Depends(auth.admin_principal), db: Session = Depends(get_db)
):
    patient = db.get(models.Patient, patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found.")
    return [prescription_out(db, rx) for rx in prescriptions_of(db, patient)]


@admin_router.get(
    "/patients/{patient_id}/prescriptions/{rx_id}/pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}, "description": "The prescription"}},
)
def patient_prescription_pdf(
    patient_id: int, rx_id: int, p: auth.Principal = Depends(auth.admin_principal), db: Session = Depends(get_db)
):
    """The PDF, for the front desk to print for the patient (recorded)."""
    rx = db.get(models.Prescription, rx_id)
    if rx is None or rx.patient_id != patient_id:
        raise HTTPException(status_code=404, detail="Prescription not found.")
    data = render_pdf(db, rx)
    record_download(db, rx, p.account)
    db.commit()
    return _pdf_response(rx, data)
