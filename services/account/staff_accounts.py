"""Staff accounts — members of staff sign in to the staff dashboard and see their own appointments.

    Creating an account (public, the staff dashboard's "Create your staff account"):
    POST /api/v1/staff/account/verify   staff number + work e-mail (both must match an active
                                        worker) → a 6-digit code goes to the work e-mail on the
                                        staff record; answers a signed, short-lived ``enrollment``
    POST /api/v1/staff/account          enrollment + code + the new password's client-derived keys
                                        → the account (role = staff), linked to the worker, signed in

    The signed-in member of staff (``staff_principal``: role staff — or an administrator whose
    account is linked to a worker — with an *active* worker record):
    GET  /api/v1/staff/me                          the account and the staff record
    GET  /api/v1/staff/me/appointments?from=&to=&status=   the visits where they are the provider
    GET  /api/v1/staff/me/appointments/{id}/check-in       the patient's check-in questionnaire
    GET  /api/v1/staff/me/availability             their week
    PUT  /api/v1/staff/me/availability             change it

Why a code: a staff number (W-000001, W-000002, …) and a work e-mail address are easy to know or
guess; the code proves the person reads that mailbox before an account is tied to the record. The
code is never stored — the ``enrollment`` token (signed with TOKEN_SECRET, valid
TWO_FACTOR_CODE_TTL_SECONDS) carries a keyed hash of it; wrong codes are counted per worker
(TWO_FACTOR_MAX_FAILURES within TWO_FACTOR_FAILURE_WINDOW_SECONDS → 429).

A staff account can never see the manager tabs (Applications, Onboarding, Roster — those need the
admin role) nor sign in to the patients' app. A worker who is made inactive on the roster keeps
the account but can no longer use it (403) until reactivated.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

import accounts
import appointments
import auth
import billing
import check_ins
import config
import messaging
import models
import schemas
import twofactor
import workers
from database import get_db
from ratelimit import FailureLimiter, RateLimiter

log = logging.getLogger("account.staff_accounts")

router = APIRouter(prefix="/staff", tags=["staff"])

ENROLL = "staff-enroll"
VERIFY_LIMITER = RateLimiter(limit=10, window_seconds=600)  # per caller address
CREATE_LIMITER = RateLimiter(limit=20, window_seconds=600)
CODE_FAILURES = FailureLimiter(config.TWO_FACTOR_MAX_FAILURES, config.TWO_FACTOR_FAILURE_WINDOW_SECONDS)
DEFAULT_DAYS = 14  # the appointment list: from today, two weeks


def _caller(request: Request) -> str | None:
    return request.client.host if request.client else None


def _code_hash(worker_id: int, code: str) -> str:
    msg = f"{ENROLL}:{worker_id}:{code.strip()}".encode()
    return hmac.new(config.TOKEN_SECRET.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def _account_with_email(db: Session, email: str) -> models.Account | None:
    return db.execute(select(models.Account).where(models.Account.email == email.lower())).scalar_one_or_none()


def _linked_account(db: Session, w: models.Worker) -> models.Account | None:
    account = db.get(models.Account, w.account_id) if w.account_id else None
    return account if account is not None and account.deleted_at is None else None


def _check_enrollable(db: Session, w: models.Worker) -> None:
    if _linked_account(db, w) is not None:
        raise HTTPException(
            status_code=409,
            detail="You already have a staff account — sign in with your work e-mail and password.",
        )
    if _account_with_email(db, w.email_norm) is not None:
        raise HTTPException(
            status_code=409,
            detail="An account with this e-mail address already exists. Please sign in with it, or ask the "
            "practice manager to link it to your staff record.",
        )


# ---------------------------------------------------------------------------- creating an account


@router.post("/account/verify", response_model=schemas.StaffEnrollmentSent)
def start_enrollment(body: schemas.StaffEnrollmentStart, request: Request, db: Session = Depends(get_db)):
    """Step 1: staff number + work e-mail → a code to the work e-mail on the staff record."""
    if not VERIFY_LIMITER.allow(_caller(request)):
        raise HTTPException(status_code=429, detail="Too many requests. Please try again in a few minutes.")
    w = workers._worker_for(db, body)  # 404, the same for a wrong number and a wrong e-mail
    _check_enrollable(db, w)
    code = f"{secrets.randbelow(10**6):06d}"
    ttl = config.TWO_FACTOR_CODE_TTL_SECONDS
    enrollment = auth.sign(
        {
            "typ": ENROLL,
            "wid": w.id,
            "email": w.email_norm,
            "ch": _code_hash(w.id, code),
            "nonce": secrets.token_hex(6),
            "exp": int(time.time()) + ttl,
        }
    )
    channel = messaging.channel(messaging.EMAIL)
    if not channel.available:
        raise HTTPException(status_code=503, detail="E-mail is not set up on this server — ask the practice manager.")
    subject = f"Your {config.OFFICE_NAME} staff account code: {code}"
    text = "\n".join(
        [
            f"Hi {w.first_name},",
            "",
            f"your code to create your staff dashboard account is:  {code}",
            "",
            f"It is valid for {ttl // 60} minutes. If you did not ask for it, you can ignore this message.",
        ]
    )
    try:
        channel.send(w.email, text, subject=subject)
    except messaging.MessagingError as exc:
        raise HTTPException(status_code=502, detail=f"The code could not be sent: {exc}") from None
    log.info("staff enrollment code sent: %s", w.staff_number)
    return schemas.StaffEnrollmentSent(
        enrollment=enrollment,
        sent_to=twofactor.mask_email(w.email) or "",
        expires_in=ttl,
        fake=channel.fake,
        outbox_url=messaging.outbox_url() if channel.fake else None,
    )


@router.post("/account", response_model=schemas.Session, status_code=201)
def create_account(body: schemas.StaffAccountCreate, request: Request, db: Session = Depends(get_db)):
    """Step 2: the code and the new password's keys → the staff account, linked and signed in."""
    if not CREATE_LIMITER.allow(_caller(request)):
        raise HTTPException(status_code=429, detail="Too many requests. Please try again in a few minutes.")
    payload = auth.verify_signed(body.enrollment)
    if not payload or payload.get("typ") != ENROLL:
        raise HTTPException(status_code=400, detail="This sign-up has expired. Please start again.")
    worker_id = int(payload["wid"])
    key = f"{ENROLL}:{worker_id}"
    if CODE_FAILURES.blocked(key):
        wait = CODE_FAILURES.retry_after(key)
        raise HTTPException(
            status_code=429,
            detail=f"Too many wrong codes. Please wait {wait} seconds and start again.",
            headers={"Retry-After": str(wait)},
        )
    if not hmac.compare_digest(_code_hash(worker_id, body.code), str(payload.get("ch", ""))):
        CODE_FAILURES.failed(key)
        raise HTTPException(status_code=401, detail="That code is not right. Please check the e-mail and try again.")
    w = db.get(models.Worker, worker_id)
    if w is None or not w.active or w.email_norm != payload.get("email"):
        raise HTTPException(status_code=400, detail="This sign-up is no longer valid. Please start again.")
    _check_enrollable(db, w)
    CODE_FAILURES.reset(key)

    salt = auth.new_salt()
    account = models.Account(
        email=w.email_norm,
        auth_salt=salt,
        auth_hash=auth.hash_auth_key(body.auth_key, salt),
        wrapped_data_key=body.wrapped_data_key,
        kdf_params=json.dumps(body.kdf_params),
        role=models.ROLE_STAFF,
    )
    db.add(account)
    db.flush()
    w.account_id = account.id
    device = models.Device(
        account_id=account.id,
        platform=body.device.platform,
        client_version=body.device.client_version,
        schema_version=body.device.schema_version,
        name_ciphertext=body.device.name_ciphertext,
    )
    db.add(device)
    db.flush()
    billing.subscription_for(db, account)
    accounts.audit(db, account, "account.staff_enroll", account, worker=w.staff_number)
    db.commit()
    log.info("staff account created for %s (%s)", w.staff_number, w.role)
    return schemas.Session(
        token=auth.issue_token(account.id, device.id, device.token_generation),
        account_id=account.id,
        device_id=device.id,
        email=account.email,
        wrapped_data_key=account.wrapped_data_key,
        kdf_params=json.loads(account.kdf_params or "{}"),
        vault_enabled=account.vault_enabled,
        has_password=True,
    )


# ---------------------------------------------------------------------------- the signed-in member of staff


@dataclass
class StaffPrincipal:
    principal: auth.Principal
    worker: models.Worker


def linked_worker(db: Session, account: models.Account) -> models.Worker | None:
    return db.execute(select(models.Worker).where(models.Worker.account_id == account.id)).scalar_one_or_none()


def staff_principal(p: auth.Principal = Depends(auth.current_principal), db: Session = Depends(get_db)):
    """A signed-in staff account (or an administrator linked to a worker) with an active record."""
    if p.account.role not in (models.ROLE_STAFF, models.ROLE_ADMIN):
        raise HTTPException(status_code=403, detail="This needs a staff account.")
    w = linked_worker(db, p.account)
    if w is None:
        raise HTTPException(
            status_code=403,
            detail="This account is not linked to a staff record. Staff create their account on the staff "
            "dashboard with their staff number and work e-mail.",
        )
    if not w.active:
        raise HTTPException(
            status_code=403,
            detail="Your staff record is inactive, so this account cannot be used. Please ask the practice manager.",
        )
    return StaffPrincipal(principal=p, worker=w)


@router.get("/me", response_model=schemas.StaffMe)
def staff_me(s: StaffPrincipal = Depends(staff_principal), db: Session = Depends(get_db)):
    return schemas.StaffMe(
        account_id=s.principal.account.id,
        email=s.principal.account.email,
        role=s.principal.account.role,
        worker=workers.full(db, s.worker),
        timezone=str(appointments.office_tz()),
    )


def _office_day(dt: datetime) -> date:
    return appointments.as_utc(dt).astimezone(appointments.office_tz()).date()


@router.get("/me/appointments", response_model=schemas.StaffAppointmentPage)
def my_appointments(
    date_from: date | None = Query(default=None, alias="from", description="First day (office time zone)"),
    date_to: date | None = Query(default=None, alias="to", description="Last day, inclusive"),
    status: str | None = Query(default=None, pattern="^(scheduled|canceled|completed|no_show)$"),
    s: StaffPrincipal = Depends(staff_principal),
    db: Session = Depends(get_db),
):
    """The visits where the signed-in worker is the provider, in time order — today and the next
    two weeks unless a period is given. Each carries the patient, the reason, the status and the
    check-in (pending · failed: why · checked in). Payment details are left out."""
    tz = appointments.office_tz()
    today = datetime.now(timezone.utc).astimezone(tz).date()
    start = date_from or today
    end = date_to or (start + timedelta(days=DEFAULT_DAYS - 1))
    if end < start:
        raise HTTPException(status_code=422, detail="to: must not be before from")
    if (end - start).days > 366:
        raise HTTPException(status_code=422, detail="Please ask for at most one year at a time.")
    begin = datetime.combine(start, datetime.min.time(), tz)
    finish = datetime.combine(end + timedelta(days=1), datetime.min.time(), tz)
    q = select(models.Appointment).where(
        models.Appointment.provider_id == s.worker.id,
        models.Appointment.start_at >= begin,
        models.Appointment.start_at < finish,
    )
    if status:
        q = q.where(models.Appointment.status == status)
    rows = db.execute(q.order_by(models.Appointment.start_at, models.Appointment.id)).scalars().all()
    items = [
        appointments.appointment_out(db, a, with_link=False, staff=True).model_copy(update={"charge": None})
        for a in rows
    ]
    db.commit()  # a check-in that just timed out is stored as failed
    return schemas.StaffAppointmentPage(
        worker=workers.summary(s.worker),
        timezone=str(tz),
        date_from=start,
        date_to=end,
        items=items,
        total=len(items),
    )


@router.get("/me/appointments/{appointment_id}/check-in", response_model=schemas.StaffCheckInAnswers)
def my_appointment_check_in(
    appointment_id: int, s: StaffPrincipal = Depends(staff_principal), db: Session = Depends(get_db)
):
    """The patient's answers to the check-in questionnaire for one of the worker's own visits."""
    a = db.get(models.Appointment, appointment_id)
    if a is None or a.provider_id != s.worker.id:
        raise HTTPException(status_code=404, detail="Appointment not found.")
    done = next((c for c in check_ins.history(db, a) if c.status == "completed"), None)
    if done is None:
        raise HTTPException(status_code=404, detail="The patient has not checked in for this appointment yet.")
    try:
        answers = json.loads(done.answers or "{}")
    except ValueError:
        answers = {}
    return schemas.StaffCheckInAnswers(
        appointment_id=a.id,
        completed_at=done.completed_at,
        via=done.via,
        questions=check_ins.questionnaire().questions,
        answers=answers,
        signer_name=done.answers_signer_name,
        signed_at=done.answers_signed_at,
    )


@router.get("/me/availability", response_model=schemas.AvailabilityOut)
def my_availability(s: StaffPrincipal = Depends(staff_principal), db: Session = Depends(get_db)):
    return schemas.AvailabilityOut(
        worker=workers.summary(s.worker),
        standard_week=workers.standard_week(),
        availability=workers.availability_of(db, s.worker),
    )


@router.put("/me/availability", response_model=schemas.AvailabilityOut)
def update_my_availability(
    body: schemas.StaffAvailabilityUpdate, s: StaffPrincipal = Depends(staff_principal), db: Session = Depends(get_db)
):
    workers._apply_week(db, s.worker, body.availability)
    db.commit()
    log.info("availability updated by %s (signed in)", s.worker.staff_number)
    return my_availability(s, db)
