"""Workers — the medical office's staff roster (doctors, nurses, …) and their weekly availability.

    POST /api/v1/workers/check                 is this person already on the roster?
    POST /api/v1/workers                       register: creates the record when new (201) — with the
                                               office's standard week — otherwise the existing summary (200)
    POST /api/v1/workers/availability/lookup   staff number + work e-mail → the worker's week
    PUT  /api/v1/workers/availability          staff number + work e-mail + the new week → saved week
    GET  /api/v1/workers                       search/list the roster          (administrators)
    GET  /api/v1/workers/{id}                  one full record + availability  (administrators)
    PUT  /api/v1/workers/{id}/availability     a manager edits the week        (administrators)

How "the same worker" is decided (``find_match``), in order:

    email       the work e-mail address agrees
    license     the professional licence number agrees (digits/letters only)
    name_phone  first + last name agree (normalised like patients.normalize_name) and the phone agrees

Availability is one row per weekday (0 = Monday … 6 = Sunday): in or out, from when to when,
an optional note. The **standard week** — Monday to Friday, 9 AM to 5 PM — is what every new
worker starts with (models.STANDARD_WORK_*); the worker adjusts it on the availability page,
identifying themself with the staff number handed out at registration plus the work e-mail.
Both public endpoints are anonymous and rate-limited; reading records needs an administrator.
"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

import auth
import models
import schemas
from database import get_db
from patients import normalize_email, normalize_name, normalize_phone, phones_match
from ratelimit import RateLimiter

log = logging.getLogger("account.workers")

router = APIRouter(prefix="/workers", tags=["workers"])

CHECK_LIMITER = RateLimiter(limit=30, window_seconds=600)
CREATE_LIMITER = RateLimiter(limit=10, window_seconds=600)
AVAILABILITY_LIMITER = RateLimiter(limit=60, window_seconds=600)

MATCH_EMAIL = "email"
MATCH_LICENSE = "license"
MATCH_NAME_PHONE = "name_phone"

_STAFF_NUMBER = re.compile(r"^\s*[Ww]?-?0*(\d{1,9})\s*$")


def normalize_license(value: str | None) -> str | None:
    """``"md-12 345"`` → ``"MD12345"``: letters and digits only, upper-cased."""
    if not value:
        return None
    cleaned = "".join(ch for ch in value if ch.isalnum()).upper()
    return cleaned or None


def parse_staff_number(value: str) -> int | None:
    """``W-000042`` / ``w42`` / ``42`` → 42."""
    m = _STAFF_NUMBER.match(value or "")
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------- the standard week


def standard_week() -> list[schemas.DayAvailability]:
    return [
        schemas.DayAvailability(
            weekday=d,
            available=d in models.STANDARD_WORK_DAYS,
            start_time=models.STANDARD_WORK_START,
            end_time=models.STANDARD_WORK_END,
        )
        for d in range(7)
    ]


def _apply_week(db: Session, worker: models.Worker, days: list[schemas.DayAvailability]) -> None:
    existing = {
        row.weekday: row
        for row in db.execute(
            select(models.WorkerAvailability).where(models.WorkerAvailability.worker_id == worker.id)
        ).scalars()
    }
    for day in days:
        row = existing.get(day.weekday)
        if row is None:
            row = models.WorkerAvailability(worker_id=worker.id, weekday=day.weekday)
            db.add(row)
        row.available = day.available
        row.start_time = day.start_time
        row.end_time = day.end_time
        row.note = day.note
    db.flush()


def availability_of(db: Session, worker: models.Worker) -> list[schemas.DayAvailability]:
    rows = {
        row.weekday: row
        for row in db.execute(
            select(models.WorkerAvailability).where(models.WorkerAvailability.worker_id == worker.id)
        ).scalars()
    }
    out = []
    for default in standard_week():  # a missing day falls back to the standard
        row = rows.get(default.weekday)
        out.append(
            schemas.DayAvailability(
                weekday=default.weekday,
                available=row.available,
                start_time=row.start_time,
                end_time=row.end_time,
                note=row.note,
            )
            if row
            else default
        )
    return out


# ---------------------------------------------------------------------------- matching


def find_match(db: Session, identity: schemas.WorkerIdentity) -> tuple[models.Worker | None, list[str]]:
    email = normalize_email(str(identity.email))
    license_ = normalize_license(identity.license_number)
    phone = normalize_phone(identity.phone)
    first = normalize_name(identity.first_name)
    last = normalize_name(identity.last_name)

    conditions = [models.Worker.email_norm == email]
    if license_:
        conditions.append(models.Worker.license_norm == license_)
    if phone:
        conditions.append((models.Worker.last_name_norm == last) & (models.Worker.first_name_norm == first))
    candidates = db.execute(select(models.Worker).where(or_(*conditions)).order_by(models.Worker.id)).scalars().all()

    best: models.Worker | None = None
    best_rules: list[str] = []
    for w in candidates:
        rules = []
        if w.email_norm == email:
            rules.append(MATCH_EMAIL)
        if license_ and w.license_norm == license_:
            rules.append(MATCH_LICENSE)
        if w.last_name_norm == last and w.first_name_norm == first and phones_match(phone, w.phone_norm):
            rules.append(MATCH_NAME_PHONE)
        if rules and (best is None or len(rules) > len(best_rules)):
            best, best_rules = w, rules
    return best, best_rules


def create(db: Session, body: schemas.WorkerIn) -> models.Worker:
    worker = models.Worker(
        first_name=body.first_name,
        last_name=body.last_name,
        role=body.role,
        specialty=body.specialty,
        license_number=body.license_number,
        email=str(body.email),
        phone=body.phone,
        start_date=body.start_date,
        employment_type=body.employment_type,
        emergency_contact_name=body.emergency_contact_name,
        emergency_contact_phone=body.emergency_contact_phone,
        notes=body.notes,
        first_name_norm=normalize_name(body.first_name),
        last_name_norm=normalize_name(body.last_name),
        email_norm=normalize_email(str(body.email)) or "",
        phone_norm=normalize_phone(body.phone),
        license_norm=normalize_license(body.license_number),
    )
    db.add(worker)
    db.flush()
    _apply_week(db, worker, standard_week())
    return worker


def summary(w: models.Worker) -> schemas.WorkerSummary:
    return schemas.WorkerSummary(
        id=w.id,
        staff_number=w.staff_number,
        first_name=w.first_name,
        last_name=w.last_name,
        role=w.role,
        created_at=w.created_at,
    )


def onboarding_state(db: Session, w: models.Worker) -> schemas.WorkerOnboardingState | None:
    o = db.execute(select(models.Onboarding).where(models.Onboarding.worker_id == w.id)).scalar_one_or_none()
    if o is None:
        return None
    items = db.execute(select(models.OnboardingItem).where(models.OnboardingItem.onboarding_id == o.id)).scalars().all()
    relevant = [i for i in items if i.status != "not_applicable"]
    done = sum(1 for i in relevant if i.status == "done")
    return schemas.WorkerOnboardingState(
        id=o.id,
        started_at=o.started_at,
        all_steps_done=not any(i.status == "pending" for i in items),
        approved_at=o.approved_at,
        completed_at=o.completed_at,
        done=done,
        total=len(relevant),
        percent=round(100 * done / len(relevant)) if relevant else 100,
    )


def full(db: Session, w: models.Worker) -> schemas.WorkerOut:
    return schemas.WorkerOut(
        **summary(w).model_dump(),
        specialty=w.specialty,
        license_number=w.license_number,
        email=w.email,
        phone=w.phone,
        start_date=w.start_date,
        employment_type=w.employment_type,
        emergency_contact_name=w.emergency_contact_name,
        emergency_contact_phone=w.emergency_contact_phone,
        notes=w.notes,
        active=w.active,
        updated_at=w.updated_at,
        availability=availability_of(db, w),
        onboarding=onboarding_state(db, w),
    )


def _caller(request: Request) -> str | None:
    return request.client.host if request.client else None


def _worker_for(db: Session, creds: schemas.WorkerCredentials) -> models.Worker:
    """The worker whose staff number *and* e-mail match — the same answer for "no such number"
    and "wrong e-mail", so the endpoint cannot be used to enumerate staff numbers."""
    worker_id = parse_staff_number(creds.staff_number)
    worker = db.get(models.Worker, worker_id) if worker_id else None
    if worker is None or not worker.active or worker.email_norm != normalize_email(str(creds.email)):
        raise HTTPException(
            status_code=404, detail="No active staff member matches this staff number and e-mail address."
        )
    return worker


# ---------------------------------------------------------------------------- public: registration


@router.post("/check", response_model=schemas.WorkerCheck)
def check(body: schemas.WorkerIdentity, request: Request, db: Session = Depends(get_db)):
    """Is this person already on the roster? Answers ``is_new`` and, for a known worker, the summary."""
    if not CHECK_LIMITER.allow(_caller(request)):
        raise HTTPException(status_code=429, detail="Too many requests. Please try again in a few minutes.")
    worker, rules = find_match(db, body)
    if worker is None:
        return schemas.WorkerCheck(is_new=True)
    return schemas.WorkerCheck(is_new=False, matched_on=rules, worker=summary(worker))


@router.post(
    "",
    response_model=schemas.WorkerRegistration,
    status_code=201,
    responses={200: {"model": schemas.WorkerRegistration, "description": "Already on the roster; nothing stored"}},
)
def register(body: schemas.WorkerIn, request: Request, response: Response, db: Session = Depends(get_db)):
    """Verify and register in one step. A new worker gets a record with the standard week
    (Mon–Fri 9–5) and their staff number; a known one gets the existing summary (``200``)."""
    if not CREATE_LIMITER.allow(_caller(request)):
        raise HTTPException(status_code=429, detail="Too many requests. Please try again in a few minutes.")
    existing, rules = find_match(db, body)
    if existing is not None:
        response.status_code = 200
        return schemas.WorkerRegistration(status="existing", matched_on=rules, worker=summary(existing))
    worker = create(db, body)
    db.commit()
    log.info("worker registered: %s (%s)", worker.staff_number, worker.role)
    return schemas.WorkerRegistration(status="new", worker=full(db, worker))


# ---------------------------------------------------------------------------- public: availability


@router.post("/availability/lookup", response_model=schemas.AvailabilityOut)
def availability_lookup(body: schemas.WorkerCredentials, request: Request, db: Session = Depends(get_db)):
    """The worker's current week (staff number + work e-mail)."""
    if not AVAILABILITY_LIMITER.allow(_caller(request)):
        raise HTTPException(status_code=429, detail="Too many requests. Please try again in a few minutes.")
    worker = _worker_for(db, body)
    return schemas.AvailabilityOut(
        worker=summary(worker), standard_week=standard_week(), availability=availability_of(db, worker)
    )


@router.put("/availability", response_model=schemas.AvailabilityOut)
def availability_update(body: schemas.AvailabilityUpdate, request: Request, db: Session = Depends(get_db)):
    """Save the week. Days not sent keep their current value."""
    if not AVAILABILITY_LIMITER.allow(_caller(request)):
        raise HTTPException(status_code=429, detail="Too many requests. Please try again in a few minutes.")
    worker = _worker_for(db, body)
    _apply_week(db, worker, body.availability)
    db.commit()
    log.info("availability updated: %s", worker.staff_number)
    return schemas.AvailabilityOut(
        worker=summary(worker), standard_week=standard_week(), availability=availability_of(db, worker)
    )


# ---------------------------------------------------------------------------- staff endpoints


@router.get("", response_model=schemas.WorkerPage)
def list_workers(
    q: str = Query(default="", max_length=120, description="Name, staff number, e-mail, role or licence"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    _p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    stmt = select(models.Worker)
    term = q.strip()
    if term:
        number = parse_staff_number(term) if re.fullmatch(r"[Ww]-?\d+", term) else None
        if number is not None:
            stmt = stmt.where(models.Worker.id == number)
        else:
            norm = normalize_name(term)
            conditions = [
                models.Worker.last_name_norm.like(f"%{norm}%"),
                models.Worker.first_name_norm.like(f"%{norm}%"),
                models.Worker.email_norm.like(f"%{term.lower()}%"),
                models.Worker.role == term.lower().replace(" ", "_"),
            ]
            if normalize_license(term):
                conditions.append(models.Worker.license_norm == normalize_license(term))
            stmt = stmt.where(or_(*conditions))
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = (
        db.execute(
            stmt.order_by(models.Worker.last_name_norm, models.Worker.first_name_norm).offset(offset).limit(limit)
        )
        .scalars()
        .all()
    )
    return schemas.WorkerPage(items=[summary(w) for w in rows], total=total, offset=offset, limit=limit)


@router.put("/{worker_id}/availability", response_model=schemas.WorkerOut)
def admin_set_availability(
    worker_id: int,
    body: schemas.AdminAvailabilityUpdate,
    _p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    """A manager edits a worker's week from the staff dashboard."""
    worker = db.get(models.Worker, worker_id)
    if worker is None:
        raise HTTPException(status_code=404, detail="Worker not found.")
    days = sorted(body.availability, key=lambda d: d.weekday)
    if len({d.weekday for d in days}) != len(days):
        raise HTTPException(status_code=422, detail="Each weekday may appear only once.")
    _apply_week(db, worker, days)
    db.commit()
    return full(db, worker)


@router.get("/{worker_id}", response_model=schemas.WorkerOut)
def get_worker(worker_id: int, _p: auth.Principal = Depends(auth.admin_principal), db: Session = Depends(get_db)):
    worker = db.get(models.Worker, worker_id)
    if worker is None:
        raise HTTPException(status_code=404, detail="Worker not found.")
    return full(db, worker)
