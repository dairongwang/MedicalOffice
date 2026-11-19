"""Hiring — job applications with pre-hire checks, and the onboarding checklist of each hire.

Served to the **staff dashboard** (apps/staff — its own deployment, talking to this API across
origins). Public, rate-limited:

    POST /api/v1/applications                 a candidate applies (age check runs at once, licence
                                              details are validated for format/expiry)
    POST /api/v1/applications/status          candidate: application number + e-mail → status

Administrators (managers):

    GET   /api/v1/applications?status=&q=     the queue
    GET   /api/v1/applications/{id}           one application with its three checks
    PUT   /api/v1/applications/{id}/checks/license      confirm with the issuing board (passed/failed + note)
    PUT   /api/v1/applications/{id}/checks/background   record the screening (in_progress/passed/failed + ref)
    POST  /api/v1/applications/{id}/hire      all checks passed → Worker (roster, standard week) + Onboarding
    POST  /api/v1/applications/{id}/reject    with a note
    GET   /api/v1/onboarding                  hires and their progress
    GET   /api/v1/onboarding/{id}             the checklist
    PUT   /api/v1/onboarding/{id}/items/{key} pending | done | not_applicable (+ note)
    PUT   /api/v1/onboarding/{id}/notes

The three checks (``JobApplication.*_check``):

    age         automatic: MIN_HIRING_AGE (18) at the time of application → passed / failed
    license     roles in LICENSED_ROLES need a number; format (4–32 letters/digits), an expiry date
                in the future → ``in_progress`` ("looks valid — confirm with the board"); a manager
                sets passed / failed. Other roles: not_applicable.
    background  needs the candidate's consent (given in the form); a manager records in_progress →
                passed (clear) / failed (flagged) with the provider's reference.

A hire needs age = passed, licence = passed or not_applicable, background = passed. Hiring
creates the roster record (workers.create) with the standard week and the onboarding checklist
(models.ONBOARDING_STEPS); "employment application" and "background screening" start as done,
and "emergency contact" when the application carried one.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

import auth
import config
import invites
import models
import onboarding_docs as docs
import schemas
import workers
from database import get_db
from patients import normalize_email
from ratelimit import RateLimiter

log = logging.getLogger("account.hiring")

router = APIRouter(tags=["hiring"])

APPLY_LIMITER = RateLimiter(limit=10, window_seconds=600)
STATUS_LIMITER = RateLimiter(limit=30, window_seconds=600)

_LICENSE_FORMAT = re.compile(r"^[A-Z0-9]{4,32}$")
_NUMBER = re.compile(r"^\s*[Aa]?-?0*(\d{1,9})\s*$")

CHECK_KEYS = ("age", "license", "background")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def parse_number(value: str) -> int | None:
    m = _NUMBER.match(value or "")
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------- the checks


def age_on(dob: date, day: date | None = None) -> int:
    day = day or date.today()
    years = day.year - dob.year
    if (day.month, day.day) < (dob.month, dob.day):
        years -= 1
    return years


def age_check(dob: date, today: date | None = None) -> tuple[str, str]:
    """(status, note) — the automatic age validation."""
    age = age_on(dob, today)
    if age < config.MIN_HIRING_AGE:
        return "failed", f"Applicant is {age}; the minimum hiring age is {config.MIN_HIRING_AGE}."
    return "passed", f"Applicant is {age} (minimum {config.MIN_HIRING_AGE})."


def license_check(
    role: str, number: str | None, authority: str | None, expires: date | None, today: date | None = None
) -> tuple[str, str]:
    """(status, note) — the automatic part of the licence validation. ``in_progress`` means the
    details look right and a manager must confirm them with the issuing board."""
    today = today or date.today()
    if role not in models.LICENSED_ROLES:
        return "not_applicable", "This role does not require a professional licence."
    if not number:
        return "failed", "A professional licence / registration number is required for this role."
    normalized = workers.normalize_license(number) or ""
    if not _LICENSE_FORMAT.match(normalized):
        return "failed", "The licence number must be 4–32 letters/digits."
    if expires is None:
        return "in_progress", "Licence number recorded; no expiry date given — confirm validity with the issuing board."
    if expires < today:
        return "failed", f"The licence expired on {expires.isoformat()}."
    board = authority or "the issuing board"
    if (expires - today).days <= 90:
        return "in_progress", f"Licence expires soon ({expires.isoformat()}) — confirm renewal with {board}."
    return "in_progress", f"Licence looks valid until {expires.isoformat()} — confirm with {board}."


def blockers(app: models.JobApplication) -> list[str]:
    out = []
    if app.status in ("hired", "rejected", "withdrawn"):
        out.append(f"The application is already {app.status}.")
    if app.age_check != "passed":
        out.append("Age validation has not passed.")
    if app.license_check not in ("passed", "not_applicable"):
        out.append("The licence has not been confirmed with the issuing board.")
    if app.background_check != "passed":
        out.append("The background screening is not clear yet.")
    return out


# ---------------------------------------------------------------------------- shaping


def _email_of(db: Session, account_id: str | None) -> str | None:
    if not account_id:
        return None
    account = db.get(models.Account, account_id)
    return account.email if account else None


def summary(a: models.JobApplication) -> schemas.ApplicationSummary:
    return schemas.ApplicationSummary(
        id=a.id,
        number=a.number,
        first_name=a.first_name,
        last_name=a.last_name,
        role=a.role,
        specialty=a.specialty,
        status=a.status,
        age_check=a.age_check,
        license_check=a.license_check,
        background_check=a.background_check,
        worker_id=a.worker_id,
        created_at=a.created_at,
        updated_at=a.updated_at,
    )


def full(db: Session, a: models.JobApplication) -> schemas.ApplicationOut:
    worker = db.get(models.Worker, a.worker_id) if a.worker_id else None
    onboarding = (
        db.execute(select(models.Onboarding).where(models.Onboarding.worker_id == worker.id)).scalar_one_or_none()
        if worker
        else None
    )
    return schemas.ApplicationOut(
        **summary(a).model_dump(),
        email=a.email,
        phone=a.phone,
        date_of_birth=a.date_of_birth,
        age=age_on(a.date_of_birth),
        employment_type=a.employment_type,
        available_from=a.available_from,
        years_experience=a.years_experience,
        license_number=a.license_number,
        license_authority=a.license_authority,
        license_expires=a.license_expires,
        certifications=a.certifications,
        cover_note=a.cover_note,
        emergency_contact_name=a.emergency_contact_name,
        emergency_contact_phone=a.emergency_contact_phone,
        background_consent=a.background_consent,
        checks={
            "age": schemas.CheckResult(status=a.age_check, note=a.age_note, checked_at=a.created_at),
            "license": schemas.CheckResult(
                status=a.license_check,
                note=a.license_note,
                checked_at=a.license_checked_at,
                checked_by=_email_of(db, a.license_checked_by),
            ),
            "background": schemas.CheckResult(
                status=a.background_check,
                note=a.background_note,
                reference=a.background_reference,
                checked_at=a.background_checked_at,
                checked_by=_email_of(db, a.background_checked_by),
            ),
        },
        can_hire=not blockers(a),
        blockers=blockers(a),
        decision_note=a.decision_note,
        decided_at=a.decided_at,
        decided_by=_email_of(db, a.decided_by),
        staff_number=worker.staff_number if worker else None,
        onboarding_id=onboarding.id if onboarding else None,
        last_invite=(
            invites.out(db, invites.for_onboarding(db, onboarding.id)[0])
            if onboarding and invites.for_onboarding(db, onboarding.id)
            else None
        ),
    )


def _get(db: Session, application_id: int) -> models.JobApplication:
    app = db.get(models.JobApplication, application_id)
    if app is None:
        raise HTTPException(status_code=404, detail="Application not found.")
    return app


def _caller(request: Request) -> str | None:
    return request.client.host if request.client else None


# ---------------------------------------------------------------------------- public


@router.post("/applications", response_model=schemas.ApplicationReceipt, status_code=201)
def apply(body: schemas.ApplicationIn, request: Request, db: Session = Depends(get_db)):
    """A candidate applies. The age check runs now; the licence details are pre-validated."""
    if not APPLY_LIMITER.allow(_caller(request)):
        raise HTTPException(status_code=429, detail="Too many requests. Please try again in a few minutes.")
    age_status, age_note = age_check(body.date_of_birth)
    lic_status, lic_note = license_check(body.role, body.license_number, body.license_authority, body.license_expires)
    app = models.JobApplication(
        first_name=body.first_name,
        last_name=body.last_name,
        email=str(body.email),
        phone=body.phone,
        date_of_birth=body.date_of_birth,
        role=body.role,
        specialty=body.specialty,
        employment_type=body.employment_type,
        available_from=body.available_from,
        years_experience=body.years_experience,
        license_number=body.license_number,
        license_authority=body.license_authority,
        license_expires=body.license_expires,
        certifications=body.certifications,
        cover_note=body.cover_note,
        emergency_contact_name=body.emergency_contact_name,
        emergency_contact_phone=body.emergency_contact_phone,
        background_consent=body.background_consent,
        status="screening" if age_status == "passed" else "submitted",
        age_check=age_status,
        age_note=age_note,
        license_check=lic_status,
        license_note=lic_note,
        background_check="pending",
        email_norm=normalize_email(str(body.email)) or "",
    )
    db.add(app)
    db.commit()
    log.info("application received: %s (%s)", app.number, app.role)
    if age_status == "failed":
        message = (
            f"Thank you, {app.first_name}. Unfortunately we can only consider applicants aged "
            f"{config.MIN_HIRING_AGE} or over. Your application ({app.number}) has been recorded."
        )
    elif lic_status == "failed":
        message = (
            f"Thank you, {app.first_name}. Your application {app.number} was received, but there is a problem "
            f"with the licence details — a manager will get in touch."
        )
    else:
        message = (
            f"Thank you, {app.first_name}! Your application {app.number} was received. We will verify your "
            f"credentials and run the background screening you agreed to, then get back to you."
        )
    return schemas.ApplicationReceipt(
        number=app.number,
        status=app.status,
        age_check=app.age_check,
        age_note=app.age_note,
        license_check=app.license_check,
        license_note=app.license_note,
        background_check=app.background_check,
        message=message,
    )


STATUS_MESSAGES = {
    "submitted": "Your application was received but cannot proceed (see the checks).",
    "screening": "We are verifying your credentials and running the background screening.",
    "hired": "Congratulations — you have been hired! Your onboarding has started; the office will contact you.",
    "rejected": "We are sorry: the office did not proceed with this application.",
    "withdrawn": "This application was withdrawn.",
}


@router.post("/applications/status", response_model=schemas.ApplicationStatusOut)
def application_status(body: schemas.ApplicationStatusLookup, request: Request, db: Session = Depends(get_db)):
    """Candidate: application number + e-mail → where things stand (same 404 for either being wrong)."""
    if not STATUS_LIMITER.allow(_caller(request)):
        raise HTTPException(status_code=429, detail="Too many requests. Please try again in a few minutes.")
    number = parse_number(body.number)
    app = db.get(models.JobApplication, number) if number else None
    if app is None or app.email_norm != normalize_email(str(body.email)):
        raise HTTPException(status_code=404, detail="No application matches this number and e-mail address.")
    worker = db.get(models.Worker, app.worker_id) if app.worker_id else None
    return schemas.ApplicationStatusOut(
        number=app.number,
        first_name=app.first_name,
        role=app.role,
        status=app.status,
        age_check=app.age_check,
        license_check=app.license_check,
        background_check=app.background_check,
        submitted_at=app.created_at,
        updated_at=app.updated_at,
        staff_number=worker.staff_number if worker else None,
        message=STATUS_MESSAGES.get(app.status, ""),
    )


# ---------------------------------------------------------------------------- managers: applications


@router.get("/applications", response_model=schemas.ApplicationPage)
def list_applications(
    status: str | None = Query(default=None, pattern="^(submitted|screening|hired|rejected|withdrawn|open)$"),
    q: str = Query(default="", max_length=120),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    _p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    """``status=open`` = submitted + screening (the queue)."""
    stmt = select(models.JobApplication)
    if status == "open":
        stmt = stmt.where(models.JobApplication.status.in_(("submitted", "screening")))
    elif status:
        stmt = stmt.where(models.JobApplication.status == status)
    term = q.strip()
    if term:
        number = parse_number(term) if re.fullmatch(r"[Aa]-?\d+", term) else None
        if number is not None:
            stmt = stmt.where(models.JobApplication.id == number)
        else:
            like = f"%{term.lower()}%"
            stmt = stmt.where(
                or_(
                    func.lower(models.JobApplication.first_name).like(like),
                    func.lower(models.JobApplication.last_name).like(like),
                    models.JobApplication.email_norm.like(like),
                    models.JobApplication.role == term.lower().replace(" ", "_"),
                )
            )
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(stmt.order_by(models.JobApplication.created_at.desc()).offset(offset).limit(limit)).scalars()
    counts = {
        s: n
        for s, n in db.execute(
            select(models.JobApplication.status, func.count()).group_by(models.JobApplication.status)
        ).all()
    }
    return schemas.ApplicationPage(
        items=[summary(a) for a in rows], total=total, offset=offset, limit=limit, counts=counts
    )


@router.get("/applications/{application_id}", response_model=schemas.ApplicationOut)
def get_application(
    application_id: int, _p: auth.Principal = Depends(auth.admin_principal), db: Session = Depends(get_db)
):
    return full(db, _get(db, application_id))


@router.put("/applications/{application_id}/checks/{check}", response_model=schemas.ApplicationOut)
def update_check(
    application_id: int,
    check: str,
    body: schemas.CheckUpdate,
    p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    """Record the outcome of the licence or background check (the age check is automatic)."""
    app = _get(db, application_id)
    if app.status in ("hired", "rejected", "withdrawn"):
        raise HTTPException(status_code=409, detail=f"The application is already {app.status}.")
    now = _now()
    if check == "license":
        if app.license_check == "not_applicable" and body.status != "not_applicable":
            raise HTTPException(status_code=409, detail="This role does not require a licence.")
        app.license_check = body.status
        app.license_note = body.note or app.license_note
        app.license_checked_at, app.license_checked_by = now, p.account.id
    elif check == "background":
        if not app.background_consent:
            raise HTTPException(status_code=409, detail="The candidate has not consented to a background screening.")
        app.background_check = body.status
        app.background_note = body.note or app.background_note
        app.background_reference = body.reference or app.background_reference
        app.background_checked_at, app.background_checked_by = now, p.account.id
    elif check == "age":
        raise HTTPException(status_code=409, detail="The age check is automatic (date of birth).")
    else:
        raise HTTPException(status_code=404, detail="Unknown check.")
    db.commit()
    return full(db, app)


def _start_onboarding(db: Session, worker: models.Worker, app: models.JobApplication | None) -> models.Onboarding:
    onboarding = models.Onboarding(worker_id=worker.id, application_id=app.id if app else None)
    db.add(onboarding)
    db.flush()
    prefilled = set()
    if app is not None:
        prefilled.add("employment_application")
        if app.background_check == "passed":
            prefilled.add("background_screening")
        if app.emergency_contact_name:
            prefilled.add("emergency_contact")
    now = _now()
    for position, (key, _label) in enumerate(models.ONBOARDING_STEPS):
        done = key in prefilled
        db.add(
            models.OnboardingItem(
                onboarding_id=onboarding.id,
                key=key,
                position=position,
                status="done" if done else "pending",
                note="From the job application" if done else None,
                completed_at=now if done else None,
            )
        )
    db.flush()
    return onboarding


@router.post("/applications/{application_id}/hire", response_model=schemas.ApplicationOut)
def hire(
    application_id: int,
    body: schemas.DecisionIn,
    request: Request,
    p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    """All three checks passed → the person joins the roster (standard week) and onboarding starts."""
    app = _get(db, application_id)
    problems = blockers(app)
    if problems:
        raise HTTPException(status_code=409, detail=" ".join(problems))
    existing, _rules = workers.find_match(
        db,
        schemas.WorkerIdentity(
            first_name=app.first_name,
            last_name=app.last_name,
            email=app.email,
            phone=app.phone,
            license_number=app.license_number,
        ),
    )
    if existing is not None:
        worker = existing  # already on the roster (e.g. re-hired): reuse the record
    else:
        worker = workers.create(
            db,
            schemas.WorkerIn(
                first_name=app.first_name,
                last_name=app.last_name,
                email=app.email,
                phone=app.phone,
                license_number=app.license_number,
                role=app.role,
                specialty=app.specialty,
                start_date=body.start_date or app.available_from,
                employment_type=body.employment_type or app.employment_type,
                emergency_contact_name=app.emergency_contact_name,
                emergency_contact_phone=app.emergency_contact_phone,
                notes=f"Hired from application {app.number}",
                consent=True,
            ),
        )
    worker.active = True
    onboarding = db.execute(
        select(models.Onboarding).where(models.Onboarding.worker_id == worker.id)
    ).scalar_one_or_none()
    if onboarding is None:
        onboarding = _start_onboarding(db, worker, app)
    if onboarding.completed_at is None:  # the new hire gets the portal link by e-mail / text
        invites.create_and_send(db, onboarding, worker, kind="hire", actor=p.account, request=request)
    app.status = "hired"
    app.worker_id = worker.id
    app.decision_note = body.note
    app.decided_at, app.decided_by = _now(), p.account.id
    db.add(
        models.AuditLog(
            actor_account_id=p.account.id,
            action="hiring.hire",
            detail=f'{{"application": "{app.number}", "worker": "{worker.staff_number}"}}',
        )
    )
    db.commit()
    log.info("hired: %s → %s", app.number, worker.staff_number)
    return full(db, app)


@router.post("/applications/{application_id}/reject", response_model=schemas.ApplicationOut)
def reject(
    application_id: int,
    body: schemas.DecisionIn,
    p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    app = _get(db, application_id)
    if app.status in ("hired", "rejected", "withdrawn"):
        raise HTTPException(status_code=409, detail=f"The application is already {app.status}.")
    app.status = "rejected"
    app.decision_note = body.note
    app.decided_at, app.decided_by = _now(), p.account.id
    db.add(
        models.AuditLog(
            actor_account_id=p.account.id, action="hiring.reject", detail=f'{{"application": "{app.number}"}}'
        )
    )
    db.commit()
    return full(db, app)


# ---------------------------------------------------------------------------- managers: onboarding

LABELS = dict(models.ONBOARDING_STEPS)


def _items(db: Session, onboarding: models.Onboarding) -> list[models.OnboardingItem]:
    return (
        db.execute(
            select(models.OnboardingItem)
            .where(models.OnboardingItem.onboarding_id == onboarding.id)
            .order_by(models.OnboardingItem.position)
        )
        .scalars()
        .all()
    )


def onboarding_summary(db: Session, o: models.Onboarding) -> schemas.OnboardingSummary:
    items = _items(db, o)
    worker = db.get(models.Worker, o.worker_id)
    app = db.get(models.JobApplication, o.application_id) if o.application_id else None
    relevant = [i for i in items if i.status != "not_applicable"]
    done = sum(1 for i in relevant if i.status == "done")
    total = len(relevant)
    pending = next((i for i in items if i.status == "pending"), None)
    return schemas.OnboardingSummary(
        id=o.id,
        worker=workers.summary(worker),
        application_number=app.number if app else None,
        started_at=o.started_at,
        all_steps_done=pending is None,
        steps_completed_at=o.steps_completed_at,
        approved_at=o.approved_at,
        approved_by=_email_of(db, o.approved_by),
        approval_signer_name=o.approval_signer_name,
        approval_esign_reference=o.approval_esign_reference,
        completed_at=o.completed_at,
        done=done,
        total=total,
        percent=round(100 * done / total) if total else 100,
        next_step=LABELS.get(pending.key, pending.key) if pending else None,
    )


def onboarding_full(db: Session, o: models.Onboarding) -> schemas.OnboardingOut:
    import portal  # the portal shapes submissions; it imports this module

    subs = portal.submissions_of(db, o)
    return schemas.OnboardingOut(
        **onboarding_summary(db, o).model_dump(),
        notes=o.notes,
        items=[
            schemas.OnboardingItemOut(
                key=i.key,
                label=LABELS.get(i.key, i.key),
                position=i.position,
                status=i.status,
                note=i.note,
                completed_at=i.completed_at,
                completed_by=_email_of(db, i.completed_by)
                or ("the new hire (online)" if i.completed_at and i.key in subs else None),
                who=(docs.step(i.key).who if docs.step(i.key) else "office"),
                submission=portal.submission_out(subs[i.key]) if i.key in subs else None,
            )
            for i in _items(db, o)
        ],
        invites=[invites.out(db, inv) for inv in invites.for_onboarding(db, o.id)],
    )


def refresh_completion(db: Session, o: models.Onboarding) -> None:
    """Keep ``steps_completed_at`` in step with the items. Reopening a step after the manager's
    approval withdraws that approval: the onboarding is no longer complete until signed again."""
    pending = any(i.status == "pending" for i in _items(db, o))
    if pending:
        if o.approved_at is not None:
            log.info("onboarding %s: a step was reopened — approval withdrawn", o.id)
        o.steps_completed_at = None
        o.approved_at = o.approved_by = o.approval_signer_name = None
        o.approval_signature_png = o.approval_esign_reference = o.approval_esign_provider = None
        o.completed_at = None
    else:
        o.steps_completed_at = o.steps_completed_at or _now()
        o.completed_at = o.approved_at  # complete only once a manager signed off


def _get_onboarding(db: Session, onboarding_id: int) -> models.Onboarding:
    o = db.get(models.Onboarding, onboarding_id)
    if o is None:
        raise HTTPException(status_code=404, detail="Onboarding not found.")
    return o


@router.get("/onboarding", response_model=schemas.OnboardingPage)
def list_onboarding(
    state: str = Query(default="all", pattern="^(all|in_progress|completed)$"),
    _p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    stmt = select(models.Onboarding)
    if state == "in_progress":
        stmt = stmt.where(models.Onboarding.completed_at.is_(None))
    elif state == "completed":
        stmt = stmt.where(models.Onboarding.completed_at.is_not(None))
    rows = db.execute(stmt.order_by(models.Onboarding.started_at.desc())).scalars().all()
    in_progress = db.execute(
        select(func.count()).select_from(models.Onboarding).where(models.Onboarding.completed_at.is_(None))
    ).scalar_one()
    total = db.execute(select(func.count()).select_from(models.Onboarding)).scalar_one()
    return schemas.OnboardingPage(
        items=[onboarding_summary(db, o) for o in rows],
        total=total,
        in_progress=in_progress,
        completed=total - in_progress,
    )


@router.get("/onboarding/{onboarding_id}", response_model=schemas.OnboardingOut)
def get_onboarding(
    onboarding_id: int, _p: auth.Principal = Depends(auth.admin_principal), db: Session = Depends(get_db)
):
    return onboarding_full(db, _get_onboarding(db, onboarding_id))


@router.put("/onboarding/{onboarding_id}/items/{key}", response_model=schemas.OnboardingOut)
def update_onboarding_item(
    onboarding_id: int,
    key: str,
    body: schemas.OnboardingItemUpdate,
    p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    """Tick a step off (or mark it not applicable / pending again). The onboarding is complete
    when no step is pending."""
    o = _get_onboarding(db, onboarding_id)
    item = db.execute(
        select(models.OnboardingItem).where(
            models.OnboardingItem.onboarding_id == o.id, models.OnboardingItem.key == key
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Unknown onboarding step.")
    item.status = body.status
    item.note = body.note if body.note is not None else item.note
    if body.status == "pending":
        item.completed_at, item.completed_by = None, None
    else:
        item.completed_at, item.completed_by = _now(), p.account.id
    db.flush()
    refresh_completion(db, o)
    db.commit()
    return onboarding_full(db, o)


@router.post("/onboarding/{onboarding_id}/approve", response_model=schemas.OnboardingOut)
def approve_onboarding(
    onboarding_id: int,
    body: schemas.OnboardingApproval,
    p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    """The manager signs electronically that the onboarding is finished (every step must be done).
    Only then is the onboarding *complete*; reopening a step withdraws the approval."""
    import esign  # the same e-sign strategy (fake in development) as the new hire's documents

    o = _get_onboarding(db, onboarding_id)
    if any(i.status == "pending" for i in _items(db, o)):
        raise HTTPException(status_code=409, detail="Every step must be done before the onboarding can be approved.")
    if o.approved_at is not None:
        raise HTTPException(status_code=409, detail="This onboarding is already approved.")
    sig = body.signature
    try:
        result, signed_at = esign.sign(sig.signer_name, sig.image_png, "onboarding_approval", f"ob{o.id}")
    except esign.NotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except esign.ESignError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    o.approved_at, o.approved_by = signed_at, p.account.id
    o.approval_signer_name = sig.signer_name
    o.approval_signature_png = sig.image_png
    o.approval_esign_reference, o.approval_esign_provider = result.reference, result.provider
    o.steps_completed_at = o.steps_completed_at or signed_at
    o.completed_at = signed_at
    if body.note:
        o.notes = f"{o.notes}\n\nApproval: {body.note}".strip() if o.notes else f"Approval: {body.note}"
    db.add(
        models.AuditLog(
            actor_account_id=p.account.id,
            action="onboarding.approve",
            detail=f'{{"onboarding": {o.id}, "esign": "{result.reference}"}}',
        )
    )
    db.commit()
    log.info("onboarding %s approved by %s (%s)", o.id, p.account.email, result.reference)
    return onboarding_full(db, o)


@router.put("/onboarding/{onboarding_id}/notes", response_model=schemas.OnboardingOut)
def update_onboarding_notes(
    onboarding_id: int,
    body: schemas.OnboardingNotes,
    _p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    o = _get_onboarding(db, onboarding_id)
    o.notes = body.notes
    db.commit()
    return onboarding_full(db, o)


# The staff dashboard's roster tab may start onboarding for someone added directly to the roster.
@router.post("/workers/{worker_id}/onboarding", response_model=schemas.OnboardingOut, status_code=201)
def start_onboarding_for_worker(
    worker_id: int,
    request: Request,
    p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    """Roster → "Start onboarding": creates the checklist (if needed) and sends the portal link.
    Refused once the worker's onboarding is complete."""
    worker = db.get(models.Worker, worker_id)
    if worker is None:
        raise HTTPException(status_code=404, detail="Worker not found.")
    stmt = select(models.Onboarding).where(models.Onboarding.worker_id == worker.id)
    o = db.execute(stmt).scalar_one_or_none()
    if o is not None and o.completed_at is not None:
        raise HTTPException(status_code=409, detail="This worker has already completed onboarding.")
    if o is None:
        o = _start_onboarding(db, worker, None)
    invites.create_and_send(db, o, worker, kind="start", actor=p.account, request=request)
    db.commit()
    return onboarding_full(db, o)
