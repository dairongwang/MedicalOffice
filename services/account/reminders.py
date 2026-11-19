"""Appointment reminders — the message the day before, with "confirm" and "cancel".

    due(db, now)                  scheduled appointments that start within REMINDER_HOURS_BEFORE of ``now``,
                                  were booked at least REMINDER_MIN_AGE_MINUTES ago (the booking message
                                  just went out) and have no reminder yet
    send_due_reminders(db, now)   send each one by e-mail and/or text to what is on the patient record —
                                  SMTP / Twilio when configured, otherwise the built-in FAKE outbox
                                  (/api/v1/messages/fake/outbox, `python dev.py outbox`) — and record the
                                  outcome on the row (reminder_sent_at, reminder_channels, reminder_note)
    run_forever()                 the in-process job: every REMINDER_CHECK_SECONDS (main.py lifespan) — it also
                                  fails check-in pages not completed in time (check_ins.expire_due) and drafts
                                  the visit summary of visits that are over (visit_summaries.prepare_due)

The message carries the patient's appointment link with an action: ``<link>?action=confirm``
confirms on opening (``POST /appointments/by-token/{token}/confirm``); ``<link>?action=check-in``
checks in on opening once the check-in window is open (``POST …/check-in``, appointments.check_in);
``<link>?action=cancel`` opens the page with cancellation in front. A patient account confirms / cancels from
"My appointments" too; the office sees "confirmed ✓ (via link)" and "reminder sent by …" on the
agenda.

Testing without waiting a day: outside production the job accepts an ``as_of`` moment
(``POST /appointments/reminders/run?as_of=…``, the "Run reminders as of …" control on the office
administration dashboard, or the tests calling ``send_due_reminders(db, now=…)`` directly).
Rows are stamped with the real clock either way, so a simulated run never sends twice.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

import appointments
import config
import messaging
import models
import patients
import twofactor

log = logging.getLogger("account.reminders")

CONFIRMED_VIA_LINK = "link"
CONFIRMED_VIA_ACCOUNT = "account"
CONFIRMED_VIA_STAFF = "staff"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def due(db: Session, now: datetime | None = None) -> list[models.Appointment]:
    now = now or _now()
    horizon = now + timedelta(hours=config.REMINDER_HOURS_BEFORE)
    booked_before = now - timedelta(minutes=config.REMINDER_MIN_AGE_MINUTES)
    return (
        db.execute(
            select(models.Appointment)
            .where(
                models.Appointment.status == "scheduled",
                models.Appointment.reminder_sent_at.is_(None),
                models.Appointment.start_at > now,
                models.Appointment.start_at <= horizon,
                models.Appointment.created_at <= booked_before,
            )
            .order_by(models.Appointment.start_at)
        )
        .scalars()
        .all()
    )


def _message(db: Session, a: models.Appointment) -> tuple[str, str, str]:
    """(subject, e-mail body, text-message body) — the reminder with its confirm / cancel links."""
    provider = db.get(models.Worker, a.provider_id)
    patient = db.get(models.Patient, a.patient_id)
    when = appointments.format_local(a.start_at)
    who = appointments._provider_name(provider)  # noqa: SLF001 - same package, shared formatting
    link = a.link or ""
    confirm = f"{link}?action=confirm" if link else ""
    cancel = f"{link}?action=cancel" if link else ""
    check_in = f"{link}?action=check-in" if link else ""
    opens, _closes = appointments.check_in_window(a)
    local = appointments.as_utc(a.start_at).astimezone(appointments.office_tz())
    subject = f"Reminder: your {config.OFFICE_NAME} appointment {a.number} — {when}"
    lines = [
        f"Hi {patient.first_name}, a reminder of your appointment tomorrow.",
        "",
        f"  With:    {who}",
        f"  When:    {when} ({appointments.office_tz()})",
        f"  Where:   {config.OFFICE_NAME}{' · ' + config.OFFICE_ADDRESS if config.OFFICE_ADDRESS else ''}",
        f"  Number:  {a.number}",
        "",
        "Please let us know you are coming:",
        f"  Confirm:  {confirm}",
        f"  Cancel:   {cancel}",
        "",
        f"Check in from the app (from {appointments.format_local(opens)} until your visit ends) — "
        "or at the front desk when you arrive:",
        f"  Check in: {check_in}",
        "",
        "You can also confirm, check in or cancel from your appointment page or your patient account. "
        "Please arrive a few minutes early."
        + (f" Questions? Call {config.OFFICE_PHONE}." if config.OFFICE_PHONE else ""),
    ]
    sms = (
        f"{config.OFFICE_NAME} reminder: {a.number} with {who} tomorrow, {local:%a} {local:%b} {local.day} at "
        f"{local:%H:%M}. Confirm: {confirm}  Cancel: {cancel}"
    )
    return subject, "\n".join(lines), sms


def send_reminder(db: Session, a: models.Appointment, now: datetime | None = None) -> list[str]:
    """Send on every channel the patient has a destination for; record the outcome. Returns the
    channels that delivered. Never raises — a provider problem lands in ``reminder_note``."""
    patient = db.get(models.Patient, a.patient_id)
    subject, body, sms = _message(db, a)
    sent: list[str] = []
    notes: list[str] = []
    phone = patients.normalize_phone(patient.phone)
    for name, destination in ((messaging.EMAIL, patient.email), (messaging.SMS, f"+{phone}" if phone else None)):
        ch = messaging.channel(name)
        if not destination:
            notes.append(f"{ch.label}: no {'address' if name == messaging.EMAIL else 'number'} on the patient record")
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
        notes.append(f"{ch.label} to {masked}" + (" (built-in fake — see the test inbox)" if ch.fake else ""))
    a.reminder_sent_at = _now()  # the real clock, even for a simulated run: never twice
    a.reminder_channels = ",".join(sent)
    a.reminder_note = ("; ".join(notes))[:500] or None
    log.info("reminder for %s: %s", a.number, ",".join(sent) or "not delivered")
    return sent


def send_due_reminders(db: Session, now: datetime | None = None) -> tuple[list[models.Appointment], int]:
    """Everything due as of ``now``; returns (the appointments handled, how many delivered)."""
    rows = due(db, now)
    delivered = 0
    for a in rows:
        if send_reminder(db, a, now):
            delivered += 1
    db.commit()
    return rows, delivered


def run_once() -> int:
    from database import SessionLocal

    with SessionLocal() as db:
        import check_ins

        check_ins.expire_due(db)  # check-in pages not completed in time fail (with the reason)
        import visit_summaries

        visit_summaries.prepare_due(db)  # visits that are over: the summary drafted, the doctor asked to sign
        rows, delivered = send_due_reminders(db)
    if rows:
        log.info("reminders: %d due, %d delivered", len(rows), delivered)
    return delivered


async def run_forever(interval: int) -> None:
    """The in-process job (main.py starts it when REMINDER_CHECK_SECONDS > 0)."""
    while True:
        try:
            await asyncio.to_thread(run_once)
        except Exception:  # noqa: BLE001 - the job must survive a transient database/provider problem
            log.exception("reminder run failed")
        await asyncio.sleep(interval)
