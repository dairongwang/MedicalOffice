"""Onboarding invite links — time-limited tokens a new hire uses to open the onboarding portal.

    link = <staff dashboard origin>/#/welcome/<token>          (portal.py serves the token endpoints)

``create_and_send`` mints a token (its SHA-256 is what we look up; the link itself is kept in the
invite row as the administrator's manual fallback), sends it by every requested channel the
server can deliver on — e-mail and/or text message, through messaging.py, i.e. the built-in
fakes while no SMTP/Twilio is configured (read them at /api/v1/messages/fake/outbox or with
``python dev.py outbox``) — and writes one ``OnboardingInvite`` row. Triggered by:

    hire                Applications → "Hire & start onboarding"
    start               Roster → "Start onboarding"
    remind              Onboarding → "Send reminder"
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

import config
import messaging
import models
import schemas
import twofactor

log = logging.getLogger("account.invites")

KINDS = ("hire", "start", "remind", "manual")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token() -> str:
    """Random, URL-safe. Outside production the token is visibly a DUMMY one (``dummy-…``): these
    links are produced by the built-in fake delivery and must never be mistaken for real ones."""
    raw = secrets.token_urlsafe(24)
    return raw if config.IS_PROD else f"dummy-{raw}"


def is_fake_delivery() -> bool:
    return bool(config.MESSAGING_FAKE_PROVIDERS) and not config.IS_PROD


def where_to_find(channels: list[str], fake: bool, link_logged: bool = True) -> str:
    """Plain-language hint shown to the manager after a link was generated."""
    parts = []
    if fake:
        parts.append(
            "Built-in fake delivery (no e-mail/SMS provider configured): the message with the link was printed in "
            "the API console (the terminal running the API / `python dev.py run`) and is in the test inbox at "
            f"{messaging.OUTBOX_PATH} (`python dev.py outbox`)."
        )
    elif channels:
        parts.append(f"Sent to the new hire by {' and '.join(channels)}.")
    else:
        parts.append("Could not be delivered — hand the link over manually.")
    if link_logged:
        parts.append("The link is also logged below (Copy) so you can pass it on by hand.")
    return " ".join(parts)


def portal_base(request: Request | None) -> str:
    """Where the staff dashboard lives:
    - STAFF_URL absolute (its own origin)                         → as configured
    - STAFF_URL a path such as /staff/ (served by this API)       → the API's origin + that path
    - STAFF_URL empty                                             → the calling dashboard's Origin header,
                                                                    else this API + /staff/"""
    url = config.STAFF_URL
    if url.startswith("http://") or url.startswith("https://"):
        return url.rstrip("/") + "/"
    origin = (request.headers.get("origin") or "").strip() if request else ""
    api_origin = str(request.base_url).rstrip("/") if request else ""
    if url.startswith("/"):
        return (origin or api_origin).rstrip("/") + url.rstrip("/") + "/"
    if origin and origin != "null":
        return origin.rstrip("/") + "/"
    return api_origin + "/staff/"


def build_link(request: Request | None, token: str) -> str:
    return f"{portal_base(request)}#/welcome/{token}"


def _message(worker: models.Worker, link: str, expires_at: datetime, kind: str) -> tuple[str, str]:
    when = expires_at.strftime("%b %d, %Y %H:%M UTC")
    if kind == "remind":
        subject = "Reminder: your Medic Office onboarding paperwork"
        lead = f"Hi {worker.first_name}, a friendly reminder to finish your onboarding paperwork."
    else:
        subject = "Welcome to Medic Office — complete your onboarding online"
        lead = f"Welcome aboard, {worker.first_name}! Please complete your onboarding paperwork online."
    body = (
        f"{lead}\n\n"
        f"Open this link (valid until {when}): {link}\n\n"
        f"You can sign the offer letter, handbook, HIPAA and OSHA acknowledgements electronically, and fill in the "
        f"I-9 (Section 1), W-4, payroll, references and emergency-contact forms. Your staff number is "
        f"{worker.staff_number}. Questions? Reply to the practice manager."
    )
    return subject, body


def deliver(
    worker: models.Worker, link: str, expires_at: datetime, kind: str, channels: list[str]
) -> tuple[list[str], str, str]:
    """Send on every requested channel that is available. Returns (channels that went out,
    masked destinations, note about failures/fakes)."""
    subject, body = _message(worker, link, expires_at, kind)
    sent: list[str] = []
    to: list[str] = []
    notes: list[str] = []
    for name in channels:
        ch = messaging.channel(name)
        destination = worker.email if name == messaging.EMAIL else worker.phone
        if not destination:
            notes.append(f"{ch.label}: no {'address' if name == messaging.EMAIL else 'number'} on file")
            continue
        if not ch.available:
            notes.append(f"{ch.label}: not set up on this server")
            continue
        try:
            sms_body = f"{body.split(chr(10))[0]} {link} (valid until {expires_at.strftime('%b %d')})"
            ch.send(destination, sms_body if name == messaging.SMS else body, subject=subject)
        except messaging.MessagingError as exc:
            notes.append(f"{ch.label}: {exc}")
            continue
        sent.append(name)
        masked = twofactor.mask_email(destination) if name == messaging.EMAIL else twofactor.mask_phone(destination)
        to.append(f"{name}:{masked}")
        if ch.fake:
            notes.append(f"{ch.label}: built-in fake — read it at {messaging.OUTBOX_PATH}")
    return sent, ", ".join(to), "; ".join(notes)


def default_channels(worker: models.Worker) -> list[str]:
    out = []
    if worker.email and messaging.EMAIL_CHANNEL.available:
        out.append(messaging.EMAIL)
    if worker.phone and messaging.SMS_CHANNEL.available:
        out.append(messaging.SMS)
    return out or [messaging.EMAIL]


def create_and_send(
    db: Session,
    onboarding: models.Onboarding,
    worker: models.Worker,
    *,
    kind: str,
    actor: models.Account | None,
    request: Request | None,
    channels: list[str] | None = None,
) -> models.OnboardingInvite:
    """Mint a link, send it, log it. Earlier links stay valid until they expire (a reminder must
    not break a link the person already opened); ``revoke`` them explicitly if needed."""
    token = new_token()
    link = build_link(request, token)
    expires_at = _now() + timedelta(hours=config.ONBOARDING_INVITE_TTL_HOURS)
    sent, to, note = deliver(worker, link, expires_at, kind, channels or default_channels(worker))
    if is_fake_delivery():  # make the link easy to grab from the console while the fakes are in use
        print(
            f"[onboarding link · fake delivery] {kind} · {worker.staff_number} {worker.first_name} "
            f"{worker.last_name} · valid until {expires_at.strftime('%Y-%m-%d %H:%M UTC')}\n    {link}",
            flush=True,
        )
    invite = models.OnboardingInvite(
        onboarding_id=onboarding.id,
        token_hash=hash_token(token),
        link=link,
        kind=kind if kind in KINDS else "manual",
        channels=",".join(sent),
        sent_to=to,
        delivery_note=note or None,
        created_by=actor.id if actor else None,
        expires_at=expires_at,
    )
    db.add(invite)
    db.flush()
    log.info("onboarding invite %s for %s: sent via %s (%s)", invite.kind, worker.staff_number, sent or "nothing", to)
    return invite


def find_valid(db: Session, token: str) -> models.OnboardingInvite | None:
    invite = db.execute(
        select(models.OnboardingInvite).where(models.OnboardingInvite.token_hash == hash_token(token))
    ).scalar_one_or_none()
    if invite is None or invite.revoked_at or invite.expires_at < _now():
        return None
    return invite


def mark_opened(invite: models.OnboardingInvite) -> None:
    now = _now()
    invite.first_opened_at = invite.first_opened_at or now
    invite.last_opened_at = now
    invite.opens += 1


def for_onboarding(db: Session, onboarding_id: int) -> list[models.OnboardingInvite]:
    return (
        db.execute(
            select(models.OnboardingInvite)
            .where(models.OnboardingInvite.onboarding_id == onboarding_id)
            .order_by(models.OnboardingInvite.created_at.desc())
        )
        .scalars()
        .all()
    )


def out(db: Session, i: models.OnboardingInvite) -> schemas.InviteOut:
    actor = db.get(models.Account, i.created_by) if i.created_by else None
    channels = [c for c in i.channels.split(",") if c]
    fake = is_fake_delivery() and "fake" in (i.delivery_note or "")
    return schemas.InviteOut(
        id=i.id,
        kind=i.kind,
        link=i.link,
        where_to_find=where_to_find(channels, fake),
        fake_delivery=fake,
        channels=channels,
        sent_to=i.sent_to,
        delivery_note=i.delivery_note,
        created_at=i.created_at,
        created_by=actor.email if actor else None,
        expires_at=i.expires_at,
        expired=i.expires_at < _now(),
        revoked_at=i.revoked_at,
        first_opened_at=i.first_opened_at,
        last_opened_at=i.last_opened_at,
        opens=i.opens,
        valid=i.revoked_at is None and i.expires_at >= _now(),
    )
