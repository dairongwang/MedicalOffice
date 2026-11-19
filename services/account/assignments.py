"""Medical assistants — the office assigns one to an appointment; the assistant does the task.

A **medical assistant** is a member of staff with the worker role ``medical_assistant``
(models.ROLE_MEDICAL_ASSISTANT). Patients do not book them: the office assigns one to a visit to
prepare it — room the patient, take the vital signs, review medications / allergies and the
check-in answers, tell the provider the patient is ready (models.ASSISTANT_DUTIES).

    The office (administrators — the office administration dashboard's agenda):
    GET    /api/v1/appointments/{id}/assistants    who could be assigned, and whether each one is
                                                   available for this visit (and why not)
    PUT    /api/v1/appointments/{id}/assistant     assign — or reassign — an available assistant
                                                   {worker_id, instructions}; the assistant is told
                                                   by e-mail / text (the fake outbox in development)
    DELETE /api/v1/appointments/{id}/assistant     take the assistant off the visit
    GET    /api/v1/appointments/{id}/assignments   the visit's task history
    GET    /api/v1/assignments?status=&from=&to=&worker_id=   the task board (e.g. status=needs_reassignment)
    The agenda (GET /api/v1/appointments) shows each visit's assistant and filters on it (``assistant=``).

    The medical assistant (signed in to the staff dashboard — "My tasks"):
    GET    /api/v1/staff/me/tasks?from=&to=&status=   their tasks, in time order
    POST   /api/v1/staff/me/tasks/{id}/accept         take the task
    GET    /api/v1/staff/me/tasks/{id}/review         the form of the visit, prepopulated (assistant_review.py):
                                                      vital signs, the medication and allergy lists (the chart
                                                      + the check-in answers), the check-in answers — and what
                                                      is still missing
    PUT    /api/v1/staff/me/tasks/{id}/review         save it (a draft: measurements, items cleared with notes, …)
    POST   /api/v1/staff/me/tasks/{id}/complete       {review, note} — done: every duty must be done
    POST   /api/v1/staff/me/tasks/{id}/report         {reason, note} — "I cannot do this one": the office
                                                      sees it at once (and by e-mail / text when
                                                      OFFICE_NOTIFY_EMAIL / OFFICE_NOTIFY_PHONE is set)
    GET    /api/v1/staff/me/tasks/{id}/check-in       the patient's check-in answers (to review them)

**Availability** (``availability``) — what "based on availability" means; the server enforces it:

    * an *active* worker whose role is medical_assistant;
    * in on the visit's weekday, and the whole visit inside their hours that day (their week on
      the staff dashboard — the standard Monday–Friday 9–5 unless they changed it);
    * not with another patient then: no other open task (assigned / accepted) on a scheduled
      visit that overlaps this one;
    * not somebody who already reported they cannot take this visit.

**The life of a task** (models.AppointmentAssignment, one row per assignment — the history stays):

    assigned ──accept──▶ accepted ──complete (every duty confirmed)──▶ completed
        │                   │
        └──────report───────┴──▶ reported ──the office reassigns──▶ (a new task for someone else)

    The office can reassign or unassign at any time before completion (the open task is
    *canceled*, the assistant told); canceling the visit (or a no-show) cancels its open task, and
    completing the visit withdraws a task nobody took (a taken one can still be completed).
    A task can be worked on and completed from ASSISTANT_TASK_WINDOW_HOURS (24) before the visit starts —
    nobody rooms a patient for next week — as long as the visit was not canceled. Completing it needs
    every duty done (assistant_review.py): the vital signs typed in, every medication and allergy item
    cleared (each with a note), the check-in answers reviewed; the decisions update the patient's chart.
    At most one task per visit is open at a time (``uq_assignment_open``: two clerks racing cannot both
    assign).

Every office action and every report / completion is in the audit log.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import accounts
import appointments
import assistant_review
import auth
import check_ins
import config
import models
import patients
import schemas
import staff_accounts
import workers
from database import get_db

log = logging.getLogger("account.assignments")

admin_router = APIRouter(tags=["assistants"])
staff_router = APIRouter(prefix="/staff/me/tasks", tags=["staff"])

OPEN = models.ASSIGNMENT_OPEN_STATUSES
DUTY_LABELS = dict(models.ASSISTANT_DUTIES)
REASON_LABELS = dict(models.ASSIGNMENT_REPORT_REASONS)
DEFAULT_DAYS = 14  # "My tasks": from today, two weeks


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _name(w: models.Worker | None) -> str:
    return f"{w.first_name} {w.last_name}" if w is not None else "—"


def _email_of(db: Session, account_id: str | None) -> str | None:
    account = db.get(models.Account, account_id) if account_id else None
    return account.email if account is not None else None


def _split(value: str | None) -> list[str]:
    return [x for x in (value or "").replace(", ", ",").split(",") if x]


# ---------------------------------------------------------------------------- reading


def history(db: Session, a: models.Appointment) -> list[models.AppointmentAssignment]:
    """Every task of the visit, newest first."""
    return (
        db.execute(
            select(models.AppointmentAssignment)
            .where(models.AppointmentAssignment.appointment_id == a.id)
            .order_by(models.AppointmentAssignment.id.desc())
        )
        .scalars()
        .all()
    )


def current_for(db: Session, a: models.Appointment) -> models.AppointmentAssignment | None:
    """The task that matters now: the open one; else a report the office has not acted on; else the
    completed one. None = nobody is (or was) on the visit."""
    rows = history(db, a)
    return (
        next((r for r in rows if r.status in OPEN), None)
        or next((r for r in rows if r.status == "reported" and r.resolved_at is None), None)
        or next((r for r in rows if r.status == "completed"), None)
    )


def summary(db: Session, row: models.AppointmentAssignment) -> schemas.AssignmentSummary:
    w = db.get(models.Worker, row.worker_id)
    return schemas.AssignmentSummary(
        id=row.id,
        status=row.status,
        worker_id=row.worker_id,
        staff_number=w.staff_number if w else "",
        name=_name(w),
        instructions=row.instructions,
        assigned_at=row.assigned_at,
        accepted_at=row.accepted_at,
        completed_at=row.completed_at,
        reported_at=row.reported_at,
        report_reason=row.report_reason,
        report_reason_label=REASON_LABELS.get(row.report_reason or ""),
        report_note=row.report_note,
        needs_reassignment=row.status == "reported" and row.resolved_at is None,
        vitals=assistant_review.vitals_out(assistant_review.vitals_of(row)),
        provider_notified_at=row.provider_notified_at,
        provider_notify_channels=_split(row.provider_notify_channels),
        provider_notify_note=row.provider_notify_note,
    )


def summary_for(db: Session, a: models.Appointment) -> schemas.AssignmentSummary | None:
    row = current_for(db, a)
    return summary(db, row) if row is not None else None


def out(db: Session, row: models.AppointmentAssignment) -> schemas.AssignmentOut:
    a = db.get(models.Appointment, row.appointment_id)
    try:
        duties = [d for d in json.loads(row.duties or "[]") if isinstance(d, str)]
    except ValueError:
        duties = []
    return schemas.AssignmentOut(
        **summary(db, row).model_dump(),
        appointment_id=row.appointment_id,
        appointment_number=a.number if a else "",
        duties=duties,
        completion_note=row.completion_note,
        assigned_by=_email_of(db, row.assigned_by),
        resolved_at=row.resolved_at,
        resolved_by=_email_of(db, row.resolved_by),
        replaced_by_id=row.replaced_by_id,
        canceled_at=row.canceled_at,
        cancel_reason=row.cancel_reason,
        notified_at=row.notified_at,
        notify_channels=_split(row.notify_channels),
        notified_to=_split(row.notified_to),
        notify_note=row.notify_note,
        review_items=[assistant_review.item_out(i) for i in assistant_review.items_of(db, row)],
        check_in_note=assistant_review.checklist(row).get("check_in_note"),
    )


# ---------------------------------------------------------------------------- availability


def is_assistant(w: models.Worker | None) -> bool:
    return w is not None and w.active and w.role == models.ROLE_MEDICAL_ASSISTANT


def list_assistants(db: Session) -> list[models.Worker]:
    return (
        db.execute(
            select(models.Worker)
            .where(models.Worker.active.is_(True), models.Worker.role == models.ROLE_MEDICAL_ASSISTANT)
            .order_by(models.Worker.last_name_norm, models.Worker.first_name_norm)
        )
        .scalars()
        .all()
    )


def _busy_with(db: Session, w: models.Worker, a: models.Appointment) -> models.Appointment | None:
    """Another scheduled visit overlapping this one on which the worker has an open task."""
    return (
        db.execute(
            select(models.Appointment)
            .join(models.AppointmentAssignment, models.AppointmentAssignment.appointment_id == models.Appointment.id)
            .where(
                models.AppointmentAssignment.worker_id == w.id,
                models.AppointmentAssignment.status.in_(OPEN),
                models.Appointment.id != a.id,
                models.Appointment.status == "scheduled",
                models.Appointment.start_at < a.end_at,
                models.Appointment.end_at > a.start_at,
            )
            .order_by(models.Appointment.start_at)
            .limit(1)
        )
        .scalars()
        .first()
    )


def availability(db: Session, a: models.Appointment, w: models.Worker) -> schemas.AssistantCandidate:
    """Can ``w`` take this visit? (see the module docstring) — with the reason when not."""
    tz = appointments.office_tz()
    start = appointments.as_utc(a.start_at).astimezone(tz)
    end = appointments.as_utc(a.end_at).astimezone(tz)
    weekday = schemas.WEEKDAY_NAMES[start.weekday()]
    day = workers.availability_of(db, w)[start.weekday()]
    works = day.available and day.end_time > day.start_time
    hours = f"{day.start_time:%H:%M}–{day.end_time:%H:%M}" if works else None
    rows = history(db, a)
    current = any(r.worker_id == w.id and r.status in OPEN for r in rows)
    reason = None
    if not w.active:
        reason = "Inactive on the roster."
    elif w.role != models.ROLE_MEDICAL_ASSISTANT:
        reason = "Not a medical assistant."
    elif current:
        reason = "Already on this visit."
    elif not works:
        reason = f"Not in on {weekday}s."
    elif not (
        datetime.combine(start.date(), day.start_time, tz) <= start
        and end <= datetime.combine(start.date(), day.end_time, tz)
    ):
        reason = f"Works {hours} on {weekday}s — the visit is {start:%H:%M}–{end:%H:%M}."
    elif any(r.worker_id == w.id and r.status == "reported" for r in rows):
        reason = "Reported they cannot take this visit."
    else:
        busy = _busy_with(db, w, a)
        if busy is not None:
            b_start = appointments.as_utc(busy.start_at).astimezone(tz)
            b_end = appointments.as_utc(busy.end_at).astimezone(tz)
            reason = f"With another patient then ({busy.number}, {b_start:%H:%M}–{b_end:%H:%M})."
    return schemas.AssistantCandidate(
        worker_id=w.id,
        staff_number=w.staff_number,
        first_name=w.first_name,
        last_name=w.last_name,
        hours=hours,
        available=reason is None,
        reason=reason,
        current=current,
    )


def options(db: Session, a: models.Appointment) -> schemas.AssistantOptions:
    candidates = [availability(db, a, w) for w in list_assistants(db)]
    candidates.sort(key=lambda c: (not c.current, not c.available))  # stable: keeps the name order
    return schemas.AssistantOptions(
        appointment_id=a.id,
        number=a.number,
        status=a.status,
        start_local=appointments.format_local(a.start_at),
        timezone=str(appointments.office_tz()),
        current=summary_for(db, a),
        candidates=candidates,
    )


# ---------------------------------------------------------------------------- messages


def _staff_link(a: models.Appointment) -> str | None:
    staff = (config.STAFF_URL or "").strip()
    if not staff:
        return None
    day = appointments.as_utc(a.start_at).astimezone(appointments.office_tz()).date().isoformat()
    base = appointments.app_base(None).rstrip("/") + staff if staff.startswith("/") else staff
    return f"{base.rstrip('/')}/#/my-tasks?from={day}"


def _admin_link(a: models.Appointment) -> str | None:
    admin = (config.ADMIN_DASHBOARD_URL or "").strip()
    if not admin:
        return None
    base = appointments.app_base(None).rstrip("/") + admin if admin.startswith("/") else admin
    return f"{base.rstrip('/')}/#/appointments?q={a.number}"


def _visit_lines(db: Session, a: models.Appointment) -> list[str]:
    provider = db.get(models.Worker, a.provider_id)
    patient = db.get(models.Patient, a.patient_id)
    return [
        f"  Visit:    {a.number} — {appointments.format_local(a.start_at)} ({appointments.office_tz()})",
        f"  Patient:  {patient.first_name} {patient.last_name} ({patient.mrn})",
        f"  With:     {appointments._provider_name(provider)}",
    ]


def _notify_assistant(db: Session, row: models.AppointmentAssignment, a: models.Appointment, kind: str) -> None:
    """Tell the assistant about a new task (kind "assigned") or one taken away ("withdrawn").
    Never fails the office's action: the outcome is kept on the row for a new task."""
    w = db.get(models.Worker, row.worker_id)
    if w is None:
        return
    local = appointments.as_utc(a.start_at).astimezone(appointments.office_tz())
    when = f"{local:%a} {local:%b} {local.day} at {local:%H:%M}"
    link = _staff_link(a)
    if kind == "assigned":
        subject = f"New task: prepare {a.number} on {when}"
        lines = [f"Hi {w.first_name},", "", "the office assigned you to prepare this visit:", "", *_visit_lines(db, a)]
        if row.instructions:
            lines += [f"  Notes:    {row.instructions}"]
        lines += [
            "",
            "Please take the task in My tasks on the staff dashboard — or report there if you cannot do it,",
            "so that the office can assign someone else." + (f"\n  {link}" if link else ""),
        ]
        sms = f"{config.OFFICE_NAME}: new task — prepare {a.number} on {when}. Take it in My tasks (staff dashboard)."
    else:
        subject = f"Task withdrawn: {a.number} on {when}"
        lines = [f"Hi {w.first_name},", "", f"you no longer need to prepare {a.number} on {when}: {row.cancel_reason}"]
        sms = f"{config.OFFICE_NAME}: task withdrawn — {a.number} on {when}. {row.cancel_reason}"
    sent, to, notes = appointments.deliver(w.email, w.phone, subject, "\n".join(lines), sms, whose="the staff record")
    if kind == "assigned":
        row.notify_channels = ",".join(sent)
        row.notified_to = ", ".join(to)
        row.notify_note = ("; ".join(notes))[:500] or None
        row.notified_at = _now() if sent else None
    log.info("assistant %s told about %s (%s) by %s", w.staff_number, a.number, kind, ",".join(sent) or "nothing")


def _notify_office(db: Session, row: models.AppointmentAssignment, a: models.Appointment) -> None:
    """An assistant reported they cannot do a task: tell the office (when an address / number for it
    is configured — the agenda shows it in any case)."""
    if not (config.OFFICE_NOTIFY_EMAIL or config.OFFICE_NOTIFY_PHONE):
        return
    w = db.get(models.Worker, row.worker_id)
    reason = REASON_LABELS.get(row.report_reason or "", row.report_reason or "")
    subject = f"Please reassign {a.number}: {_name(w)} cannot do the task"
    lines = [
        f"{_name(w)} ({w.staff_number if w else ''}) reported that they cannot prepare this visit:",
        "",
        *_visit_lines(db, a),
        f"  Reason:   {reason}" + (f" — {row.report_note}" if row.report_note else ""),
        "",
        "Assign another medical assistant on the office administration dashboard (Appointments):",
        f"  {_admin_link(a) or 'Appointments tab → ' + a.number}",
    ]
    sms = f"{config.OFFICE_NAME}: {_name(w)} cannot prepare {a.number} ({reason}). Please reassign it."
    sent, _to, _notes = appointments.deliver(
        config.OFFICE_NOTIFY_EMAIL or None,
        config.OFFICE_NOTIFY_PHONE or None,
        subject,
        "\n".join(lines),
        sms,
        whose="the office settings",
    )
    log.info("office told that %s cannot do %s (by %s)", w.staff_number if w else "?", a.number, ",".join(sent))


# ---------------------------------------------------------------------------- the office's actions


def assign(
    db: Session,
    a: models.Appointment,
    w: models.Worker,
    *,
    actor: models.Account | None,
    instructions: str | None = None,
) -> models.AppointmentAssignment:
    """Assign ``w`` to the visit — replacing the open task (reassignment) and settling a report the
    assistant made. ``409`` when the visit is not on, the assistant is not available or the task
    is already done. Caller commits."""
    now = _now()
    if a.status != "scheduled":
        raise HTTPException(
            status_code=409, detail=f"This visit is {a.status.replace('_', ' ')} — it needs no assistant."
        )
    if appointments.as_utc(a.end_at) <= now:
        raise HTTPException(status_code=409, detail="This visit is over.")
    rows = history(db, a)
    if any(r.status == "completed" for r in rows):
        raise HTTPException(status_code=409, detail="The assistant already completed the task for this visit.")
    candidate = availability(db, a, w)
    if candidate.current:
        raise HTTPException(status_code=409, detail=f"{_name(w)} is already assigned to this visit.")
    if not candidate.available:
        raise HTTPException(status_code=409, detail=f"{_name(w)} is not available for this visit: {candidate.reason}")
    previous = next((r for r in rows if r.status in OPEN), None)
    if previous is not None:  # reassignment: the open task goes first (one open task per visit)
        previous.status = "canceled"
        previous.canceled_at = now
        previous.cancel_reason = f"Reassigned to {_name(w)} by the office."
        db.flush()
    row = models.AppointmentAssignment(
        appointment_id=a.id,
        worker_id=w.id,
        status="assigned",
        instructions=instructions,
        assigned_by=actor.id if actor else None,
        assigned_at=now,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:  # another clerk assigned somebody in the same instant
        db.rollback()
        raise HTTPException(status_code=409, detail="Someone else just assigned this visit. Please reload.") from None
    reports = [r for r in rows if r.status == "reported" and r.resolved_at is None]
    for r in reports:
        r.resolved_at, r.resolved_by, r.replaced_by_id = now, actor.id if actor else None, row.id
    if previous is not None:
        previous.replaced_by_id = row.id
        if previous.worker_id != w.id:
            _notify_assistant(db, previous, a, "withdrawn")
    _notify_assistant(db, row, a, "assigned")
    action = "appointment.assistant.reassign" if previous is not None or reports else "appointment.assistant.assign"
    accounts.audit(
        db,
        actor,
        action,
        None,
        appointment=a.number,
        worker=w.staff_number,
        replaced=[db.get(models.Worker, r.worker_id).staff_number for r in ([previous] if previous else []) + reports],
    )
    db.flush()
    log.info("%s: %s assigned to %s", a.number, w.staff_number, "reassign" if previous or reports else "assign")
    return row


def unassign(db: Session, a: models.Appointment, *, actor: models.Account | None) -> None:
    """Take the assistant off the visit (the open task is canceled, a report is settled). Caller commits."""
    now = _now()
    rows = history(db, a)
    open_ = [r for r in rows if r.status in OPEN]
    reports = [r for r in rows if r.status == "reported" and r.resolved_at is None]
    if not open_ and not reports:
        raise HTTPException(status_code=409, detail="No assistant is assigned to this visit.")
    for r in open_:
        r.status, r.canceled_at, r.cancel_reason = "canceled", now, "Taken off the visit by the office."
        _notify_assistant(db, r, a, "withdrawn")
    for r in reports:
        r.resolved_at, r.resolved_by = now, actor.id if actor else None
    accounts.audit(
        db,
        actor,
        "appointment.assistant.unassign",
        None,
        appointment=a.number,
        workers=[db.get(models.Worker, r.worker_id).staff_number for r in open_ + reports],
    )
    db.flush()


def on_appointment_status(db: Session, a: models.Appointment) -> None:
    """The visit was canceled or the patient did not come: its open task is canceled (the assistant
    is told about a cancellation) and a pending report needs no reassignment any more. The visit was
    completed: a task nobody took is withdrawn (one that was taken can still be completed)."""
    if a.status not in ("canceled", "no_show", "completed"):
        return
    now = _now()
    why = {
        "canceled": "The visit was canceled.",
        "no_show": "The patient did not come (no-show).",
        "completed": "The visit was completed before the task was taken.",
    }[a.status]
    for r in history(db, a):
        if r.status in OPEN and (a.status != "completed" or r.status == "assigned"):
            r.status, r.canceled_at, r.cancel_reason = "canceled", now, why
            if a.status == "canceled":
                _notify_assistant(db, r, a, "withdrawn")
        elif r.status == "reported" and r.resolved_at is None:
            r.resolved_at = now
    db.flush()


# ---------------------------------------------------------------------------- the assistant's actions


def complete_opens_at(a: models.Appointment) -> datetime:
    return appointments.as_utc(a.start_at) - timedelta(hours=config.ASSISTANT_TASK_WINDOW_HOURS)


def _visit_is_on(a: models.Appointment, now: datetime) -> bool:
    return a.status == "scheduled" and appointments.as_utc(a.end_at) > now


def accept(db: Session, row: models.AppointmentAssignment, actor: models.Account) -> None:
    if row.status == "accepted":
        return  # taking it twice is fine
    if row.status != "assigned":
        raise HTTPException(status_code=409, detail=f"This task is {row.status} — it cannot be taken.")
    a = db.get(models.Appointment, row.appointment_id)
    if not _visit_is_on(a, _now()):
        raise HTTPException(status_code=409, detail=f"The visit is {'over' if a.status == 'scheduled' else a.status}.")
    row.status, row.accepted_at = "accepted", _now()
    accounts.audit(db, actor, "assignment.accept", None, appointment=a.number, task=row.id)


def _check_workable(db: Session, row: models.AppointmentAssignment) -> models.Appointment:
    """The review can be filled in / completed: the task was taken, the visit is on (or was completed by
    the office) and its time has come."""
    if row.status == "assigned":
        raise HTTPException(status_code=409, detail="Take the task first.")
    if row.status != "accepted":
        raise HTTPException(status_code=409, detail=f"This task is {row.status} — it cannot be changed.")
    a = db.get(models.Appointment, row.appointment_id)
    if a.status not in ("scheduled", "completed"):
        raise HTTPException(
            status_code=409,
            detail=f"The visit is {a.status.replace('_', ' ')} — there is nothing to complete.",
        )
    opens = complete_opens_at(a)
    if _now() < opens:
        raise HTTPException(
            status_code=409,
            detail=f"Too early: this task can be worked on from {appointments.format_local(opens)} "
            f"({appointments.office_tz()}), {config.ASSISTANT_TASK_WINDOW_HOURS:g} hours before the visit.",
        )
    return a


def save_review(
    db: Session, row: models.AppointmentAssignment, body: schemas.TaskReviewIn, actor: models.Account
) -> None:
    _check_workable(db, row)
    assistant_review.prepare(db, row)
    assistant_review.apply(db, row, body)


def complete(
    db: Session,
    row: models.AppointmentAssignment,
    body: schemas.TaskCompletion,
    actor: models.Account,
    worker: models.Worker | None = None,
) -> None:
    """Done — properly: the task was taken, the visit is on (or was completed by the office), its time
    has come, and every duty is done (assistant_review.progress — ``422`` lists what is missing). The
    medication and allergy decisions update the patient's chart."""
    a = _check_workable(db, row)
    assistant_review.prepare(db, row)  # a check-in that arrived meanwhile is on the lists
    if body.review is not None:
        assistant_review.apply(db, row, body.review)
    done, problems = assistant_review.progress(db, row)
    if problems:
        raise HTTPException(status_code=422, detail="Not everything is done yet: " + " ".join(problems))
    assistant_review.apply_to_chart(db, row, worker)
    row.status, row.completed_at = "completed", _now()
    row.duties = json.dumps(done)
    row.completion_note = body.note
    accounts.audit(db, actor, "assignment.complete", None, appointment=a.number, task=row.id)
    notify_provider(db, row, a)


def chart_link(a: models.Appointment) -> str | None:
    """The patient's chart for this visit on the staff dashboard (STAFF_URL), for the provider."""
    staff = (config.STAFF_URL or "").strip()
    if not staff:
        return None
    base = appointments.app_base(None).rstrip("/") + staff if staff.startswith("/") else staff
    return f"{base.rstrip('/')}/#/patient-chart/{a.id}"


def notify_provider(db: Session, row: models.AppointmentAssignment, a: models.Appointment) -> None:
    """The assistant finished: tell the visit's provider (e-mail / text to the staff record) that the
    patient is ready, with the link to the chart. Deliberately no medical details in the message — the
    vital signs, the reviewed lists and the notes are in the chart. Never fails the completion: the
    outcome (or why nobody could be told) is kept on the task."""
    provider = db.get(models.Worker, a.provider_id)
    patient = db.get(models.Patient, a.patient_id)
    assistant = db.get(models.Worker, row.worker_id)
    if provider is None:
        row.provider_notify_note = "No provider on the appointment — nobody to notify."
        return
    local = appointments.as_utc(a.start_at).astimezone(appointments.office_tz())
    who = f"{patient.first_name} {patient.last_name}"
    subject = f"Ready for you: {who} ({patient.mrn}) — {a.number} at {local:%H:%M}"
    link = chart_link(a)
    lines = [
        f"Hi {provider.first_name},",
        "",
        f"{who} ({patient.mrn}) is ready for you: {_name(assistant)} has finished preparing the visit — the",
        "patient is roomed, the vital signs are taken and the medications, allergies and check-in answers",
        "were reviewed with the patient.",
        "",
        *_visit_lines(db, a),
        "",
        "The patient's chart — previous visits, medical history, medications, allergies, lab and imaging",
        "results, referral notes, the check-in and the assistant's work — is on the staff dashboard"
        + (f":\n  {link}" if link else " (My appointments)."),
        "",
        "(No medical details are included in this message.)",
    ]
    sms = (
        f"{config.OFFICE_NAME}: {who} ({patient.mrn}) is ready for {a.number} at {local:%H:%M} — "
        "chart on the staff dashboard."
    )
    sent, to, notes = appointments.deliver(
        provider.email, provider.phone, subject, "\n".join(lines), sms, whose="the staff record"
    )
    row.provider_notify_channels = ",".join(sent)
    row.provider_notified_to = ", ".join(to)
    row.provider_notify_note = ("; ".join(notes))[:500] or None
    row.provider_notified_at = _now() if sent else None
    log.info(
        "%s: provider %s told the patient is ready by %s", a.number, provider.staff_number, ",".join(sent) or "nothing"
    )


def report(db: Session, row: models.AppointmentAssignment, body: schemas.TaskReport, actor: models.Account) -> None:
    """The assistant cannot do the task: it goes back to the office, which assigns someone else."""
    if row.status not in OPEN:
        raise HTTPException(status_code=409, detail=f"This task is {row.status} — there is nothing to report.")
    a = db.get(models.Appointment, row.appointment_id)
    if not _visit_is_on(a, _now()):
        raise HTTPException(status_code=409, detail=f"The visit is {'over' if a.status == 'scheduled' else a.status}.")
    row.status, row.reported_at = "reported", _now()
    row.report_reason, row.report_note = body.reason, body.note
    _notify_office(db, row, a)
    accounts.audit(db, actor, "assignment.report", None, appointment=a.number, task=row.id, reason=body.reason)


def task_out(db: Session, row: models.AppointmentAssignment, now: datetime | None = None) -> schemas.AssistantTaskOut:
    now = now or _now()
    a = db.get(models.Appointment, row.appointment_id)
    provider = db.get(models.Worker, a.provider_id)
    patient = db.get(models.Patient, a.patient_id)
    base = out(db, row)
    on = _visit_is_on(a, now)
    opens = complete_opens_at(a)
    return schemas.AssistantTaskOut(
        id=row.id,
        status=row.status,
        instructions=row.instructions,
        assigned_at=row.assigned_at,
        accepted_at=row.accepted_at,
        completed_at=row.completed_at,
        duties=base.duties,
        completion_note=row.completion_note,
        reported_at=row.reported_at,
        report_reason=row.report_reason,
        report_reason_label=base.report_reason_label,
        report_note=row.report_note,
        canceled_at=row.canceled_at,
        cancel_reason=row.cancel_reason,
        appointment=schemas.TaskAppointment(
            id=a.id,
            number=a.number,
            status=a.status,
            start_at=appointments.as_utc(a.start_at),
            end_at=appointments.as_utc(a.end_at),
            start_local=appointments.format_local(a.start_at),
            timezone=str(appointments.office_tz()),
            reason=a.reason,
            provider_name=appointments._provider_name(provider),
            provider_role=provider.role,
            patient=patients.summary(patient),
            confirmed_at=a.confirmed_at,
            checked_in_at=a.checked_in_at,
            check_in=check_ins.latest_summary(db, a),
        ),
        can_accept=row.status == "assigned" and on,
        can_complete=row.status == "accepted" and a.status in ("scheduled", "completed") and now >= opens,
        can_report=row.status in OPEN and on,
        complete_opens_at=opens,
        has_check_in_answers=_completed_check_in(db, a) is not None,
        vitals=base.vitals,
    )


def _completed_check_in(db: Session, a: models.Appointment) -> models.AppointmentCheckIn | None:
    return next((c for c in check_ins.history(db, a) if c.status == "completed"), None)


# ---------------------------------------------------------------------------- routes: the office


def _appointment(db: Session, appointment_id: int) -> models.Appointment:
    a = db.get(models.Appointment, appointment_id)
    if a is None:
        raise HTTPException(status_code=404, detail="Appointment not found.")
    return a


@admin_router.get("/appointments/{appointment_id}/assistants", response_model=schemas.AssistantOptions)
def assistant_options(
    appointment_id: int, _p: auth.Principal = Depends(auth.admin_principal), db: Session = Depends(get_db)
):
    """The medical assistants who could prepare this visit — available ones first, the others with
    the reason (not in that day, outside their hours, with another patient then, …)."""
    return options(db, _appointment(db, appointment_id))


@admin_router.get("/appointments/{appointment_id}/assignments", response_model=list[schemas.AssignmentOut])
def appointment_assignments(
    appointment_id: int, _p: auth.Principal = Depends(auth.admin_principal), db: Session = Depends(get_db)
):
    """Every task of the visit, newest first: who was assigned, taken, completed, reported, withdrawn."""
    return [out(db, r) for r in history(db, _appointment(db, appointment_id))]


@admin_router.put("/appointments/{appointment_id}/assistant", response_model=schemas.AppointmentOut)
def assign_assistant(
    appointment_id: int,
    body: schemas.AssignmentRequest,
    p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    """Assign — or reassign — a medical assistant who is available for the visit (``409`` with the
    reason otherwise). The assistant is told by e-mail / text; a replaced one too."""
    a = _appointment(db, appointment_id)
    w = db.get(models.Worker, body.worker_id)
    if not is_assistant(w):
        raise HTTPException(status_code=404, detail="No active medical assistant with this id.")
    assign(db, a, w, actor=p.account, instructions=body.instructions)
    db.commit()
    return appointments.appointment_out(db, a, office=True)


@admin_router.delete("/appointments/{appointment_id}/assistant", response_model=schemas.AppointmentOut)
def unassign_assistant(
    appointment_id: int, p: auth.Principal = Depends(auth.admin_principal), db: Session = Depends(get_db)
):
    """Take the assistant off the visit."""
    a = _appointment(db, appointment_id)
    unassign(db, a, actor=p.account)
    db.commit()
    return appointments.appointment_out(db, a, office=True)


def _office_range(date_from: date | None, date_to: date | None):
    tz = appointments.office_tz()
    out = []
    if date_from:
        out.append(models.Appointment.start_at >= datetime.combine(date_from, datetime.min.time(), tz))
    if date_to:
        out.append(models.Appointment.start_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time(), tz))
    return out


NEEDS_REASSIGNMENT = "needs_reassignment"


@admin_router.get("/assignments", response_model=schemas.AssignmentPage)
def list_assignments(
    status: str | None = Query(
        default=None,
        pattern="^(open|assigned|accepted|completed|reported|canceled|needs_reassignment)$",
        description="open = assigned or accepted · needs_reassignment = reported, nobody assigned instead yet",
    ),
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    worker_id: int | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    _p: auth.Principal = Depends(auth.admin_principal),
    db: Session = Depends(get_db),
):
    """The office's task board, in visit order; the counts are office-wide, upcoming visits only."""
    AA = models.AppointmentAssignment
    stmt = select(AA).join(models.Appointment, models.Appointment.id == AA.appointment_id)
    if status == "open":
        stmt = stmt.where(AA.status.in_(OPEN))
    elif status == NEEDS_REASSIGNMENT:
        stmt = stmt.where(AA.status == "reported", AA.resolved_at.is_(None))
    elif status:
        stmt = stmt.where(AA.status == status)
    if worker_id:
        stmt = stmt.where(AA.worker_id == worker_id)
    for cond in _office_range(date_from, date_to):
        stmt = stmt.where(cond)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(stmt.order_by(models.Appointment.start_at, AA.id).offset(offset).limit(limit)).scalars().all()
    upcoming = (
        select(func.count())
        .select_from(AA)
        .join(models.Appointment, models.Appointment.id == AA.appointment_id)
        .where(models.Appointment.end_at >= _now(), models.Appointment.status == "scheduled")
    )
    return schemas.AssignmentPage(
        items=[out(db, r) for r in rows],
        total=total,
        open=db.execute(upcoming.where(AA.status.in_(OPEN))).scalar_one(),
        needs_reassignment=db.execute(upcoming.where(AA.status == "reported", AA.resolved_at.is_(None))).scalar_one(),
    )


def agenda_filter(value: str):
    """The agenda's ``assistant=`` filter as a condition on models.Appointment: none (nobody on the
    visit) · open (assigned / accepted) · reported (needs reassignment) · completed."""
    AA = models.AppointmentAssignment
    mine = AA.appointment_id == models.Appointment.id
    open_ = exists(select(AA.id).where(mine, AA.status.in_(OPEN)))
    reported = exists(select(AA.id).where(mine, AA.status == "reported", AA.resolved_at.is_(None)))
    done = exists(select(AA.id).where(mine, AA.status == "completed"))
    if value == "none":
        return ~or_(open_, reported, done)
    if value == "open":
        return open_
    if value == "reported":
        return reported
    if value == "completed":
        return done
    raise HTTPException(status_code=422, detail="assistant: none · open · reported · completed")


# ---------------------------------------------------------------------------- routes: the assistant


def assistant_principal(
    s: staff_accounts.StaffPrincipal = Depends(staff_accounts.staff_principal),
) -> staff_accounts.StaffPrincipal:
    """A signed-in member of staff whose roster record is a medical assistant."""
    if s.worker.role != models.ROLE_MEDICAL_ASSISTANT:
        raise HTTPException(status_code=403, detail="My tasks is for medical assistants.")
    return s


def _own_task(db: Session, s: staff_accounts.StaffPrincipal, task_id: int) -> models.AppointmentAssignment:
    row = db.get(models.AppointmentAssignment, task_id)
    if row is None or row.worker_id != s.worker.id:
        raise HTTPException(status_code=404, detail="Task not found.")
    return row


@staff_router.get("", response_model=schemas.AssistantTaskPage)
def my_tasks(
    date_from: date | None = Query(default=None, alias="from", description="First day (office time zone)"),
    date_to: date | None = Query(default=None, alias="to", description="Last day, inclusive"),
    status: str | None = Query(default=None, pattern="^(open|assigned|accepted|completed|reported|canceled)$"),
    s: staff_accounts.StaffPrincipal = Depends(assistant_principal),
    db: Session = Depends(get_db),
):
    """The signed-in assistant's tasks by visit time — today and the next two weeks unless a period
    is given (``open`` = assigned or accepted)."""
    tz = appointments.office_tz()
    today = _now().astimezone(tz).date()
    start = date_from or today
    end = date_to or (start + timedelta(days=DEFAULT_DAYS - 1))
    if end < start:
        raise HTTPException(status_code=422, detail="to: must not be before from")
    if (end - start).days > 366:
        raise HTTPException(status_code=422, detail="Please ask for at most one year at a time.")
    AA = models.AppointmentAssignment
    stmt = (
        select(AA)
        .join(models.Appointment, models.Appointment.id == AA.appointment_id)
        .where(AA.worker_id == s.worker.id, *_office_range(start, end))
    )
    if status == "open":
        stmt = stmt.where(AA.status.in_(OPEN))
    elif status:
        stmt = stmt.where(AA.status == status)
    rows = db.execute(stmt.order_by(models.Appointment.start_at, AA.id)).scalars().all()
    now = _now()
    items = [task_out(db, r, now) for r in rows]
    db.commit()  # a check-in that just timed out is stored as failed
    return schemas.AssistantTaskPage(
        worker=workers.summary(s.worker),
        timezone=str(tz),
        date_from=start,
        date_to=end,
        items=items,
        total=len(items),
        duties=[schemas.KeyLabel(key=k, label=v) for k, v in models.ASSISTANT_DUTIES],
        report_reasons=[schemas.KeyLabel(key=k, label=v) for k, v in models.ASSIGNMENT_REPORT_REASONS],
    )


@staff_router.post("/{task_id}/accept", response_model=schemas.AssistantTaskOut)
def accept_task(
    task_id: int, s: staff_accounts.StaffPrincipal = Depends(assistant_principal), db: Session = Depends(get_db)
):
    """Take the task."""
    row = _own_task(db, s, task_id)
    accept(db, row, s.principal.account)
    db.commit()
    return task_out(db, row)


@staff_router.post("/{task_id}/complete", response_model=schemas.AssistantTaskOut)
def complete_task(
    task_id: int,
    body: schemas.TaskCompletion,
    s: staff_accounts.StaffPrincipal = Depends(assistant_principal),
    db: Session = Depends(get_db),
):
    """Complete the task with the review (saved first): every duty must be done (``422`` says what is
    missing); the medication and allergy decisions update the patient's chart."""
    row = _own_task(db, s, task_id)
    complete(db, row, body, s.principal.account, s.worker)
    db.commit()
    log.info("task #%d completed by %s", row.id, s.worker.staff_number)
    return task_out(db, row)


def _review_out(db: Session, row: models.AppointmentAssignment) -> schemas.TaskReviewOut:
    a = db.get(models.Appointment, row.appointment_id)
    opens = complete_opens_at(a)
    editable = row.status == "accepted" and a.status in ("scheduled", "completed") and _now() >= opens
    return assistant_review.review_out(db, row, editable=editable, opens_at=opens)


@staff_router.get("/{task_id}/review", response_model=schemas.TaskReviewOut)
def get_review(
    task_id: int, s: staff_accounts.StaffPrincipal = Depends(assistant_principal), db: Session = Depends(get_db)
):
    """The form of the visit: the vital signs, the medication and allergy lists prepopulated from the
    chart and the patient's check-in answers, the answers themselves — and what is still missing."""
    row = _own_task(db, s, task_id)
    if row.status in ("assigned", "canceled", "reported"):
        raise HTTPException(
            status_code=409, detail="Take the task first." if row.status == "assigned" else "This task is not yours."
        )
    assistant_review.prepare(db, row)
    db.commit()
    return _review_out(db, row)


@staff_router.put("/{task_id}/review", response_model=schemas.TaskReviewOut)
def put_review(
    task_id: int,
    body: schemas.TaskReviewIn,
    s: staff_accounts.StaffPrincipal = Depends(assistant_principal),
    db: Session = Depends(get_db),
):
    """Save the review (a draft): measurements, ticks, each medication / allergy cleared with its note,
    items added. Completeness is checked when the task is completed."""
    row = _own_task(db, s, task_id)
    save_review(db, row, body, s.principal.account)
    db.commit()
    return _review_out(db, row)


@staff_router.post("/{task_id}/report", response_model=schemas.AssistantTaskOut)
def report_task(
    task_id: int,
    body: schemas.TaskReport,
    s: staff_accounts.StaffPrincipal = Depends(assistant_principal),
    db: Session = Depends(get_db),
):
    """ "I cannot do this one" — the office sees it at once and assigns someone else."""
    row = _own_task(db, s, task_id)
    report(db, row, body, s.principal.account)
    db.commit()
    log.info("task #%d reported by %s (%s)", row.id, s.worker.staff_number, body.reason)
    return task_out(db, row)


@staff_router.get("/{task_id}/check-in", response_model=schemas.StaffCheckInAnswers)
def task_check_in(
    task_id: int, s: staff_accounts.StaffPrincipal = Depends(assistant_principal), db: Session = Depends(get_db)
):
    """The patient's check-in answers, to review them with the patient (a task that was not withdrawn)."""
    row = _own_task(db, s, task_id)
    if row.status in ("canceled", "reported"):
        raise HTTPException(status_code=404, detail="This task is no longer yours.")
    a = db.get(models.Appointment, row.appointment_id)
    done = _completed_check_in(db, a)
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
