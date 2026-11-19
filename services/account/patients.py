"""Patients — the medical office's roster and the public intake page behind it.

    POST /api/v1/patients/check   is this patient new?  (name + date of birth, optional e-mail/phone)
    POST /api/v1/patients         register: creates the record when the patient is new (201),
                                  otherwise answers with the existing record's summary (200)
    GET  /api/v1/patients         search/list the roster           (administrators)
    GET  /api/v1/patients/{id}    one full record                  (administrators)
    PUT  /api/v1/patients/{id}    edit the record — every field, the insurance, the card photos
                                  (replace / remove); a changed insurance is checked again (administrators)
    GET  /api/v1/patients/{id}/insurance-card/{front|back}
                                  the photo of the insurance card   (administrators)
    POST /api/v1/patients/{id}/insurance/verify
                                  check coverage and benefits now    (administrators)
    GET  /api/v1/patients/{id}/insurance/verifications
                                  every check so far, newest first   (administrators)

How "the same patient" is decided (``find_match``), in order:

    name_dob    first name + last name + date of birth agree (names compared lower-cased,
                accent-stripped, punctuation/whitespace ignored — "Anne-Marie" == "anne marie")
    email_dob   the e-mail address agrees *and* the date of birth agrees
    phone_dob   the phone number agrees *and* the date of birth agrees — digits only, and a number
                typed without its country code still matches the stored one (suffix comparison)

Contact details alone are deliberately **not** enough: a parent registers several children
with one e-mail address and one phone number; the date of birth keeps them apart. A returning
patient whose name was typed differently ("Bob" vs "Robert", a new surname) is still found
through the second and third rule.

The check and the registration are anonymous (the patient fills the form before any account
exists) and rate-limited per caller address. They reveal nothing beyond "known / new" and the
record number: reading records needs an administrator account.

Insurance: the form's insurance section (company, plan, member id, group number, policyholder)
is stored on the record; the photos of the card (front / back) arrive in the same request as
image data URLs (insurance.py checks them) and are stored in ``patient_insurance_cards`` — for
a *new* patient only. A known patient gets "welcome back" and nothing is stored, as for every
other field: corrections and new cards are handled at the front desk.

Verification: when the section names an insurance company and a member id, registration also
runs an eligibility & benefits check (insurance_verification.py — the built-in fake unless a
clearinghouse is configured): coverage and effective dates, copay, deductible status,
coinsurance, referral and prior-authorization requirements. The answer is stored
(``patient_insurance_verifications``, one row per check), shown to the patient on the
confirmation page and to the desk on the record, which can re-check at any time.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import func, literal, or_, select
from sqlalchemy.orm import Session

import auth
import config
import insurance
import insurance_verification
import models
import schemas
from database import get_db
from ratelimit import RateLimiter

log = logging.getLogger("account.patients")

router = APIRouter(prefix="/patients", tags=["patients"])

# A real patient checks once or twice; a scraper probing "is X a patient here?" must not get far.
CHECK_LIMITER = RateLimiter(limit=30, window_seconds=600)
CREATE_LIMITER = RateLimiter(limit=10, window_seconds=600)

MATCH_NAME_DOB = "name_dob"
MATCH_EMAIL_DOB = "email_dob"
MATCH_PHONE_DOB = "phone_dob"

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


# ---------------------------------------------------------------------------- normalisation


def normalize_name(value: str) -> str:
    """``"  Zoë O'Neil-Smith "`` → ``"zoeoneilsmith"``: case, accents, punctuation and spacing
    do not count when two names are compared."""
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_only = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _NON_ALNUM.sub("", ascii_only.lower())


def normalize_email(value: str | None) -> str | None:
    text = (value or "").strip().lower()
    return text or None


def normalize_phone(value: str | None) -> str | None:
    """Digits only; a leading international ``00`` is dropped like the ``+`` it stands for, so
    ``+1 (555) 010-0123``, ``001 555 010 0123`` and ``15550100123`` are the same number."""
    if not value:
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    if value.strip().startswith("00"):
        digits = digits[2:]
    return digits or None


MIN_PHONE_MATCH_DIGITS = 7


def phones_match(a: str | None, b: str | None) -> bool:
    """Same number when one is the other with a country code in front (``15552223333`` vs
    ``5552223333``); at least 7 digits have to agree."""
    if not a or not b or min(len(a), len(b)) < MIN_PHONE_MATCH_DIGITS:
        return False
    return a.endswith(b) or b.endswith(a)


# ---------------------------------------------------------------------------- matching


def find_match(db: Session, identity: schemas.PatientIdentity) -> tuple[models.Patient | None, list[str]]:
    """The existing record this person most likely is, and the rules that say so."""
    first = normalize_name(identity.first_name)
    last = normalize_name(identity.last_name)
    email = normalize_email(identity.email)
    phone = normalize_phone(identity.phone)

    conditions = [
        (models.Patient.last_name_norm == last) & (models.Patient.first_name_norm == first),
    ]
    if email:
        conditions.append(models.Patient.email_norm == email)
    if phone and len(phone) >= MIN_PHONE_MATCH_DIGITS:
        conditions.append(models.Patient.phone_norm.like("%" + phone))
        conditions.append(literal(phone).like(literal("%").concat(models.Patient.phone_norm)))
    candidates = (
        db.execute(
            select(models.Patient)
            .where(models.Patient.date_of_birth == identity.date_of_birth, or_(*conditions))
            .order_by(models.Patient.id)
        )
        .scalars()
        .all()
    )
    best: models.Patient | None = None
    best_rules: list[str] = []
    for p in candidates:
        rules = []
        if p.last_name_norm == last and p.first_name_norm == first:
            rules.append(MATCH_NAME_DOB)
        if email and p.email_norm == email:
            rules.append(MATCH_EMAIL_DOB)
        if phones_match(phone, p.phone_norm):
            rules.append(MATCH_PHONE_DOB)
        # Prefer the record the name agrees with; among equals, the oldest (lowest id).
        if rules and (best is None or (MATCH_NAME_DOB in rules and MATCH_NAME_DOB not in best_rules)):
            best, best_rules = p, rules
    return best, best_rules


def create(db: Session, body: schemas.PatientIn, cards: dict[str, insurance.CardImage] | None = None) -> models.Patient:
    """Store a new record (+ the decoded card photos, ``decode_cards(body)``)."""
    cards = cards or {}
    patient = models.Patient(
        first_name=body.first_name,
        last_name=body.last_name,
        date_of_birth=body.date_of_birth,
        sex=body.sex,
        email=str(body.email) if body.email else None,
        phone=body.phone,
        address_line=body.address_line,
        city=body.city,
        state=body.state,
        postal_code=body.postal_code,
        country=body.country,
        insurance_provider=body.insurance_provider,
        insurance_plan_name=body.insurance_plan_name,
        insurance_member_id=body.insurance_member_id,
        insurance_group_number=body.insurance_group_number,
        insurance_policyholder_name=body.insurance_policyholder_name,
        insurance_policyholder_relationship=body.insurance_policyholder_relationship,
        emergency_contact_name=body.emergency_contact_name,
        emergency_contact_phone=body.emergency_contact_phone,
        reason_for_visit=body.reason_for_visit,
        first_name_norm=normalize_name(body.first_name),
        last_name_norm=normalize_name(body.last_name),
        email_norm=normalize_email(body.email),
        phone_norm=normalize_phone(body.phone),
    )
    db.add(patient)
    db.flush()
    for side, image in cards.items():
        db.add(
            models.PatientInsuranceCard(
                patient_id=patient.id,
                side=side,
                content_type=image.content_type,
                size_bytes=image.size_bytes,
                data=image.data,
            )
        )
    db.flush()
    return patient


INSURANCE_FIELDS = (
    "insurance_provider",
    "insurance_plan_name",
    "insurance_member_id",
    "insurance_group_number",
    "insurance_policyholder_name",
    "insurance_policyholder_relationship",
)
DETAIL_FIELDS = (
    "sex",
    "address_line",
    "city",
    "state",
    "postal_code",
    "country",
    *INSURANCE_FIELDS,
    "emergency_contact_name",
    "emergency_contact_phone",
    "reason_for_visit",
)


def apply_update(
    db: Session, patient: models.Patient, body: schemas.PatientUpdate, cards: dict[str, insurance.CardImage]
) -> bool:
    """An update of the record — by the patient (patient_updates.py) or the office
    (``PatientAdminUpdate``, which also carries the date of birth): name, contact details,
    everything in PatientDetails, and the card photos (replace / remove / keep). Returns whether
    anything about the insurance changed — the caller then re-runs the eligibility check."""
    if isinstance(body, schemas.PatientAdminUpdate):
        patient.date_of_birth = body.date_of_birth
    patient.first_name = body.first_name
    patient.last_name = body.last_name
    patient.email = str(body.email) if body.email else None
    patient.phone = body.phone
    patient.first_name_norm = normalize_name(body.first_name)
    patient.last_name_norm = normalize_name(body.last_name)
    patient.email_norm = normalize_email(body.email)
    patient.phone_norm = normalize_phone(body.phone)
    insurance_changed = False
    for name in DETAIL_FIELDS:
        value = getattr(body, name)
        if getattr(patient, name) != value:
            setattr(patient, name, value)
            if name in INSURANCE_FIELDS:
                insurance_changed = True
    existing = {
        c.side: c
        for c in db.execute(
            select(models.PatientInsuranceCard).where(models.PatientInsuranceCard.patient_id == patient.id)
        ).scalars()
    }
    for side, remove in (("front", body.remove_insurance_card_front), ("back", body.remove_insurance_card_back)):
        image = cards.get(side)
        if image is not None:
            row = existing.get(side)
            if row is None:
                row = models.PatientInsuranceCard(patient_id=patient.id, side=side)
                db.add(row)
            row.content_type = image.content_type
            row.size_bytes = image.size_bytes
            row.data = image.data
            row.created_at = models.utcnow()
            insurance_changed = True
        elif remove and side in existing:
            db.delete(existing[side])
            insurance_changed = True
    patient.updated_at = models.utcnow()
    db.flush()
    return insurance_changed


def decode_cards(body: schemas.PatientIn | schemas.PatientUpdate) -> dict[str, insurance.CardImage]:
    """The card photos of the form, decoded and checked — ``{"front": …, "back": …}`` for the
    sides that were sent. Raises insurance.InsuranceCardError with a message for the patient."""
    cards: dict[str, insurance.CardImage] = {}
    for side, data_url in (("front", body.insurance_card_front), ("back", body.insurance_card_back)):
        if data_url:
            cards[side] = insurance.decode_card_image(data_url, side=side)
    return cards


def card_url(patient_id: int, side: str) -> str:
    """Where an administrator fetches the photo (relative to the API prefix, like the calendar links)."""
    return f"/api/v1/patients/{patient_id}/insurance-card/{side}"


def cards_of(db: Session, patient: models.Patient) -> list[schemas.InsuranceCardOut]:
    rows = (
        db.execute(
            select(models.PatientInsuranceCard)
            .where(models.PatientInsuranceCard.patient_id == patient.id)
            .order_by(models.PatientInsuranceCard.side.desc())  # front before back
        )
        .scalars()
        .all()
    )
    return [
        schemas.InsuranceCardOut(
            side=c.side,
            content_type=c.content_type,
            size_bytes=c.size_bytes,
            uploaded_at=c.created_at,
            url=card_url(patient.id, c.side),
        )
        for c in rows
    ]


# ---------------------------------------------------------------------------- insurance verification


def has_insurance(p: models.Patient) -> bool:
    return bool(p.insurance_provider or p.insurance_member_id)


def verify_insurance(
    db: Session, p: models.Patient, triggered_by: str, service_date: date | None = None
) -> models.InsuranceVerification:
    """Run the configured verifier for what is on the record and store the answer (one row per
    check). ``service_date``: the day of the visit (today by default). Never raises — a failure
    is stored as ``unavailable``."""
    request = insurance_verification.VerificationRequest(
        payer_name=p.insurance_provider,
        plan_name=p.insurance_plan_name,
        member_id=p.insurance_member_id,
        group_number=p.insurance_group_number,
        policyholder_name=p.insurance_policyholder_name,
        relationship=p.insurance_policyholder_relationship,
        first_name=p.first_name,
        last_name=p.last_name,
        date_of_birth=p.date_of_birth,
        service_date=service_date or date.today(),
    )
    r = insurance_verification.verify(request)
    row = models.InsuranceVerification(
        patient_id=p.id,
        status=r.status,
        message=r.message[:500],
        triggered_by=triggered_by,
        provider=r.provider,
        reference=r.reference,
        payer_name=r.payer_name,
        plan_name=r.plan_name,
        plan_type=r.plan_type,
        member_id=r.member_id,
        group_number=r.group_number,
        coverage_start=r.coverage_start,
        coverage_end=r.coverage_end,
        currency=r.currency,
        copay_cents=r.copay_cents,
        specialist_copay_cents=r.specialist_copay_cents,
        deductible_cents=r.deductible_cents,
        deductible_met_cents=r.deductible_met_cents,
        deductible_status=r.deductible_status,
        coinsurance_percent=r.coinsurance_percent,
        out_of_pocket_max_cents=r.out_of_pocket_max_cents,
        out_of_pocket_met_cents=r.out_of_pocket_met_cents,
        referral_required=r.referral_required,
        referral_notes=r.referral_notes,
        prior_auth_required=r.prior_auth_required,
        prior_auth_notes=r.prior_auth_notes,
        raw=json.dumps(r.raw, default=str),
        checked_at=r.checked_at,
    )
    db.add(row)
    db.flush()
    log.info("insurance verification for %s (%s): %s", p.mrn, triggered_by, r.status)
    return row


def verification_out(v: models.InsuranceVerification) -> schemas.InsuranceVerificationOut:
    remaining = None if v.deductible_cents is None else max(0, v.deductible_cents - (v.deductible_met_cents or 0))
    return schemas.InsuranceVerificationOut(
        id=v.id,
        status=v.status,
        message=v.message,
        triggered_by=v.triggered_by,
        provider=v.provider,
        reference=v.reference,
        payer_name=v.payer_name,
        plan_name=v.plan_name,
        plan_type=v.plan_type,
        member_id=v.member_id,
        group_number=v.group_number,
        coverage_start=v.coverage_start,
        coverage_end=v.coverage_end,
        currency=v.currency,
        copay_cents=v.copay_cents,
        specialist_copay_cents=v.specialist_copay_cents,
        deductible_cents=v.deductible_cents,
        deductible_met_cents=v.deductible_met_cents,
        deductible_remaining_cents=remaining,
        deductible_status=v.deductible_status,
        coinsurance_percent=v.coinsurance_percent,
        out_of_pocket_max_cents=v.out_of_pocket_max_cents,
        out_of_pocket_met_cents=v.out_of_pocket_met_cents,
        referral_required=v.referral_required,
        referral_notes=v.referral_notes,
        prior_auth_required=v.prior_auth_required,
        prior_auth_notes=v.prior_auth_notes,
        checked_at=v.checked_at,
    )


def verifications_of(db: Session, p: models.Patient) -> list[models.InsuranceVerification]:
    """Every check so far, newest first."""
    return (
        db.execute(
            select(models.InsuranceVerification)
            .where(models.InsuranceVerification.patient_id == p.id)
            .order_by(models.InsuranceVerification.checked_at.desc(), models.InsuranceVerification.id.desc())
        )
        .scalars()
        .all()
    )


def latest_verification(db: Session, p: models.Patient) -> schemas.InsuranceVerificationOut | None:
    rows = verifications_of(db, p)
    return verification_out(rows[0]) if rows else None


def summary(p: models.Patient) -> schemas.PatientSummary:
    return schemas.PatientSummary(
        id=p.id,
        mrn=p.mrn,
        first_name=p.first_name,
        last_name=p.last_name,
        date_of_birth=p.date_of_birth,
        created_at=p.created_at,
    )


def full(db: Session, p: models.Patient) -> schemas.PatientOut:
    return schemas.PatientOut(
        **summary(p).model_dump(),
        sex=p.sex,
        email=p.email,
        phone=p.phone,
        address_line=p.address_line,
        city=p.city,
        state=p.state,
        postal_code=p.postal_code,
        country=p.country,
        insurance_provider=p.insurance_provider,
        insurance_plan_name=p.insurance_plan_name,
        insurance_member_id=p.insurance_member_id,
        insurance_group_number=p.insurance_group_number,
        insurance_policyholder_name=p.insurance_policyholder_name,
        insurance_policyholder_relationship=p.insurance_policyholder_relationship,
        insurance_cards=cards_of(db, p),
        insurance_verification=latest_verification(db, p),
        emergency_contact_name=p.emergency_contact_name,
        emergency_contact_phone=p.emergency_contact_phone,
        reason_for_visit=p.reason_for_visit,
        updated_at=p.updated_at,
        **_health_lists(db, p),
    )


def _health_lists(db: Session, p: models.Patient) -> dict:
    import health_records  # local: health_records imports this module

    return health_records.lists_out(db, p)


def _caller(request: Request) -> str | None:
    return request.client.host if request.client else None


# ---------------------------------------------------------------------------- public endpoints


@router.post("/check", response_model=schemas.PatientCheck)
def check(body: schemas.PatientIdentity, request: Request, db: Session = Depends(get_db)):
    """Is this patient new to the office? Answers ``is_new`` and, for a known patient, the
    record's summary (number, name, date of birth) — nothing more."""
    if not CHECK_LIMITER.allow(_caller(request)):
        raise HTTPException(status_code=429, detail="Too many requests. Please try again in a few minutes.")
    patient, rules = find_match(db, body)
    if patient is None:
        return schemas.PatientCheck(is_new=True)
    return schemas.PatientCheck(is_new=False, matched_on=rules, patient=summary(patient))


@router.post(
    "",
    response_model=schemas.PatientRegistration,
    status_code=201,
    responses={200: {"model": schemas.PatientRegistration, "description": "Already registered; nothing stored"}},
)
def register(
    body: schemas.PatientIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    me: auth.Principal | None = Depends(auth.optional_principal),
):
    """Verify and register in one step: a new patient gets a record (``201``, returned in full
    so they can check it); a known one gets the existing record's summary (``200``) and
    nothing is stored twice. Registered while signed in with a patient account (a parent
    registering a child), the new record belongs to that account from the start."""
    if not CREATE_LIMITER.allow(_caller(request)):
        raise HTTPException(status_code=429, detail="Too many requests. Please try again in a few minutes.")
    try:
        cards = decode_cards(body)
    except insurance.InsuranceCardError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    existing, rules = find_match(db, body)
    if existing is not None:
        response.status_code = 200
        return schemas.PatientRegistration(status="existing", matched_on=rules, patient=summary(existing))
    patient = create(db, body, cards)
    if me is not None and me.account.role == models.ROLE_PATIENT:
        patient.account_id = me.account.id
    if config.INSURANCE_VERIFY_ON_REGISTRATION and has_insurance(patient):
        verify_insurance(db, patient, "registration")
    db.commit()
    log.info("patient registered: %s (insurance card photos: %s)", patient.mrn, ", ".join(cards) or "none")
    return schemas.PatientRegistration(status="new", patient=full(db, patient))


# ---------------------------------------------------------------------------- staff endpoints


@router.get("", response_model=schemas.PatientPage)
def list_patients(
    q: str = Query(default="", max_length=120, description="Name, record number, e-mail or phone"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    _p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    stmt = select(models.Patient)
    term = q.strip()
    if term:
        if re.fullmatch(r"[Pp]-?\d+", term):
            stmt = stmt.where(models.Patient.id == int(term.lstrip("Pp-")))
        else:
            norm = normalize_name(term)
            like = f"%{norm}%"
            conditions = [
                models.Patient.last_name_norm.like(like),
                models.Patient.first_name_norm.like(like),
                models.Patient.email_norm.like(f"%{term.lower()}%"),
            ]
            digits = normalize_phone(term)
            if digits:
                conditions.append(models.Patient.phone_norm.like(f"%{digits}%"))
            stmt = stmt.where(or_(*conditions))
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = (
        db.execute(
            stmt.order_by(models.Patient.last_name_norm, models.Patient.first_name_norm).offset(offset).limit(limit)
        )
        .scalars()
        .all()
    )
    return schemas.PatientPage(items=[summary(p) for p in rows], total=total, offset=offset, limit=limit)


@router.get("/{patient_id}", response_model=schemas.PatientOut)
def get_patient(patient_id: int, _p: auth.Principal = Depends(auth.admin_principal), db: Session = Depends(get_db)):
    patient = db.get(models.Patient, patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found.")
    return full(db, patient)


@router.put("/{patient_id}", response_model=schemas.PatientOut)
def update_patient(
    patient_id: int,
    body: schemas.PatientAdminUpdate,
    _p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    """The office edits the record: every field including the date of birth, the insurance
    section and the card photos (a data URL replaces a side, ``remove_insurance_card_*`` drops
    it, otherwise it is kept). A changed insurance is checked again (``triggered_by = staff``)."""
    patient = db.get(models.Patient, patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found.")
    try:
        cards = decode_cards(body)
    except insurance.InsuranceCardError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    insurance_changed = apply_update(db, patient, body, cards)
    if insurance_changed and config.INSURANCE_VERIFY_ON_REGISTRATION and has_insurance(patient):
        verify_insurance(db, patient, "staff")
    db.commit()
    log.info("patient %s edited by the office (insurance changed: %s)", patient.mrn, insurance_changed)
    return full(db, patient)


@router.get(
    "/{patient_id}/insurance-card/{side}",
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}, "image/png": {}, "image/webp": {}}, "description": "The photo"}},
)
def get_insurance_card(
    patient_id: int,
    side: schemas.InsuranceCardSide,
    _p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    """The photo of one side of the patient's insurance card, as uploaded on the intake page."""
    card = db.execute(
        select(models.PatientInsuranceCard).where(
            models.PatientInsuranceCard.patient_id == patient_id, models.PatientInsuranceCard.side == side
        )
    ).scalar_one_or_none()
    if card is None:
        raise HTTPException(status_code=404, detail="No photo of this side of the insurance card on file.")
    return Response(
        content=card.data,
        media_type=card.content_type,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'inline; filename="{patient_id}-insurance-card-{side}.{_extension(card)}"',
        },
    )


@router.post("/{patient_id}/insurance/verify", response_model=schemas.InsuranceVerificationOut)
def verify_patient_insurance(
    patient_id: int, _p: auth.Principal = Depends(auth.admin_principal), db: Session = Depends(get_db)
):
    """Check the patient's coverage and benefits now (the configured verifier; the built-in fake
    outside production) and keep the answer. Works for a record without insurance details too —
    the answer is then ``incomplete``."""
    patient = db.get(models.Patient, patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found.")
    row = verify_insurance(db, patient, "staff")
    db.commit()
    return verification_out(row)


@router.get("/{patient_id}/insurance/verifications", response_model=list[schemas.InsuranceVerificationOut])
def list_patient_insurance_verifications(
    patient_id: int, _p: auth.Principal = Depends(auth.admin_principal), db: Session = Depends(get_db)
):
    """Every check so far, newest first."""
    patient = db.get(models.Patient, patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found.")
    return [verification_out(v) for v in verifications_of(db, patient)]


def _extension(card: models.PatientInsuranceCard) -> str:
    return {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}.get(card.content_type, "img")
