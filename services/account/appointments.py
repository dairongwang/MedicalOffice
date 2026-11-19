"""Appointments — registered patients book a time slot with a provider (appointments.py).

Public (the patients page of the main app; anonymous, rate-limited per caller address):

    GET  /api/v1/appointments/providers                 staff patients can book with + the weekdays they are in
    GET  /api/v1/appointments/slots?provider_id=&date=  the day's slots (free / taken) for one provider
    POST /api/v1/appointments/lookup                    patient identity → record summary + upcoming appointments
    POST /api/v1/appointments/coverage                  is the insurance good for the day of the visit? (else the
                                                        booking needs the patient's self-pay agreement)
    POST /api/v1/appointments                           book a slot (201) — the patient must be registered;
                                                        the appointment link goes out by e-mail / text
    POST /api/v1/appointments/{id}/cancel               the patient cancels their own appointment
    POST /api/v1/appointments/{id}/check-in             the patient starts the check-in (identity again) — from
                                                        CHECK_IN_WINDOW_HOURS (24) before the visit until it ends;
                                                        answers the check-in page to open (check_ins.py)
    GET  /api/v1/appointments/by-token/{token}          the link's page: the appointment (+ calendar links)
    GET  /api/v1/appointments/by-token/{token}/calendar.ics   the .ics file ("add to calendar")
    POST /api/v1/appointments/by-token/{token}/confirm  "I am coming" (the reminder's confirm link)
    POST /api/v1/appointments/by-token/{token}/cancel   cancel from the link
    POST /api/v1/appointments/by-token/{token}/check-in start the check-in from the link (reminder: ?action=check-in)

A signed-in patient (role = patient — the only role the patients' app signs in):

    GET  /api/v1/me/patients                  the records that belong to the account (matched by e-mail
                                              automatically, or attached by name + date of birth)
    POST /api/v1/me/patients/link             attach a record (patient identity, like the intake page)
    GET  /api/v1/me/appointments              every appointment on those records, upcoming and past
    POST /api/v1/me/appointments/{id}/confirm confirm one of them
    POST /api/v1/me/appointments/{id}/cancel  cancel one of them
    POST /api/v1/me/appointments/{id}/check-in   start the check-in (within the check-in window)
    Booking while signed in attaches the record to the account as well.

Administrators (the office administration dashboard, apps/admin):

    GET   /api/v1/appointments?date=&from=&to=&provider_id=&status=&checked_in=&q=    the agenda
    GET   /api/v1/appointments/{id}
    PATCH /api/v1/appointments/{id}                     status (completed / no_show / canceled), notes, confirmed,
                                                        checked_in: true = send the patient the check-in link
                                                        (any time while scheduled; again = a new link) ·
                                                        false = call a pending check-in off / undo it (audited)
    GET   /api/v1/appointments/{id}/check-ins           every check-in: answers, signatures, estimate, failures
    POST  /api/v1/appointments/{id}/send-link           send the patient's link again
    POST  /api/v1/appointments/reminders/run[?as_of=]   run the reminder job now (reminders.py; as_of outside prod)
    GET/PUT/DELETE /api/v1/appointments/{id}/assistant(s)  the medical assistant preparing the visit
                                                        (assignments.py; the agenda filters on ``assistant=``)

The patient's link (``send_link``): every booking mints a random token (outside production a
visibly ``dummy-…`` one); its SHA-256 is stored, the link ``<web app>/appointments/<token>`` is
kept on the row for the office, and a message with the link goes to the patient's e-mail and/or
phone on record through messaging.py — the real SMTP / Twilio when configured, otherwise the
built-in fake whose messages land in the test inbox (/api/v1/messages/fake/outbox,
``python dev.py outbox``). Opening the link shows the appointment, offers "add to calendar"
(.ics download, Google Calendar, Outlook.com) and cancellation; opens are counted so the office
can see whether the patient looked at it.

Check-in (check_ins.py): the patient starts it from the patients' app — their account, the
appointment link (the reminder links straight to it with ``?action=check-in``) or the lookup page
— from ``CHECK_IN_WINDOW_HOURS`` (24) before the visit until it ends; earlier the app says when it
opens. Starting opens the **check-in page**: a questionnaire the patient signs, the estimated cost
not covered by insurance the patient signs to accept, then the estimate is pre-authorized on the
payment method on file — only then is the visit checked in. The office starts a check-in from the
administration dashboard at any time: the page's link goes to the patient's e-mail / phone and
must be completed within CHECK_IN_LINK_MINUTES (60). A check-in that cannot be completed fails
with the reason on both boards (``check_in`` on every appointment). The agenda filters on
``checked_in``; office actions are audited; a completed check-in counts as the confirmation.

How a slot is decided:

    * A **provider** is an active ``Worker`` whose role is in ``models.APPOINTMENT_PROVIDER_ROLES``
      (doctor, nurse, physician assistant). Receptionists and administrators have no agenda.
    * The provider's **weekly availability** (workers.py: the standard week Mon–Fri 9–5 unless the
      worker changed it) is cut into slots of ``APPOINTMENT_SLOT_MINUTES`` in the office's time zone
      (``OFFICE_TIMEZONE``). A slot is bookable when it starts in the future, lies within
      ``APPOINTMENT_MAX_DAYS_AHEAD`` days, and no *scheduled* appointment of that provider starts
      at the same time (partial unique index ``uq_appointment_slot`` — two patients racing for the
      same slot cannot both win).
    * The **patient** proves who they are exactly like on the intake page (patients.find_match:
      name + date of birth, or e-mail/phone + date of birth). Unknown patients are sent to
      register first; nothing is created on their behalf here.

What the public endpoints reveal: providers' names and roles (the office publishes them
anyway), whether a slot is free, and — after a successful identity match — the patient's own
upcoming appointments. Reading the agenda needs an administrator account.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import accounts
import assignments
import auth
import check_ins
import config
import messaging
import models
import patient_payments
import patients
import schemas
import twofactor
import visit_summaries
import workers
from database import get_db
from ratelimit import RateLimiter

log = logging.getLogger("account.appointments")

router = APIRouter(prefix="/appointments", tags=["appointments"])

SLOTS_LIMITER = RateLimiter(limit=120, window_seconds=600)
LOOKUP_LIMITER = RateLimiter(limit=30, window_seconds=600)
BOOK_LIMITER = RateLimiter(limit=20, window_seconds=600)
TOKEN_LIMITER = RateLimiter(limit=60, window_seconds=600)

# A patient may hold at most this many upcoming (scheduled) appointments at a time.
MAX_UPCOMING_PER_PATIENT = 5

CANCELED_BY_PATIENT = "patient"
CANCELED_BY_STAFF = "staff"


# ---------------------------------------------------------------------------- time helpers


def office_tz() -> ZoneInfo:
    try:
        return ZoneInfo(config.OFFICE_TIMEZONE)
    except Exception:  # noqa: BLE001 - a typo in OFFICE_TIMEZONE must not take the API down
        log.warning("OFFICE_TIMEZONE=%r is not a known time zone; using UTC", config.OFFICE_TIMEZONE)
        return ZoneInfo("UTC")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    """Naive datetimes are taken as UTC (what the database hands back); aware ones are converted."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def slot_length() -> timedelta:
    return timedelta(minutes=max(5, config.APPOINTMENT_SLOT_MINUTES))


def format_local(value: datetime) -> str:
    """``Monday, June 7, 2027 · 09:30`` in the office's time zone (portable: no ``%-d``)."""
    local = as_utc(value).astimezone(office_tz())
    return f"{local:%A}, {local:%B} {local.day}, {local.year} · {local:%H:%M}"


# ---------------------------------------------------------------------------- providers


def is_provider(w: models.Worker) -> bool:
    return w.active and w.role in models.APPOINTMENT_PROVIDER_ROLES


def provider_out(db: Session, w: models.Worker) -> schemas.ProviderOut:
    week = workers.availability_of(db, w)
    return schemas.ProviderOut(
        id=w.id,
        staff_number=w.staff_number,
        first_name=w.first_name,
        last_name=w.last_name,
        role=w.role,
        specialty=w.specialty,
        weekdays=[d.weekday for d in week if d.available and d.end_time > d.start_time],
    )


def list_providers(db: Session) -> list[models.Worker]:
    return (
        db.execute(
            select(models.Worker)
            .where(models.Worker.active.is_(True), models.Worker.role.in_(models.APPOINTMENT_PROVIDER_ROLES))
            .order_by(models.Worker.last_name_norm, models.Worker.first_name_norm)
        )
        .scalars()
        .all()
    )


def _provider(db: Session, provider_id: int) -> models.Worker:
    worker = db.get(models.Worker, provider_id)
    if worker is None or not is_provider(worker):
        raise HTTPException(status_code=404, detail="This provider does not take appointments.")
    return worker


# ---------------------------------------------------------------------------- slots


def booked_starts(db: Session, provider: models.Worker, day_start: datetime, day_end: datetime) -> set[datetime]:
    rows = db.execute(
        select(models.Appointment.start_at).where(
            models.Appointment.provider_id == provider.id,
            models.Appointment.status == "scheduled",
            models.Appointment.start_at >= day_start,
            models.Appointment.start_at < day_end,
        )
    ).scalars()
    return {as_utc(s) for s in rows}


def day_slots(db: Session, provider: models.Worker, day: date, now: datetime | None = None) -> schemas.DaySlots:
    """Every slot of ``day`` for this provider — free or taken — in the office's time zone."""
    now = now or _now()
    tz = office_tz()
    week = workers.availability_of(db, provider)
    availability = week[day.weekday()]
    out = schemas.DaySlots(provider_id=provider.id, date=day, timezone=str(tz), slots=[])
    today_local = now.astimezone(tz).date()
    if day < today_local:
        out.note = "This date is in the past."
        return out
    if day > today_local + timedelta(days=config.APPOINTMENT_MAX_DAYS_AHEAD):
        out.note = f"Appointments can be booked up to {config.APPOINTMENT_MAX_DAYS_AHEAD} days ahead."
        return out
    if not availability.available or availability.end_time <= availability.start_time:
        out.note = f"{provider.first_name} {provider.last_name} is not in on {schemas.WEEKDAY_NAMES[day.weekday()]}s."
        return out
    start = datetime.combine(day, availability.start_time, tzinfo=tz)
    end = datetime.combine(day, availability.end_time, tzinfo=tz)
    step = slot_length()
    taken = booked_starts(db, provider, start.astimezone(timezone.utc), end.astimezone(timezone.utc))
    t = start
    while t + step <= end:
        utc = t.astimezone(timezone.utc)
        out.slots.append(
            schemas.SlotOut(
                start_at=utc,
                end_at=utc + step,
                label=f"{t:%H:%M}",
                available=utc not in taken and utc > now,
            )
        )
        t += step
    if out.slots and not any(s.available for s in out.slots):
        out.note = "Every slot on this day is taken or already past."
    return out


# ---------------------------------------------------------------------------- output


def appointment_out(
    db: Session,
    a: models.Appointment,
    *,
    with_link: bool = True,
    token: str | None = None,
    staff: bool = False,
    office: bool = False,
) -> schemas.AppointmentOut:
    """``with_link``: include the patient's link and its delivery record (the patient after proving
    who they are, the office); ``token``: the page behind the link also gets the calendar links.
    ``staff`` / ``office``: the medical assistant on the visit (assignments.py) — never shown to the
    patient; ``office`` also adds the staff-only notes."""
    provider = db.get(models.Worker, a.provider_id)
    patient = db.get(models.Patient, a.patient_id)
    return schemas.AppointmentOut(
        id=a.id,
        number=a.number,
        status=a.status,
        start_at=as_utc(a.start_at),
        end_at=as_utc(a.end_at),
        start_local=format_local(a.start_at),
        timezone=str(office_tz()),
        reason=a.reason,
        provider=provider_out(db, provider),
        patient=patients.summary(patient),
        created_at=a.created_at,
        canceled_at=a.canceled_at,
        office_name=config.OFFICE_NAME,
        office_address=config.OFFICE_ADDRESS or None,
        link=link_out(a) if with_link else None,
        calendar=calendar_links(db, a, token) if token else None,
        confirmed_at=a.confirmed_at,
        confirmed_via=a.confirmed_via,
        reminder_sent_at=a.reminder_sent_at,
        reminder_channels=[c for c in (a.reminder_channels or "").split(",") if c],
        reminder_note=a.reminder_note,
        payment=a.payment or "insurance",
        coverage_status=a.coverage_status,
        self_pay_agreed_at=a.self_pay_agreed_at,
        charge=patient_payments.payment_out(db, charge) if (charge := patient_payments.charge_for(db, a)) else None,
        checked_in_at=a.checked_in_at,
        checked_in_via=a.checked_in_via,
        check_in_opens_at=check_in_window(a)[0],
        check_in_closes_at=check_in_window(a)[1],
        can_check_in=can_check_in(a),
        check_in=check_ins.latest_summary(db, a),
        assistant=assignments.summary_for(db, a) if (staff or office) else None,
        notes=a.notes if office else None,
        provider_seen_at=_seen_at(db, a) if (staff or office) else None,
        visit_summary=visit_summaries.brief_for(db, a, patient=not (staff or office)),
    )


def _seen_at(db: Session, a: models.Appointment) -> datetime | None:
    note = db.execute(select(models.VisitNote).where(models.VisitNote.appointment_id == a.id)).scalar_one_or_none()
    return note.seen_at if note is not None else None


def confirm(a: models.Appointment, via: str) -> None:
    """The patient says they are coming. Only a scheduled visit can be confirmed; confirming twice is fine."""
    if a.status != "scheduled":
        raise HTTPException(status_code=409, detail=f"This appointment is {a.status.replace('_', ' ')}.")
    if a.confirmed_at is None:
        a.confirmed_at = _now()
        a.confirmed_via = via


# ---------------------------------------------------------------------------- check-in

CHECKED_IN_BY_STAFF = "staff"


def check_in_window(a: models.Appointment) -> tuple[datetime, datetime]:
    """(opens, closes): the patient may check in from the app from CHECK_IN_WINDOW_HOURS before the
    visit starts until it ends."""
    return as_utc(a.start_at) - timedelta(hours=config.CHECK_IN_WINDOW_HOURS), as_utc(a.end_at)


def can_check_in(a: models.Appointment, now: datetime | None = None) -> bool:
    """Right now, may the *patient* start a check-in from the app? (scheduled, not checked in, in
    the window). Checking in itself is the signed check-in page — check_ins.py."""
    if a.status != "scheduled" or a.checked_in_at is not None:
        return False
    opens, closes = check_in_window(a)
    return opens <= (now or _now()) < closes


def _hours(value: float) -> str:
    return f"{value:g} hour{'' if value == 1 else 's'}"


def _start_check_in(db: Session, a: models.Appointment, via: str, request: Request) -> schemas.CheckInStart:
    c, token = check_ins.start(db, a, via, request)
    db.commit()
    log.info("check-in page opened for %s (%s)", a.number, via)
    return schemas.CheckInStart(token=token, url=c.link, check_in=check_ins.summary(db, c))


def undo_check_in(a: models.Appointment) -> None:
    """Clear the appointment's check-in (check_ins.undo also releases its pre-authorization)."""
    a.checked_in_at = None
    a.checked_in_via = None
    a.checked_in_by = None


# ---------------------------------------------------------------------------- the patient's link


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token() -> str:
    """Random, URL-safe. Outside production visibly a DUMMY token (``dummy-…``), like the
    onboarding invites: produced by the fake delivery, never to be mistaken for a real one."""
    raw = secrets.token_urlsafe(24)
    return raw if config.IS_PROD else f"dummy-{raw}"


def app_base(request: Request | None) -> str:
    """Where the patients' web app lives, for the links in messages: WEB_APP_URL (configured —
    preferred, so a forged Host header can never point a patient elsewhere), else the Angular dev
    server (DEV_WEB_URL), else this API's own origin (it serves the app when WEB_DIST is set)."""
    for url in (config.WEB_APP_URL, config.DEV_WEB_URL):
        if url:
            return url.rstrip("/") + "/"
    if request is not None:
        return str(request.base_url).rstrip("/") + "/"
    return f"http://localhost:{os.environ.get('PORT', '8000')}/"  # no request (the seed): a usable guess


def build_link(request: Request | None, token: str) -> str:
    return f"{app_base(request)}appointments/{token}"


def is_fake_delivery() -> bool:
    return bool(config.MESSAGING_FAKE_PROVIDERS) and not config.IS_PROD


def link_out(a: models.Appointment) -> schemas.AppointmentLink | None:
    if not a.link:
        return None
    return schemas.AppointmentLink(
        url=a.link,
        channels=[c for c in (a.link_channels or "").split(",") if c],
        sent_to=[t for t in (a.link_sent_to or "").split(", ") if t],
        sent_at=a.link_sent_at,
        note=a.link_delivery_note,
        fake=is_fake_delivery(),
        outbox_url=messaging.outbox_url() if is_fake_delivery() else None,
        opens=a.link_opens or 0,
        last_opened_at=a.link_last_opened_at,
    )


def _provider_name(w: models.Worker) -> str:
    return f"{'Dr. ' if w.role == 'doctor' else ''}{w.first_name} {w.last_name}"


def _message(db: Session, a: models.Appointment, link: str, kind: str) -> tuple[str, str, str]:
    """(subject, e-mail body, text-message body)."""
    provider = db.get(models.Worker, a.provider_id)
    patient = db.get(models.Patient, a.patient_id)
    when = format_local(a.start_at)
    tz = str(office_tz())
    who = _provider_name(provider)
    lead = (
        f"Hi {patient.first_name}, here is your appointment link again."
        if kind == "resend"
        else f"Hi {patient.first_name}, your appointment is booked."
    )
    subject = f"Your {config.OFFICE_NAME} appointment {a.number} — {when}"
    role = workers_role_label(provider.role) + (f" · {provider.specialty}" if provider.specialty else "")
    lines = [
        lead,
        "",
        f"  With:    {who} ({role})",
        f"  When:    {when} ({tz})",
        f"  Where:   {config.OFFICE_NAME}{' · ' + config.OFFICE_ADDRESS if config.OFFICE_ADDRESS else ''}",
        f"  Number:  {a.number}",
    ]
    if a.reason:
        lines.append(f"  Reason:  {a.reason}")
    lines += [
        "",
        "Open your appointment page to review it, add it to your phone or web calendar, or cancel it:",
        f"  {link}",
        "",
        f"Check in from that page (or your patient account) from {_hours(config.CHECK_IN_WINDOW_HOURS)} before "
        "your visit — or at the front desk when you arrive.",
        "",
        "Please arrive a few minutes early and mention your number at the front desk."
        + (f" Questions? Call {config.OFFICE_PHONE}." if config.OFFICE_PHONE else ""),
    ]
    local = as_utc(a.start_at).astimezone(office_tz())
    sms = (
        f"{config.OFFICE_NAME}: {'your appointment link again — ' if kind == 'resend' else 'your appointment '}"
        f"{a.number} with {who} is on {local:%a}, {local:%b} {local.day} at {local:%H:%M}. "
        f"Review, add to calendar or cancel: {link}"
    )
    return subject, "\n".join(lines), sms


def workers_role_label(role: str) -> str:
    return {
        "doctor": "Doctor",
        "nurse": "Nurse",
        "physician_assistant": "Physician assistant",
        "medical_assistant": "Medical assistant",
        "technician": "Technician",
        "receptionist": "Receptionist",
        "administrator": "Administrator",
    }.get(role, role)


def deliver(
    email: str | None, phone: str | None, subject: str, body: str, sms: str, *, whose: str
) -> tuple[list[str], list[str], list[str]]:
    """Send a message by e-mail and text to the given address / number, on every channel the
    server can deliver on. ``whose`` names the record in notes ("the patient record", "the staff
    record"). Returns (channels sent, masked destinations "email:p•••@…", notes). Never raises —
    a provider problem becomes a note."""
    sent: list[str] = []
    to: list[str] = []
    notes: list[str] = []
    # Text messages go to the number in E.164 form ("+15550100401"), whatever spacing was typed.
    digits = patients.normalize_phone(phone)
    for name, destination in ((messaging.EMAIL, email), (messaging.SMS, f"+{digits}" if digits else None)):
        ch = messaging.channel(name)
        if not destination:
            notes.append(f"{ch.label}: no {'address' if name == messaging.EMAIL else 'number'} on {whose}")
            continue
        if not ch.available:
            notes.append(f"{ch.label}: not set up on this server")
            continue
        try:
            ch.send(destination, sms if name == messaging.SMS else body, subject=subject)
        except messaging.MessagingError as exc:
            notes.append(f"{ch.label}: {exc}")
            continue
        sent.append(name)
        masked = twofactor.mask_email(destination) if name == messaging.EMAIL else twofactor.mask_phone(destination)
        to.append(f"{name}:{masked}")
        if ch.fake:
            notes.append(f"{ch.label}: built-in fake — read it at {messaging.OUTBOX_PATH}")
    return sent, to, notes


def deliver_to_patient(
    patient: models.Patient, subject: str, body: str, sms: str
) -> tuple[list[str], list[str], list[str]]:
    """``deliver`` to the e-mail / phone on the patient record."""
    return deliver(patient.email, patient.phone, subject, body, sms, whose="the patient record")


def deliver_document(
    patient: models.Patient, subject: str, body: str, sms: str, attachment: messaging.Attachment
) -> tuple[list[str], list[str], list[str]]:
    """A document for the patient (a signed visit summary, a report): the e-mail carries the PDF, the text
    only says it is ready (no medical details). Like ``deliver``: (channels, masked destinations, notes),
    never raises."""
    sent: list[str] = []
    to: list[str] = []
    notes: list[str] = []
    digits = patients.normalize_phone(patient.phone)
    for name, destination in ((messaging.EMAIL, patient.email), (messaging.SMS, f"+{digits}" if digits else None)):
        ch = messaging.channel(name)
        if not destination:
            notes.append(f"{ch.label}: no {'address' if name == messaging.EMAIL else 'number'} on the patient record")
            continue
        if not ch.available:
            notes.append(f"{ch.label}: not set up on this server")
            continue
        try:
            if name == messaging.EMAIL:
                ch.send(destination, body, subject=subject, attachments=[attachment])
            else:
                ch.send(destination, sms)
        except messaging.MessagingError as exc:
            notes.append(f"{ch.label}: {exc}")
            continue
        sent.append(name)
        masked = twofactor.mask_email(destination) if name == messaging.EMAIL else twofactor.mask_phone(destination)
        to.append(f"{name}:{masked}")
        if ch.fake:
            notes.append(f"{ch.label}: built-in fake — read it at {messaging.OUTBOX_PATH}")
    return sent, to, notes


def send_link(db: Session, a: models.Appointment, request: Request | None, kind: str = "booked") -> None:
    """Mint the token on the first call (later calls re-use the same link), send the message on
    every channel the patient has a destination for and the server can deliver on, and record
    the outcome on the row. Never raises: a provider problem is noted, the booking stands."""
    if not a.token_hash:
        token = new_token()
        a.token_hash = hash_token(token)
        a.link = build_link(request, token)
    patient = db.get(models.Patient, a.patient_id)
    subject, body, sms = _message(db, a, a.link, kind)
    sent, to, notes = deliver_to_patient(patient, subject, body, sms)
    a.link_channels = ",".join(sent)
    a.link_sent_to = ", ".join(to)
    a.link_delivery_note = ("; ".join(notes))[:500] or None
    a.link_sent_at = _now() if sent else a.link_sent_at
    log.info("appointment %s: link %s by %s", a.number, kind, ",".join(sent) or "nobody (not delivered)")


def _by_token(db: Session, token: str) -> models.Appointment:
    if not token or len(token) > 128:
        raise HTTPException(status_code=404, detail="This appointment link is not valid.")
    appointment = db.execute(
        select(models.Appointment).where(models.Appointment.token_hash == hash_token(token))
    ).scalar_one_or_none()
    if appointment is None:
        raise HTTPException(status_code=404, detail="This appointment link is not valid.")
    return appointment


# ---------------------------------------------------------------------------- calendar


def _ics_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _ics_stamp(value: datetime) -> str:
    return f"{as_utc(value):%Y%m%dT%H%M%SZ}"


def ics(db: Session, a: models.Appointment) -> str:
    """One VEVENT (RFC 5545) in UTC — calendars convert to the user's zone. A canceled
    appointment is exported with STATUS:CANCELLED so a re-import removes it."""
    provider = db.get(models.Worker, a.provider_id)
    summary = f"{config.OFFICE_NAME}: appointment with {_provider_name(provider)}"
    description = f"Appointment {a.number}" + (f" — {a.reason}" if a.reason else "")
    if a.link:
        description += f"\nReview or cancel: {a.link}"
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Medic Office management//Appointments//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:appointment-{a.id}@{config.OFFICE_NAME.lower().replace(' ', '-')}",
        f"DTSTAMP:{_ics_stamp(_now())}",
        f"DTSTART:{_ics_stamp(a.start_at)}",
        f"DTEND:{_ics_stamp(a.end_at)}",
        f"SUMMARY:{_ics_escape(summary)}",
        f"DESCRIPTION:{_ics_escape(description)}",
        f"LOCATION:{_ics_escape(config.OFFICE_ADDRESS or config.OFFICE_NAME)}",
        f"STATUS:{'CANCELLED' if a.status == 'canceled' else 'CONFIRMED'}",
        "BEGIN:VALARM",
        "TRIGGER:-PT60M",
        "ACTION:DISPLAY",
        f"DESCRIPTION:{_ics_escape(summary)}",
        "END:VALARM",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(lines) + "\r\n"


def calendar_links(db: Session, a: models.Appointment, token: str) -> schemas.CalendarLinks:
    provider = db.get(models.Worker, a.provider_id)
    title = f"{config.OFFICE_NAME}: appointment with {_provider_name(provider)}"
    details = f"Appointment {a.number}" + (f" — {a.reason}" if a.reason else "") + (f"\n{a.link}" if a.link else "")
    location = config.OFFICE_ADDRESS or config.OFFICE_NAME
    start, end = as_utc(a.start_at), as_utc(a.end_at)
    google = "https://calendar.google.com/calendar/render?" + urlencode(
        {
            "action": "TEMPLATE",
            "text": title,
            "dates": f"{_ics_stamp(start)}/{_ics_stamp(end)}",
            "details": details,
            "location": location,
        }
    )
    outlook = "https://outlook.live.com/calendar/0/deeplink/compose?" + urlencode(
        {
            "path": "/calendar/action/compose",
            "rru": "addevent",
            "subject": title,
            "startdt": start.isoformat().replace("+00:00", "Z"),
            "enddt": end.isoformat().replace("+00:00", "Z"),
            "body": details,
            "location": location,
        }
    )
    return schemas.CalendarLinks(
        ics_url=f"/api/v1/appointments/by-token/{quote(token, safe='')}/calendar.ics",
        google_url=google,
        outlook_url=outlook,
    )


def upcoming_for(db: Session, patient: models.Patient, now: datetime | None = None) -> list[models.Appointment]:
    now = now or _now()
    return (
        db.execute(
            select(models.Appointment)
            .where(
                models.Appointment.patient_id == patient.id,
                models.Appointment.status == "scheduled",
                models.Appointment.end_at >= now,
            )
            .order_by(models.Appointment.start_at)
        )
        .scalars()
        .all()
    )


def _caller(request: Request) -> str | None:
    return request.client.host if request.client else None


def _patient_for(db: Session, identity: schemas.PatientIdentity) -> models.Patient:
    patient, _rules = patients.find_match(db, identity)
    if patient is None:
        raise HTTPException(
            status_code=404,
            detail="We could not find a patient record with these details. Please register first, "
            "or check the spelling of your name and your date of birth.",
        )
    return patient


# ---------------------------------------------------------------------------- coverage at booking

COVERAGE_MESSAGES = {
    "none": "No insurance is on your record, so this visit would be self-pay.",
    "inactive": "Your insurer says this coverage is not active for the day of the visit.",
    "pending": "Your coverage does not start until after the day of the visit.",
    "not_found": "Your insurer has no record of the member id on file.",
    "incomplete": "Your record does not have enough insurance details to check (insurance company and member id).",
    "unavailable": "Your coverage could not be checked right now.",
}


def coverage_for(db: Session, patient: models.Patient, service_date: date) -> schemas.CoverageCheck:
    """Is the patient's coverage good for ``service_date``? Re-uses a check made in the last
    COVERAGE_REUSE_SECONDS (the pre-check from the booking page, typically), otherwise asks the
    insurer (the built-in fake outside production) with the day of the visit as the service date
    and keeps the answer (``triggered_by = booking``)."""
    if not patients.has_insurance(patient):
        return schemas.CoverageCheck(
            covered=False,
            self_pay_required=config.APPOINTMENT_REQUIRE_COVERAGE,
            has_insurance=False,
            status="none",
            message=COVERAGE_MESSAGES["none"],
            service_date=service_date,
        )
    recent = patients.verifications_of(db, patient)
    row = recent[0] if recent else None
    if row is None or (_now() - row.checked_at).total_seconds() > config.COVERAGE_REUSE_SECONDS:
        row = patients.verify_insurance(db, patient, "booking", service_date)
        db.commit()
    out = patients.verification_out(row)
    covered = out.status == "active" and (out.coverage_end is None or out.coverage_end >= service_date)
    if out.status == "active" and not covered:
        message = f"Your coverage ends on {out.coverage_end.isoformat()}, before the day of the visit."
    else:
        message = COVERAGE_MESSAGES.get(out.status, out.message) if not covered else out.message
    return schemas.CoverageCheck(
        covered=covered,
        self_pay_required=not covered and config.APPOINTMENT_REQUIRE_COVERAGE,
        has_insurance=True,
        status=out.status,
        message=message,
        service_date=service_date,
        verification=out,
    )


# ---------------------------------------------------------------------------- public endpoints


@router.get("/providers", response_model=schemas.ProviderList)
def providers(db: Session = Depends(get_db)):
    """Who patients can book with, and on which weekdays; plus the slot length and booking horizon."""
    return schemas.ProviderList(
        providers=[provider_out(db, w) for w in list_providers(db)],
        slot_minutes=config.APPOINTMENT_SLOT_MINUTES,
        max_days_ahead=config.APPOINTMENT_MAX_DAYS_AHEAD,
        timezone=str(office_tz()),
        payment_method_required=config.APPOINTMENT_REQUIRE_PAYMENT_METHOD,
        self_pay_visit_cents=config.SELF_PAY_VISIT_CENTS,
        currency=config.BILLING_CURRENCY,
    )


@router.get("/slots", response_model=schemas.DaySlots)
def slots(
    request: Request,
    provider_id: int = Query(description="A provider id from /appointments/providers"),
    day: date = Query(alias="date", description="The day, YYYY-MM-DD (office time zone)"),
    db: Session = Depends(get_db),
):
    """The day's slots for one provider, each marked free or taken."""
    if not SLOTS_LIMITER.allow(_caller(request)):
        raise HTTPException(status_code=429, detail="Too many requests. Please try again in a few minutes.")
    return day_slots(db, _provider(db, provider_id), day)


@router.post("/lookup", response_model=schemas.AppointmentLookup)
def lookup(body: schemas.PatientIdentity, request: Request, db: Session = Depends(get_db)):
    """Patient identity → the record's summary and the patient's upcoming appointments."""
    if not LOOKUP_LIMITER.allow(_caller(request)):
        raise HTTPException(status_code=429, detail="Too many requests. Please try again in a few minutes.")
    patient = _patient_for(db, body)
    return schemas.AppointmentLookup(
        patient=patients.summary(patient), appointments=[appointment_out(db, a) for a in upcoming_for(db, patient)]
    )


@router.post("/coverage", response_model=schemas.CoverageCheck)
def coverage(body: schemas.CoverageCheckRequest, request: Request, db: Session = Depends(get_db)):
    """Before booking: whether the patient's insurance covers the day of the visit — and so
    whether they will have to agree to self-pay. ``404`` unknown patient."""
    if not LOOKUP_LIMITER.allow(_caller(request)):
        raise HTTPException(status_code=429, detail="Too many requests. Please try again in a few minutes.")
    patient = _patient_for(db, body)
    day = (as_utc(body.start_at) if body.start_at else _now()).astimezone(office_tz()).date()
    return coverage_for(db, patient, day)


@router.post("", response_model=schemas.AppointmentOut, status_code=201)
def book(
    body: schemas.AppointmentRequest,
    request: Request,
    db: Session = Depends(get_db),
    me: auth.Principal | None = Depends(auth.optional_principal),
):
    """Book a slot. ``404`` unknown patient or provider · ``400`` not a bookable time ·
    ``409`` the slot was just taken, the patient already has an appointment then — or the
    coverage is not active for the day and the patient did not agree to self-pay.

    Payment (patient_payments.py, APPOINTMENT_REQUIRE_PAYMENT_METHOD): the patient must be
    signed in (``401`` otherwise) with a validated payment method on the account (``409``);
    the copay / self-pay fee is put on hold on it — a declined hold means no booking (``409``)."""
    if not BOOK_LIMITER.allow(_caller(request)):
        raise HTTPException(status_code=429, detail="Too many requests. Please try again in a few minutes.")
    patient = _patient_for(db, body)
    provider = _provider(db, body.provider_id)
    method = patient_payments.require_booking_method(db, me)
    if method is not None and patient.account_id and patient.account_id != me.account.id:
        raise HTTPException(
            status_code=409,
            detail="This patient record belongs to another account. Please sign in with that account, or ask "
            "the front desk.",
        )
    if me is not None and me.account.role == models.ROLE_PATIENT and patient.account_id is None:
        patient.account_id = me.account.id  # booked while signed in: the record is theirs from now on
    now = _now()
    start = as_utc(body.start_at)
    if start <= now:
        raise HTTPException(status_code=400, detail="That time has already passed. Please pick a later slot.")
    day = start.astimezone(office_tz()).date()
    slots_today = day_slots(db, provider, day, now)
    slot = next((s for s in slots_today.slots if s.start_at == start), None)
    if slot is None:
        raise HTTPException(
            status_code=400,
            detail=slots_today.note
            or "That time is not a bookable slot for this provider. Please pick one of the listed times.",
        )
    if not slot.available:
        raise HTTPException(status_code=409, detail="That slot was just taken. Please choose another time.")
    upcoming = upcoming_for(db, patient, now)
    if any(as_utc(a.start_at) == start for a in upcoming):
        raise HTTPException(status_code=409, detail="You already have an appointment at that time.")
    if len(upcoming) >= MAX_UPCOMING_PER_PATIENT:
        raise HTTPException(
            status_code=409,
            detail=f"You already have {MAX_UPCOMING_PER_PATIENT} upcoming appointments. "
            "Please cancel one before booking another.",
        )
    cover = coverage_for(db, patient, day)
    if cover.self_pay_required and not body.self_pay_agreed:
        raise HTTPException(
            status_code=409,
            detail=f"{cover.message} To book this visit, please agree to pay for it yourself (self-pay).",
        )
    appointment = models.Appointment(
        patient_id=patient.id,
        provider_id=provider.id,
        start_at=start,
        end_at=slot.end_at,
        status="scheduled",
        reason=body.reason,
        payment="insurance" if cover.covered else "self_pay",
        coverage_status=cover.status,
        coverage_verification_id=cover.verification.id if cover.verification else None,
        self_pay_agreed_at=None if cover.covered else now,
    )
    db.add(appointment)
    try:
        db.flush()
    except IntegrityError:  # two patients raced for the same slot: the unique index decides
        db.rollback()
        raise HTTPException(status_code=409, detail="That slot was just taken. Please choose another time.") from None
    if method is not None:  # the copay / self-pay hold (409 from inside when declined: nothing is booked)
        patient_payments.hold_for_appointment(db, appointment, patient, me.account, method, cover)
    db.commit()
    db.refresh(appointment)
    log.info("appointment booked: %s (%s with %s)", appointment.number, patient.mrn, provider.staff_number)
    send_link(db, appointment, request)  # the patient's link by e-mail / text (fake outbox in development)
    db.commit()
    return appointment_out(db, appointment)


@router.post("/{appointment_id}/cancel", response_model=schemas.AppointmentOut)
def cancel(appointment_id: int, body: schemas.AppointmentCancel, request: Request, db: Session = Depends(get_db)):
    """The patient cancels their own appointment (identity checked again; the slot becomes free)."""
    if not BOOK_LIMITER.allow(_caller(request)):
        raise HTTPException(status_code=429, detail="Too many requests. Please try again in a few minutes.")
    patient = _patient_for(db, body)
    appointment = db.get(models.Appointment, appointment_id)
    if appointment is None or appointment.patient_id != patient.id:
        raise HTTPException(status_code=404, detail="Appointment not found.")
    if appointment.status != "scheduled":
        raise HTTPException(
            status_code=409, detail=f"This appointment is already {appointment.status.replace('_', ' ')}."
        )
    appointment.status = "canceled"
    appointment.canceled_at = _now()
    appointment.canceled_by = CANCELED_BY_PATIENT
    patient_payments.on_appointment_status(db, appointment)
    assignments.on_appointment_status(
        db, appointment
    )  # its medical-assistant task is withdrawn  # the copay / self-pay hold is released
    db.commit()
    log.info("appointment canceled by patient: %s", appointment.number)
    return appointment_out(db, appointment)


@router.post("/{appointment_id}/check-in", response_model=schemas.CheckInStart)
def check_in_by_identity(
    appointment_id: int, body: schemas.AppointmentCheckIn, request: Request, db: Session = Depends(get_db)
):
    """The patient starts the check-in from the lookup page (identity checked again) — from
    CHECK_IN_WINDOW_HOURS before the visit until it ends (``409`` outside, saying when it opens).
    Answers the check-in page to open (check_ins.py); the visit is checked in once it is signed."""
    if not BOOK_LIMITER.allow(_caller(request)):
        raise HTTPException(status_code=429, detail="Too many requests. Please try again in a few minutes.")
    patient = _patient_for(db, body)
    appointment = db.get(models.Appointment, appointment_id)
    if appointment is None or appointment.patient_id != patient.id:
        raise HTTPException(status_code=404, detail="Appointment not found.")
    return _start_check_in(db, appointment, "identity", request)


# ---------------------------------------------------------------------------- a signed-in patient

me_router = APIRouter(prefix="/me", tags=["me"])


def my_records(db: Session, account: models.Account) -> list[models.Patient]:
    """The account's patient records. Records registered with the account's e-mail address and
    not claimed by anyone are attached on the way (caller commits)."""
    email = patients.normalize_email(account.email)
    if email:
        for row in db.execute(
            select(models.Patient).where(models.Patient.email_norm == email, models.Patient.account_id.is_(None))
        ).scalars():
            row.account_id = account.id
        db.flush()
    return (
        db.execute(select(models.Patient).where(models.Patient.account_id == account.id).order_by(models.Patient.id))
        .scalars()
        .all()
    )


def _my_record_out(account: models.Account, p: models.Patient) -> schemas.MyPatientRecord:
    same_email = bool(p.email_norm) and p.email_norm == patients.normalize_email(account.email)
    return schemas.MyPatientRecord(
        **patients.summary(p).model_dump(),
        email=p.email,
        phone=p.phone,
        linked_by="email" if same_email else "identity",
    )


@me_router.get("/patients", response_model=list[schemas.MyPatientRecord])
def my_patients(p: auth.Principal = Depends(auth.patient_principal), db: Session = Depends(get_db)):
    """The patient records that belong to this account."""
    rows = my_records(db, p.account)
    db.commit()
    return [_my_record_out(p.account, r) for r in rows]


@me_router.post("/patients/link", response_model=list[schemas.MyPatientRecord])
def link_my_patient(
    body: schemas.PatientLinkRequest, p: auth.Principal = Depends(auth.patient_principal), db: Session = Depends(get_db)
):
    """Attach a record by identity (name + date of birth; e-mail / phone count too). ``404`` no
    such record — register first; ``409`` it belongs to another account."""
    record = _patient_for(db, body)
    if record.account_id and record.account_id != p.account.id:
        raise HTTPException(
            status_code=409,
            detail="This patient record is already linked to another account. Please ask the front desk.",
        )
    record.account_id = p.account.id
    rows = my_records(db, p.account)
    db.commit()
    log.info("patient record %s linked to account %s", record.mrn, p.account.email)
    return [_my_record_out(p.account, r) for r in rows]


@me_router.get("/appointments", response_model=schemas.MyAppointments)
def my_appointments(p: auth.Principal = Depends(auth.patient_principal), db: Session = Depends(get_db)):
    """Every appointment on the account's records: upcoming (scheduled, not over) and the rest."""
    rows = my_records(db, p.account)
    db.commit()
    now = _now()
    upcoming: list[schemas.AppointmentOut] = []
    past: list[schemas.AppointmentOut] = []
    if rows:
        items = (
            db.execute(
                select(models.Appointment)
                .where(models.Appointment.patient_id.in_([r.id for r in rows]))
                .order_by(models.Appointment.start_at.desc())
            )
            .scalars()
            .all()
        )
        for a in items:
            out = appointment_out(db, a)
            (upcoming if a.status == "scheduled" and as_utc(a.end_at) >= now else past).append(out)
        upcoming.reverse()  # soonest first; the past stays newest first
    return schemas.MyAppointments(patients=[_my_record_out(p.account, r) for r in rows], upcoming=upcoming, past=past)


@me_router.post("/appointments/{appointment_id}/confirm", response_model=schemas.AppointmentOut)
def confirm_my_appointment(
    appointment_id: int, p: auth.Principal = Depends(auth.patient_principal), db: Session = Depends(get_db)
):
    rows = my_records(db, p.account)
    appointment = db.get(models.Appointment, appointment_id)
    if appointment is None or appointment.patient_id not in {r.id for r in rows}:
        raise HTTPException(status_code=404, detail="Appointment not found.")
    confirm(appointment, "account")
    db.commit()
    log.info("appointment confirmed from the patient's account: %s", appointment.number)
    return appointment_out(db, appointment)


@me_router.post("/appointments/{appointment_id}/check-in", response_model=schemas.CheckInStart)
def check_in_my_appointment(
    appointment_id: int,
    request: Request,
    p: auth.Principal = Depends(auth.patient_principal),
    db: Session = Depends(get_db),
):
    """Start the check-in from the patient's account — from CHECK_IN_WINDOW_HOURS before the visit
    until it ends. Answers the check-in page to open (questionnaire, estimate, signatures)."""
    rows = my_records(db, p.account)
    appointment = db.get(models.Appointment, appointment_id)
    if appointment is None or appointment.patient_id not in {r.id for r in rows}:
        raise HTTPException(status_code=404, detail="Appointment not found.")
    return _start_check_in(db, appointment, "account", request)


@me_router.post("/appointments/{appointment_id}/cancel", response_model=schemas.AppointmentOut)
def cancel_my_appointment(
    appointment_id: int, p: auth.Principal = Depends(auth.patient_principal), db: Session = Depends(get_db)
):
    rows = my_records(db, p.account)
    appointment = db.get(models.Appointment, appointment_id)
    if appointment is None or appointment.patient_id not in {r.id for r in rows}:
        raise HTTPException(status_code=404, detail="Appointment not found.")
    if appointment.status != "scheduled":
        raise HTTPException(
            status_code=409, detail=f"This appointment is already {appointment.status.replace('_', ' ')}."
        )
    appointment.status = "canceled"
    appointment.canceled_at = _now()
    appointment.canceled_by = CANCELED_BY_PATIENT
    patient_payments.on_appointment_status(db, appointment)
    assignments.on_appointment_status(db, appointment)  # its medical-assistant task is withdrawn
    db.commit()
    log.info("appointment canceled by the patient's account: %s", appointment.number)
    return appointment_out(db, appointment)


# ---------------------------------------------------------------------------- the link's endpoints


@router.get("/by-token/{token}", response_model=schemas.AppointmentOut)
def by_token(token: str, request: Request, db: Session = Depends(get_db)):
    """The page behind the patient's link: the appointment with its calendar links. Each open
    is counted (the office can see the patient looked at it)."""
    if not TOKEN_LIMITER.allow(_caller(request)):
        raise HTTPException(status_code=429, detail="Too many requests. Please try again in a few minutes.")
    appointment = _by_token(db, token)
    appointment.link_opens = (appointment.link_opens or 0) + 1
    appointment.link_last_opened_at = _now()
    db.commit()
    return appointment_out(db, appointment, token=token)


@router.get("/by-token/{token}/calendar.ics", include_in_schema=True)
def by_token_ics(token: str, request: Request, db: Session = Depends(get_db)):
    """The appointment as an .ics file — phones open it in the calendar app, desktops import it."""
    if not TOKEN_LIMITER.allow(_caller(request)):
        raise HTTPException(status_code=429, detail="Too many requests. Please try again in a few minutes.")
    appointment = _by_token(db, token)
    return Response(
        ics(db, appointment),
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="appointment-{appointment.number}.ics"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/by-token/{token}/confirm", response_model=schemas.AppointmentOut)
def by_token_confirm(token: str, request: Request, db: Session = Depends(get_db)):
    """The reminder's "Confirm" link: the patient is coming."""
    if not TOKEN_LIMITER.allow(_caller(request)):
        raise HTTPException(status_code=429, detail="Too many requests. Please try again in a few minutes.")
    appointment = _by_token(db, token)
    confirm(appointment, "link")
    db.commit()
    log.info("appointment confirmed from the link: %s", appointment.number)
    return appointment_out(db, appointment, token=token)


@router.post("/by-token/{token}/check-in", response_model=schemas.CheckInStart)
def by_token_check_in(token: str, request: Request, db: Session = Depends(get_db)):
    """Start the check-in from the appointment link (the reminder links here with ``?action=check-in``)."""
    if not TOKEN_LIMITER.allow(_caller(request)):
        raise HTTPException(status_code=429, detail="Too many requests. Please try again in a few minutes.")
    appointment = _by_token(db, token)
    return _start_check_in(db, appointment, "link", request)


@router.post("/by-token/{token}/cancel", response_model=schemas.AppointmentOut)
def by_token_cancel(token: str, request: Request, db: Session = Depends(get_db)):
    """Cancel from the link (the token is the proof it is the patient's own appointment)."""
    if not TOKEN_LIMITER.allow(_caller(request)):
        raise HTTPException(status_code=429, detail="Too many requests. Please try again in a few minutes.")
    appointment = _by_token(db, token)
    if appointment.status != "scheduled":
        raise HTTPException(
            status_code=409, detail=f"This appointment is already {appointment.status.replace('_', ' ')}."
        )
    appointment.status = "canceled"
    appointment.canceled_at = _now()
    appointment.canceled_by = CANCELED_BY_PATIENT
    patient_payments.on_appointment_status(db, appointment)
    assignments.on_appointment_status(db, appointment)  # its medical-assistant task is withdrawn
    db.commit()
    log.info("appointment canceled from the link: %s", appointment.number)
    return appointment_out(db, appointment, token=token)


# ---------------------------------------------------------------------------- staff endpoints


@router.post("/reminders/run", response_model=schemas.ReminderRun)
def run_reminders(
    as_of: datetime | None = Query(
        default=None, description="Pretend it is this moment (dev/test only) — to try the reminder a day early"
    ),
    p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    """Run the reminder job now (it also runs by itself every REMINDER_CHECK_SECONDS)."""
    import reminders

    if as_of is not None and config.IS_PROD:
        raise HTTPException(status_code=400, detail="as_of is for development and testing only.")
    now = as_utc(as_of) if as_of else _now()
    rows, delivered = reminders.send_due_reminders(db, now)
    log.info(
        "reminders run by %s as of %s: %d due, %d delivered", p.account.email, now.isoformat(), len(rows), delivered
    )
    return schemas.ReminderRun(
        as_of=now, due=len(rows), sent=delivered, numbers=[a.number for a in rows], simulated=as_of is not None
    )


@router.post("/{appointment_id}/send-link", response_model=schemas.AppointmentOut)
def resend_link(
    appointment_id: int,
    request: Request,
    p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    """Send the patient's link again (same link; e-mail and/or text to what is on the record)."""
    appointment = db.get(models.Appointment, appointment_id)
    if appointment is None:
        raise HTTPException(status_code=404, detail="Appointment not found.")
    send_link(db, appointment, request, kind="resend")
    db.commit()
    log.info("appointment %s: link re-sent by %s", appointment.number, p.account.email)
    return appointment_out(db, appointment, office=True)


@router.get("", response_model=schemas.AppointmentPage)
def list_appointments(
    day: date | None = Query(default=None, alias="date", description="One day (office time zone)"),
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to", description="Inclusive"),
    provider_id: int | None = Query(default=None),
    patient_id: int | None = Query(default=None, description="One patient's history (past visits included)"),
    status: str | None = Query(default=None, description="scheduled · canceled · completed · no_show"),
    checked_in: bool | None = Query(default=None, description="true: checked in · false: not checked in"),
    assistant: str | None = Query(
        default=None,
        pattern="^(none|open|reported|completed)$",
        description="Medical assistant: none (nobody on the visit) · open (assigned / taken) · "
        "reported (cannot do it — needs reassignment) · completed",
    ),
    q: str = Query(default="", max_length=120, description="Patient name, record number or confirmation number"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    _p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    """The agenda. Without a date filter: everything from now on — except for one patient's
    history (``patient_id``), which includes past visits."""
    tz = office_tz()
    stmt = select(models.Appointment)
    if day:
        date_from = date_to = day
    if date_from is None and date_to is None and patient_id is None:
        stmt = stmt.where(models.Appointment.end_at >= _now())
    if patient_id:
        stmt = stmt.where(models.Appointment.patient_id == patient_id)
    if date_from:
        stmt = stmt.where(
            models.Appointment.start_at >= datetime.combine(date_from, datetime.min.time(), tz).astimezone(timezone.utc)
        )
    if date_to:
        stmt = stmt.where(
            models.Appointment.start_at
            < datetime.combine(date_to + timedelta(days=1), datetime.min.time(), tz).astimezone(timezone.utc)
        )
    if provider_id:
        stmt = stmt.where(models.Appointment.provider_id == provider_id)
    if status:
        if status not in models.APPOINTMENT_STATUSES:
            raise HTTPException(status_code=422, detail="Unknown status.")
        stmt = stmt.where(models.Appointment.status == status)
    if checked_in is not None:
        stmt = stmt.where(
            models.Appointment.checked_in_at.is_not(None) if checked_in else models.Appointment.checked_in_at.is_(None)
        )
    if assistant:
        stmt = stmt.where(assignments.agenda_filter(assistant))
    term = q.strip()
    if term:
        upper = term.upper().replace(" ", "")
        if upper.startswith("APT") and upper.lstrip("APT-").isdigit():
            stmt = stmt.where(models.Appointment.id == int(upper.lstrip("APT-")))
        elif upper.startswith("P") and upper.lstrip("P-").isdigit():
            stmt = stmt.where(models.Appointment.patient_id == int(upper.lstrip("P-")))
        else:
            like = f"%{patients.normalize_name(term)}%"
            stmt = stmt.join(models.Patient, models.Patient.id == models.Appointment.patient_id).where(
                or_(models.Patient.last_name_norm.like(like), models.Patient.first_name_norm.like(like))
            )
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(stmt.order_by(models.Appointment.start_at, models.Appointment.id).offset(offset).limit(limit))
    return schemas.AppointmentPage(
        items=[appointment_out(db, a, office=True) for a in rows.scalars()], total=total, offset=offset, limit=limit
    )


@router.get("/{appointment_id}/check-ins", response_model=list[schemas.CheckInRecord])
def appointment_check_ins(
    appointment_id: int, _p: auth.Principal = Depends(auth.admin_principal), db: Session = Depends(get_db)
):
    """Every check-in of the visit, newest first: the answers, both signatures, the estimate, the
    pre-authorization — or why it failed."""
    appointment = db.get(models.Appointment, appointment_id)
    if appointment is None:
        raise HTTPException(status_code=404, detail="Appointment not found.")
    out = [check_ins.record_out(db, c) for c in check_ins.history(db, appointment)]
    db.commit()  # a check-in that just timed out is stored as failed
    return out


@router.get("/{appointment_id}", response_model=schemas.AppointmentOut)
def get_appointment(
    appointment_id: int, _p: auth.Principal = Depends(auth.admin_principal), db: Session = Depends(get_db)
):
    appointment = db.get(models.Appointment, appointment_id)
    if appointment is None:
        raise HTTPException(status_code=404, detail="Appointment not found.")
    return appointment_out(db, appointment, office=True)


@router.patch("/{appointment_id}", response_model=schemas.AppointmentOut)
def update_appointment(
    appointment_id: int,
    body: schemas.AppointmentStaffUpdate,
    request: Request,
    p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    """A manager marks the visit completed (the payment hold is charged) / no-show or cancels it
    (the hold is released), or leaves a note."""
    appointment = db.get(models.Appointment, appointment_id)
    if appointment is None:
        raise HTTPException(status_code=404, detail="Appointment not found.")
    if body.status and body.status != appointment.status:
        if appointment.status == "canceled" and body.status != "scheduled":
            raise HTTPException(status_code=409, detail="A canceled appointment can only be re-scheduled.")
        if body.status == "scheduled":
            # Re-instating: the slot must still be free.
            clash = db.execute(
                select(models.Appointment.id).where(
                    models.Appointment.provider_id == appointment.provider_id,
                    models.Appointment.start_at == appointment.start_at,
                    models.Appointment.status == "scheduled",
                    models.Appointment.id != appointment.id,
                )
            ).first()
            if clash:
                raise HTTPException(status_code=409, detail="That slot has been taken by another appointment.")
            appointment.canceled_at = None
            appointment.canceled_by = None
            check_ins.undo(db, appointment)  # a re-instated visit starts without the old check-in
        if body.status == "canceled":
            appointment.canceled_at = _now()
            appointment.canceled_by = CANCELED_BY_STAFF
        appointment.status = body.status
        # completed → the copay / self-pay hold is charged; canceled / no-show → released
        patient_payments.on_appointment_status(db, appointment, p.account)
        assignments.on_appointment_status(db, appointment)  # canceled / no-show: the assistant's task too
        # completed → the visit summary is prepared as a draft and the provider asked to sign it
        visit_summaries.on_appointment_status(db, appointment)
    if body.notes is not None:
        appointment.notes = body.notes
    if body.confirmed is True:
        confirm(appointment, "staff")
    elif body.confirmed is False:
        appointment.confirmed_at = None
        appointment.confirmed_via = None
    if body.checked_in is True and appointment.checked_in_at is None:
        # The desk starts the check-in (any time while scheduled): the page's link goes to the
        # patient, who answers and signs within CHECK_IN_LINK_MINUTES — or it fails, saying why.
        c, _token = check_ins.start(db, appointment, CHECKED_IN_BY_STAFF, request, actor=p.account)
        accounts.audit(
            db, p.account, "appointment.check_in.link", None, appointment=appointment.number, status=c.status
        )
    elif body.checked_in is False:
        if appointment.checked_in_at is not None:
            was = appointment.checked_in_via
            check_ins.undo(db, appointment)
            accounts.audit(db, p.account, "appointment.check_in.undo", None, appointment=appointment.number, was=was)
        elif check_ins.cancel_pending(db, appointment, "Called off by the office."):
            accounts.audit(db, p.account, "appointment.check_in.cancel", None, appointment=appointment.number)
    db.commit()
    log.info("appointment %s updated by %s: status=%s", appointment.number, p.account.email, appointment.status)
    return appointment_out(db, appointment, office=True)
