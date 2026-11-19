"""Onboarding portal — what a new hire opens from the invite link (staff dashboard, #/welcome/<token>).

Public, identified by the time-limited token only (invites.py):

    GET  /api/v1/onboarding/portal/{token}               the checklist, documents and forms, own submissions
    POST /api/v1/onboarding/portal/{token}/steps/{key}   submit a form and/or an electronic signature

Administrators:

    POST /api/v1/onboarding/{id}/invite                  send (again) — "Send reminder" / manual
    GET  /api/v1/onboarding/{id}/invites                 the log of links (for handing one over manually)
    POST /api/v1/onboarding/{id}/invites/{invite_id}/revoke
    GET  /api/v1/onboarding/{id}/submissions/{key}/signature   the signature image of a signed document

Steps the hire completes (onboarding_docs.STEPS, who = "hire") are marked **done** when the
submission is valid — except the references step, which stays pending with a note until the
office has contacted the references. Documents with ``signature = True`` need a typed name, a
drawn signature and the e-sign consent; the signature is recorded through esign.py (the fake in
development) and the document version that was shown is stored with it.

What is deliberately NOT collected online: government id / social security numbers, full bank
account numbers (last four digits only), I-9 document numbers (Section 2 is done in person).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

import auth
import esign
import hiring
import invites
import models
import onboarding_docs as docs
import schemas
import twofactor
from database import get_db
from ratelimit import RateLimiter

log = logging.getLogger("account.portal")

router = APIRouter(tags=["onboarding portal"])

PORTAL_LIMITER = RateLimiter(limit=120, window_seconds=600)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _caller(request: Request) -> str | None:
    return request.client.host if request.client else None


# ---------------------------------------------------------------------------- shaping


def document_context(worker: models.Worker) -> dict[str, str]:
    return {
        "first_name": worker.first_name,
        "last_name": worker.last_name,
        "role": worker.role.replace("_", " "),
        "specialty": f" ({worker.specialty})" if worker.specialty else "",
        "employment_type": (worker.employment_type or "full-time").replace("_", "-"),
        "start_date": worker.start_date.strftime("%B %d, %Y") if worker.start_date else "a date to be agreed",
    }


def submissions_of(db: Session, onboarding: models.Onboarding) -> dict[str, models.OnboardingSubmission]:
    rows = db.execute(
        select(models.OnboardingSubmission).where(models.OnboardingSubmission.onboarding_id == onboarding.id)
    ).scalars()
    return {r.key: r for r in rows}


def submission_out(s: models.OnboardingSubmission) -> schemas.SubmissionOut:
    try:
        data = json.loads(s.data or "{}")
    except ValueError:
        data = {}
    return schemas.SubmissionOut(
        data=data,
        document_version=s.document_version,
        signer_name=s.signer_name,
        signed_at=s.signed_at,
        esign_reference=s.esign_reference,
        esign_provider=s.esign_provider,
        submitted_at=s.submitted_at,
        has_signature=bool(s.signature_png),
    )


def state(db: Session, invite: models.OnboardingInvite) -> schemas.PortalState:
    onboarding = db.get(models.Onboarding, invite.onboarding_id)
    worker = db.get(models.Worker, onboarding.worker_id)
    items = hiring._items(db, onboarding)
    subs = submissions_of(db, onboarding)
    context = document_context(worker)
    steps = []
    for item in items:
        spec = docs.step(item.key)
        public = (
            docs.public(spec, context)
            if spec
            else {
                "key": item.key,
                "who": "office",
                "title": hiring.LABELS.get(item.key, item.key),
                "intro": "",
                "document": "",
                "version": "",
                "signature": False,
                "fields": [],
            }
        )
        sub = subs.get(item.key)
        steps.append(
            schemas.PortalStep(
                **public,
                status=item.status,
                note=item.note,
                office_note=spec.office_note if spec else "",
                submission=submission_out(sub) if sub else None,
            )
        )
    summary = hiring.onboarding_summary(db, onboarding)
    provider = esign.provider()
    return schemas.PortalState(
        worker=schemas.PortalWorker(
            first_name=worker.first_name,
            last_name=worker.last_name,
            role=worker.role,
            staff_number=worker.staff_number,
            start_date=worker.start_date,
            email=twofactor.mask_email(worker.email),
        ),
        expires_at=invite.expires_at,
        done=summary.done,
        total=summary.total,
        percent=summary.percent,
        completed_at=onboarding.completed_at,
        all_steps_done=summary.all_steps_done,
        approved_at=onboarding.approved_at,
        steps=steps,
        esign_provider=provider.name,
        esign_fake=provider.name == "fake",
    )


# ---------------------------------------------------------------------------- validation of a submission

_DIGITS4 = re.compile(r"^\d{4}$")


def validate_fields(spec: docs.Step, data: dict) -> dict:
    """Keep only the declared fields, check required/select/checkbox/number rules; return clean data."""
    clean: dict = {}
    problems: list[str] = []
    for f in spec.fields:
        raw = data.get(f.name)
        if f.type == "checkbox":
            value = bool(raw)
            if f.required and not value:
                problems.append(f"{f.label}: please tick this box.")
            clean[f.name] = value
            continue
        text = "" if raw is None else str(raw).strip()
        if not text:
            if f.required:
                problems.append(f"{f.label}: required.")
            clean[f.name] = None
            continue
        if len(text) > f.maxlength:
            problems.append(f"{f.label}: too long (max {f.maxlength}).")
        if f.type == "select" and text not in {o[0] for o in f.options}:
            problems.append(f"{f.label}: please choose one of the options.")
        if f.type == "number":
            try:
                number = float(text)
            except ValueError:
                problems.append(f"{f.label}: must be a number.")
            else:
                if number < 0 or number > 10_000_000:
                    problems.append(f"{f.label}: out of range.")
                text = str(int(number)) if number.is_integer() else str(number)
        if f.type == "date" and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            problems.append(f"{f.label}: please enter a date.")
        if f.type == "tel" and not 7 <= len(re.sub(r"\D", "", text)) <= 15:
            problems.append(f"{f.label}: please enter a phone number with 7–15 digits.")
        if f.name == "account_last4" and not _DIGITS4.match(text):
            problems.append(f"{f.label}: exactly four digits.")
        clean[f.name] = text
    # Never store what looks like a full account / government id number, whatever the field.
    for f in spec.fields:
        value = clean.get(f.name)
        if f.type in ("tel", "checkbox", "date", "number", "select") or f.name == "account_last4":
            continue
        if isinstance(value, str) and re.fullmatch(r"[\d\- ]{9,}", value):
            problems.append(f"{f.label}: please do not enter account or id numbers online.")
    if problems:
        raise HTTPException(status_code=422, detail=" ".join(problems))
    return clean


# ---------------------------------------------------------------------------- public (token)


def _invite_or_410(db: Session, token: str) -> models.OnboardingInvite:
    invite = invites.find_valid(db, token)
    if invite is None:
        raise HTTPException(
            status_code=410,
            detail="This onboarding link is no longer valid (expired or revoked). Please ask the office for a new one.",
        )
    return invite


@router.get("/onboarding/portal/{token}", response_model=schemas.PortalState)
def portal_state(token: str, request: Request, db: Session = Depends(get_db)):
    if not PORTAL_LIMITER.allow(_caller(request)):
        raise HTTPException(status_code=429, detail="Too many requests. Please try again in a few minutes.")
    invite = _invite_or_410(db, token)
    invites.mark_opened(invite)
    db.commit()
    return state(db, invite)


@router.post("/onboarding/portal/{token}/steps/{key}", response_model=schemas.PortalState)
def portal_submit(token: str, key: str, body: schemas.PortalSubmit, request: Request, db: Session = Depends(get_db)):
    """The new hire submits one step: the form fields and, for documents, the electronic signature."""
    if not PORTAL_LIMITER.allow(_caller(request)):
        raise HTTPException(status_code=429, detail="Too many requests. Please try again in a few minutes.")
    invite = _invite_or_410(db, token)
    onboarding = db.get(models.Onboarding, invite.onboarding_id)
    worker = db.get(models.Worker, onboarding.worker_id)
    spec = docs.step(key)
    if spec is None:
        raise HTTPException(status_code=404, detail="Unknown onboarding step.")
    if spec.who != "hire":
        raise HTTPException(status_code=409, detail="This step is handled by the office.")
    item = db.execute(
        select(models.OnboardingItem).where(
            models.OnboardingItem.onboarding_id == onboarding.id, models.OnboardingItem.key == key
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="This step is not part of your checklist.")
    if item.status == "not_applicable":
        raise HTTPException(status_code=409, detail="The office marked this step as not applicable for you.")

    clean = validate_fields(spec, body.data or {})

    signer_name = signed_at = reference = provider_name = image = None
    if spec.signature:
        if body.signature is None:
            raise HTTPException(
                status_code=422, detail="Please sign the document (draw your signature and type your name)."
            )
        expected = f"{worker.first_name} {worker.last_name}".lower()
        typed = body.signature.signer_name.lower()
        if not (set(expected.split()) & set(typed.split())):
            raise HTTPException(status_code=422, detail="Please type your own full name as it appears on your record.")
        try:
            result, signed_at = esign.sign(body.signature.signer_name, body.signature.image_png, key, spec.version)
        except esign.NotConfigured as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except esign.ESignError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        signer_name, reference, provider_name, image = (
            body.signature.signer_name,
            result.reference,
            result.provider,
            body.signature.image_png,
        )

    sub = db.execute(
        select(models.OnboardingSubmission).where(
            models.OnboardingSubmission.onboarding_id == onboarding.id, models.OnboardingSubmission.key == key
        )
    ).scalar_one_or_none()
    if sub is None:
        sub = models.OnboardingSubmission(onboarding_id=onboarding.id, key=key)
        db.add(sub)
    sub.data = json.dumps(clean)
    sub.document_version = spec.version if spec.document else None
    sub.signer_name, sub.signed_at, sub.esign_reference, sub.esign_provider, sub.signature_png = (
        signer_name,
        signed_at,
        reference,
        provider_name,
        image,
    )
    sub.submitted_at = _now()

    if key == "reference_check":
        item.note = spec.office_note  # the office still has to contact them
    else:
        item.status = "done"
        item.note = "Completed online by the new hire" + (f" · e-signed ({reference})" if reference else "")
        item.completed_at, item.completed_by = _now(), None
    if key == "emergency_contact":  # keep the roster record in step
        worker.emergency_contact_name = clean.get("name")
        worker.emergency_contact_phone = clean.get("phone")
    db.flush()
    hiring.refresh_completion(db, onboarding)
    db.commit()
    log.info("portal: %s submitted %s%s", worker.staff_number, key, " (signed)" if reference else "")
    return state(db, invite)


# ---------------------------------------------------------------------------- administrators


@router.post("/onboarding/{onboarding_id}/invite", response_model=schemas.OnboardingOut)
def send_invite(
    onboarding_id: int,
    body: schemas.InviteRequest,
    request: Request,
    p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    """Send the onboarding link (again) — the "Send reminder" button. The link is also logged."""
    o = hiring._get_onboarding(db, onboarding_id)
    if o.completed_at is not None:
        raise HTTPException(status_code=409, detail="This onboarding is already complete.")
    worker = db.get(models.Worker, o.worker_id)
    invites.create_and_send(db, o, worker, kind=body.kind, actor=p.account, request=request, channels=body.channels)
    db.commit()
    return hiring.onboarding_full(db, o)


@router.get("/onboarding/{onboarding_id}/invites", response_model=list[schemas.InviteOut])
def list_invites(onboarding_id: int, _p: auth.Principal = Depends(auth.admin_principal), db: Session = Depends(get_db)):
    """The log of links sent for this onboarding — newest first, links included."""
    o = hiring._get_onboarding(db, onboarding_id)
    return [invites.out(db, i) for i in invites.for_onboarding(db, o.id)]


@router.post("/onboarding/{onboarding_id}/invites/{invite_id}/revoke", response_model=list[schemas.InviteOut])
def revoke_invite(
    onboarding_id: int,
    invite_id: int,
    _p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    o = hiring._get_onboarding(db, onboarding_id)
    invite = db.get(models.OnboardingInvite, invite_id)
    if invite is None or invite.onboarding_id != o.id:
        raise HTTPException(status_code=404, detail="Invite not found.")
    invite.revoked_at = invite.revoked_at or _now()
    db.commit()
    return [invites.out(db, i) for i in invites.for_onboarding(db, o.id)]


@router.get("/onboarding/{onboarding_id}/approval/signature", include_in_schema=False)
def approval_signature_image(
    onboarding_id: int, _p: auth.Principal = Depends(auth.admin_principal), db: Session = Depends(get_db)
):
    o = hiring._get_onboarding(db, onboarding_id)
    if not o.approval_signature_png:
        raise HTTPException(status_code=404, detail="Not approved yet.")
    return Response(
        esign.validate_image(o.approval_signature_png),
        media_type="image/png",
        headers={"Cache-Control": "private, no-store"},
    )


@router.get("/onboarding/{onboarding_id}/submissions/{key}/signature", include_in_schema=False)
def signature_image(
    onboarding_id: int, key: str, _p: auth.Principal = Depends(auth.admin_principal), db: Session = Depends(get_db)
):
    """The drawn signature of a signed document, as PNG (administrators)."""
    o = hiring._get_onboarding(db, onboarding_id)
    sub = db.execute(
        select(models.OnboardingSubmission).where(
            models.OnboardingSubmission.onboarding_id == o.id, models.OnboardingSubmission.key == key
        )
    ).scalar_one_or_none()
    if sub is None or not sub.signature_png:
        raise HTTPException(status_code=404, detail="No signature for this step.")
    return Response(
        esign.validate_image(sub.signature_png), media_type="image/png", headers={"Cache-Control": "private, no-store"}
    )
