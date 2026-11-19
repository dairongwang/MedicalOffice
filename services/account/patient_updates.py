"""A patient updates their own record — name, contact details, address, insurance (details and
card photos), emergency contact — from the registration page.

Two ways to prove the record is theirs, because name + date of birth alone are not enough (the
intake check is anonymous, and a parent's children share one phone):

    signed in     a patient account the record is linked to (appointments.my_records):
                  GET / PUT /api/v1/me/patients/{id}
    by code       a one-time code sent to the e-mail address and/or phone number **on the record**
                  (messaging.py — the built-in fake outbox in development):
                  POST /api/v1/patients/update/request   {identity}           → challenge (+ where the code went)
                  POST /api/v1/patients/update/confirm   {challenge, code}    → edit token + the record
                  GET  /api/v1/patients/update/me        X-Edit-Token         → the record
                  PUT  /api/v1/patients/update           X-Edit-Token + body  → the updated record

The challenge and the edit token are signed, stateless and short-lived (auth.sign); wrong codes
are counted per record (FailureLimiter), requests per caller address. A record with neither an
e-mail nor a phone cannot be edited by code — sign in, or ask the front desk.

What may change is ``schemas.PatientUpdate``: everything but the date of birth (the identity
anchor, corrected at the desk). A change to the insurance re-runs the eligibility check
(``triggered_by = patient``). Nothing a patient does reaches other records.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

import auth
import config
import insurance
import messaging
import models
import patients
import schemas
import twofactor
from database import get_db
from ratelimit import FailureLimiter, RateLimiter

log = logging.getLogger("account.patient_updates")

router = APIRouter(prefix="/patients/update", tags=["patients"])
me_router = APIRouter(prefix="/me/patients", tags=["me"])

REQUEST_LIMITER = RateLimiter(limit=10, window_seconds=600)  # codes per caller address
CODE_FAILURES = FailureLimiter(limit=5, window_seconds=900)  # wrong codes per record
CODE_DIGITS = 6
CODE_TTL_SECONDS = config.TWO_FACTOR_CODE_TTL_SECONDS
EDIT_TTL_SECONDS = config.PATIENT_EDIT_TTL_SECONDS
PURPOSE_CODE = "patient-edit-code"
PURPOSE_EDIT = "patient-edit"


def _code_hash(patient_id: int, code: str) -> str:
    msg = f"{patient_id}:{PURPOSE_CODE}:{''.join(ch for ch in code if ch.isdigit())}".encode()
    return hmac.new(config.TOKEN_SECRET.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def _caller(request: Request) -> str | None:
    return request.client.host if request.client else None


def _message(code: str) -> tuple[str, str]:
    minutes = max(1, CODE_TTL_SECONDS // 60)
    subject = f"{config.OFFICE_NAME}: your code to update your details"
    body = (
        f"{code} is your {config.OFFICE_NAME} code to update your patient details. "
        f"It is valid for {minutes} minutes.\nIf you did not request it, you can ignore this message."
    )
    return subject, body


def send_code(patient: models.Patient) -> tuple[str, list[str], list[str]]:
    """Deliver a fresh code to every contact detail on the record that a channel can reach.
    Returns (code, masked destinations, channels). 409 when there is nothing to send to; 503
    when every delivery failed."""
    targets: list[tuple[str, str, str]] = []  # channel, destination, masked
    if patient.email and messaging.EMAIL_CHANNEL.available:
        targets.append((messaging.EMAIL, patient.email, twofactor.mask_email(patient.email) or patient.email))
    if patient.phone and messaging.SMS_CHANNEL.available:
        try:
            number = twofactor.normalize_phone(patient.phone)
        except (ValueError, HTTPException):
            number = None
        if number:
            targets.append((messaging.SMS, number, twofactor.mask_phone(number) or number))
    if not targets:
        raise HTTPException(
            status_code=409,
            detail="This record has no e-mail address or phone number we could send a code to. "
            "Please sign in with your patient account, or ask the front desk to update your details.",
        )
    code = f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"
    subject, body = _message(code)
    sent_to: list[str] = []
    channels: list[str] = []
    errors: list[str] = []
    for channel, destination, masked in targets:
        try:
            messaging.channel(channel).send(destination, body, subject=subject)
        except messaging.MessagingError as exc:
            errors.append(f"{channel}: {exc}")
            continue
        sent_to.append(f"{channel}:{masked}")
        channels.append(channel)
    if not sent_to:
        raise HTTPException(status_code=503, detail="The code could not be sent: " + "; ".join(errors))
    return code, sent_to, channels


# ---------------------------------------------------------------------------- by code (no account)


@router.post("/request", response_model=schemas.PatientUpdateChallenge)
def request_code(body: schemas.PatientIdentity, request: Request, db: Session = Depends(get_db)):
    """Step 1: who you are → a code goes to the e-mail / phone on the record (never to what was
    typed here). ``404`` unknown patient · ``409`` nothing on the record to send to."""
    if not REQUEST_LIMITER.allow(_caller(request)):
        raise HTTPException(status_code=429, detail="Too many requests. Please try again in a few minutes.")
    patient, _rules = patients.find_match(db, body)
    if patient is None:
        raise HTTPException(
            status_code=404,
            detail="We could not find a patient record with these details. Please check the spelling of "
            "your name and your date of birth, or register first.",
        )
    code, sent_to, channels = send_code(patient)
    challenge = auth.sign(
        {
            "t": PURPOSE_CODE,
            "pid": patient.id,
            "h": _code_hash(patient.id, code),
            "n": secrets.token_hex(8),
            "exp": int(time.time()) + CODE_TTL_SECONDS,
        }
    )
    log.info("patient %s asked for an update code (%s)", patient.mrn, ", ".join(channels))
    return schemas.PatientUpdateChallenge(
        challenge=challenge,
        patient=patients.summary(patient),
        sent_to=sent_to,
        channels=channels,
        expires_in=CODE_TTL_SECONDS,
        fake=any(messaging.channel(c).fake for c in channels),
        outbox_url=messaging.outbox_url() if any(messaging.channel(c).fake for c in channels) else None,
    )


@router.post("/confirm", response_model=schemas.PatientUpdateSession)
def confirm_code(body: schemas.PatientUpdateConfirm, db: Session = Depends(get_db)):
    """Step 2: the code → a short-lived edit token and the record in full."""
    payload = auth.verify_signed(body.challenge)
    if not payload or payload.get("t") != PURPOSE_CODE:
        raise HTTPException(status_code=401, detail="The code has expired. Please ask for a new one.")
    patient_id = int(payload["pid"])
    key = f"patient-edit:{patient_id}"
    if CODE_FAILURES.blocked(key):
        raise HTTPException(
            status_code=429,
            detail="Too many wrong codes. Please wait a few minutes and ask for a new one.",
            headers={"Retry-After": str(CODE_FAILURES.retry_after(key))},
        )
    if not hmac.compare_digest(_code_hash(patient_id, body.code), payload.get("h", "")):
        CODE_FAILURES.failed(key)
        raise HTTPException(status_code=401, detail="That code is not correct. Please check the message and try again.")
    CODE_FAILURES.reset(key)
    patient = db.get(models.Patient, patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found.")
    token = auth.sign({"t": PURPOSE_EDIT, "pid": patient.id, "exp": int(time.time()) + EDIT_TTL_SECONDS})
    log.info("patient %s confirmed the update code", patient.mrn)
    return schemas.PatientUpdateSession(
        edit_token=token, expires_in=EDIT_TTL_SECONDS, patient=patients.full(db, patient)
    )


def editing_patient(
    x_edit_token: str | None = Header(default=None, alias="X-Edit-Token"), db: Session = Depends(get_db)
) -> models.Patient:
    """The record an edit token (from /confirm) is for; ``401`` when missing or expired."""
    payload = auth.verify_signed(x_edit_token or "")
    if not payload or payload.get("t") != PURPOSE_EDIT:
        raise HTTPException(status_code=401, detail="Your editing session has expired. Please start again.")
    patient = db.get(models.Patient, int(payload["pid"]))
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found.")
    return patient


@router.get("/me", response_model=schemas.PatientOut)
def current_record(patient: models.Patient = Depends(editing_patient), db: Session = Depends(get_db)):
    return patients.full(db, patient)


@router.put("", response_model=schemas.PatientOut)
def save_by_code(
    body: schemas.PatientUpdate, patient: models.Patient = Depends(editing_patient), db: Session = Depends(get_db)
):
    """Save the editable part of the record (see ``schemas.PatientUpdate``)."""
    return _save(db, patient, body)


# ---------------------------------------------------------------------------- signed-in patient account


def _own_record(db: Session, account: models.Account, patient_id: int) -> models.Patient:
    patient = db.get(models.Patient, patient_id)
    if patient is None or patient.account_id != account.id:
        raise HTTPException(
            status_code=404,
            detail="This is not one of your records. Attach it on the My appointments page first.",
        )
    return patient


@me_router.get("/{patient_id}", response_model=schemas.PatientOut)
def my_record(patient_id: int, p: auth.Principal = Depends(auth.patient_principal), db: Session = Depends(get_db)):
    """One of the account's records, in full (to fill the update form)."""
    return patients.full(db, _own_record(db, p.account, patient_id))


@me_router.put("/{patient_id}", response_model=schemas.PatientOut)
def update_my_record(
    patient_id: int,
    body: schemas.PatientUpdate,
    p: auth.Principal = Depends(auth.patient_principal),
    db: Session = Depends(get_db),
):
    """Save the editable part of one of the account's records."""
    return _save(db, _own_record(db, p.account, patient_id), body)


# ---------------------------------------------------------------------------- shared


def _save(db: Session, patient: models.Patient, body: schemas.PatientUpdate) -> schemas.PatientOut:
    try:
        cards = patients.decode_cards(body)
    except insurance.InsuranceCardError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    insurance_changed = patients.apply_update(db, patient, body, cards)
    if insurance_changed and config.INSURANCE_VERIFY_ON_REGISTRATION and patients.has_insurance(patient):
        patients.verify_insurance(db, patient, "patient")
    db.commit()
    log.info("patient %s updated their details (insurance changed: %s)", patient.mrn, insurance_changed)
    return patients.full(db, patient)
