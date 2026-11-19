"""The medical assistant's review of a visit — the form behind "Complete…" on My tasks.

A task is done properly when every duty (models.ASSISTANT_DUTIES) is done:

    room_patient      ticked: the patient is in the room, identity confirmed
    vitals            typed in: blood pressure (systolic / diastolic), pulse, temperature, weight
                      (schemas.VitalsIn refuses implausible values — a typo or the wrong unit)
    medications       every item of the medication list cleared — or "takes no medications"
    allergies         every item of the allergy list cleared — or "no known allergies"
    check_in_review   ticked: the check-in answers were reviewed with the patient (no completed
                      check-in: a note saying how the questions were covered instead)
    provider_ready    ticked: the provider was told the patient is ready

**The medication and allergy lists are prepopulated** (``prepare``) from two sources:

    the chart        the patient's active entries (health_records.py)
    the check-in     what the patient wrote on the check-in page ("Medications you take now",
                     "Allergies"): split into one item per entry (new lines, ";" and ","), a
                     medication into name + dose ("Lisinopril 20 mg daily" → Lisinopril · 20 mg daily),
                     an allergy into substance + reaction ("Penicillin (rash)" → Penicillin · rash)

and matched by name. Each item carries a hint (``flag``) for the assistant: *match* (on the chart and
in the check-in), *changed* (the check-in says something else — the newer detail is prefilled, the
chart's is shown), *new* (only in the check-in), *not_mentioned* (on the chart but missing from the
patient's list), *said_none* (on the chart but the patient wrote "none"). A check-in completed after
the review started is merged in on the next look; an item it contradicts must be cleared again.

**Clearing an item**: *confirmed* (current — as shown; name and detail can be corrected) or *removed*
(not current / entered in error — for an entry of the chart a note saying why is required). Every item
has its own note. The assistant can add items the patient mentions.

**On completion** (``apply_to_chart``) the decisions update the chart: confirmed entries are kept
(corrected, marked reviewed by the assistant), removed ones become inactive with the note, confirmed
new ones are added (source ``check_in`` / ``assistant``). The review items stay with the task as the
record of what was reviewed.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

import check_ins
import health_records
import models
import patients
import schemas

KINDS = models.REVIEWED_KINDS
KIND_LABEL = {"medication": "Medications", "allergy": "Allergies"}
CHECKLIST_FLAGS = ("room_patient", "no_medications", "no_allergies", "check_in_reviewed", "provider_ready")
MAX_ADDED_PER_KIND = 50

# What counts as "none" in a free-text answer.
_NONE_ANSWERS = {
    "none",
    "no",
    "nil",
    "na",
    "n a",
    "nothing",
    "nka",
    "nkda",
    "no known allergies",
    "no known drug allergies",
    "no allergies",
    "none known",
    "no medications",
    "no medication",
    "no meds",
    "not any",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------- reading the patient's text


def says_none(text: str | None) -> bool:
    """ "None", "NKDA", "no known allergies", … (case and punctuation ignored)."""
    words = re.sub(r"[^a-z ]+", " ", (text or "").lower()).split()
    return bool(words) and " ".join(words) in _NONE_ANSWERS


def split_entries(text: str | None) -> list[str]:
    """One entry per line / ";" / "," — list bullets and numbering removed."""
    out = []
    for part in re.split(r"[\n;,]+", text or ""):
        entry = re.sub(r"^\s*(?:[-*•·]+|\d+[.)])\s*", "", part).strip(" .\t")
        if entry and not says_none(entry):
            out.append(entry[:300])
    return out


def _cap(name: str) -> str:
    return name[:1].upper() + name[1:] if name else name


def parse_medication(entry: str) -> tuple[str, str | None]:
    """ "Lisinopril 20 mg once daily" → ("Lisinopril", "20 mg once daily"); "Vitamin D (1000 IU)" →
    ("Vitamin D", "1000 IU"); "aspirin" → ("Aspirin", None)."""
    m = re.match(r"^(.*?)\s*\((.*)\)\s*$", entry)
    if m and m.group(1):
        return _cap(m.group(1).strip())[:200], m.group(2).strip()[:300] or None
    tokens = entry.split()
    first_number = next((i for i, t in enumerate(tokens) if any(ch.isdigit() for ch in t)), None)
    if not first_number:  # no number, or the entry starts with one: keep it whole
        return _cap(entry)[:200], None
    return _cap(" ".join(tokens[:first_number]))[:200], " ".join(tokens[first_number:])[:300]


def parse_allergy(entry: str) -> tuple[str, str | None]:
    """ "Penicillin (rash)" / "penicillin - rash" / "Penicillin: hives" → (substance, reaction)."""
    m = re.match(r"^(.*?)\s*\((.*)\)\s*$", entry) or re.match(r"^(.*?)\s*(?:\s[-–—]\s|:)\s*(.*)$", entry)
    if m and m.group(1).strip():
        return _cap(m.group(1).strip())[:200], (m.group(2).strip() or None) and m.group(2).strip()[:300]
    return _cap(entry.strip())[:200], None


PARSERS = {"medication": parse_medication, "allergy": parse_allergy}
CHECK_IN_QUESTION = {"medication": "medications", "allergy": "allergies"}


def _name_key(name: str | None) -> str:
    return patients.normalize_name(name or "")


def _detail_key(detail: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (detail or "").lower())


# ---------------------------------------------------------------------------- the task's state


def checklist(row: models.AppointmentAssignment) -> dict:
    try:
        data = json.loads(row.checklist or "{}")
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def _save_checklist(row: models.AppointmentAssignment, data: dict) -> None:
    row.checklist = json.dumps(data, sort_keys=True)


def vitals_of(row: models.AppointmentAssignment) -> dict | None:
    try:
        data = json.loads(row.vitals) if row.vitals else None
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _num(value: float | int) -> str:
    return f"{value:g}"


def vitals_out(data: dict | None) -> schemas.VitalsOut | None:
    """The stored vital signs with the one-line summary ("BP 120/80 mmHg · pulse 72/min · 98.6 °F · 154 lb")."""
    if not data:
        return None
    v = schemas.VitalsOut(**{k: data.get(k) for k in schemas.VitalsOut.model_fields if k != "text" and k in data})
    parts = []
    if v.bp_systolic is not None and v.bp_diastolic is not None:
        parts.append(f"BP {v.bp_systolic}/{v.bp_diastolic} mmHg")
    if v.pulse is not None:
        parts.append(f"pulse {v.pulse}/min")
    if v.temperature is not None:
        parts.append(f"{_num(v.temperature)} °{v.temperature_unit}")
    if v.weight is not None:
        parts.append(f"{_num(v.weight)} {v.weight_unit}")
    v.text = " · ".join(parts)
    return v if (parts or v.note) else None


def items_of(db: Session, row: models.AppointmentAssignment) -> list[models.AssignmentReviewItem]:
    return (
        db.execute(
            select(models.AssignmentReviewItem)
            .where(models.AssignmentReviewItem.assignment_id == row.id)
            .order_by(models.AssignmentReviewItem.position, models.AssignmentReviewItem.id)
        )
        .scalars()
        .all()
    )


def completed_check_in(db: Session, a: models.Appointment) -> models.AppointmentCheckIn | None:
    return next((c for c in check_ins.history(db, a) if c.status == "completed"), None)


def _answers(c: models.AppointmentCheckIn | None) -> dict:
    if c is None:
        return {}
    try:
        data = json.loads(c.answers or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------- prepopulating


def prepare(db: Session, row: models.AppointmentAssignment) -> None:
    """Bring the lists up to date (idempotent): every active chart entry is on it; a completed check-in
    not merged yet is merged (matched by name). Only while the task is open."""
    if row.status not in models.ASSIGNMENT_OPEN_STATUSES:
        return
    a = db.get(models.Appointment, row.appointment_id)
    patient = db.get(models.Patient, a.patient_id)
    items = items_of(db, row)
    position = max((i.position for i in items), default=0)
    on_list = {i.health_item_id for i in items if i.health_item_id}
    for entry in health_records.items_of(db, patient, active_only=True):
        if entry.id in on_list or entry.kind not in KINDS:  # the medical history is not part of the review
            continue
        position += 1
        item = models.AssignmentReviewItem(
            assignment_id=row.id,
            kind=entry.kind,
            health_item_id=entry.id,
            origin="record",
            name=entry.name,
            detail=entry.detail,
            record_detail=entry.detail,
            position=position,
        )
        db.add(item)
        items.append(item)
    c = completed_check_in(db, a)
    if c is not None and c.id != row.review_check_in_id:
        _merge_check_in(db, row, items, _answers(c), position)
        row.review_check_in_id = c.id
    db.flush()


def _merge_check_in(
    db: Session, row: models.AppointmentAssignment, items: list[models.AssignmentReviewItem], answers: dict, pos: int
) -> None:
    data = checklist(row)
    for kind in KINDS:
        text = answers.get(CHECK_IN_QUESTION[kind])
        text = text if isinstance(text, str) else ""
        said_none = says_none(text) or (
            kind == "medication" and not text.strip() and answers.get("medications_changed") == "yes"
        )
        data[f"patient_said_no_{'medications' if kind == 'medication' else 'allergies'}"] = said_none
        raw_entries = split_entries(text)
        mine = [i for i in items if i.kind == kind]
        mentioned: dict[int, tuple[str, str | None, str]] = {}  # id(item) → (name, detail, what was written)
        for raw in raw_entries:
            name, detail = PARSERS[kind](raw)
            match = next((i for i in mine if _name_key(i.name) == _name_key(name)), None)
            if match is None:
                pos += 1
                match = models.AssignmentReviewItem(
                    assignment_id=row.id,
                    kind=kind,
                    origin="check_in",
                    flag="new",
                    name=name,
                    detail=detail,
                    check_in_text=raw,
                    position=pos,
                )
                db.add(match)
                items.append(match)
                mine.append(match)
            mentioned[id(match)] = (name, detail, raw)
        for item in mine:
            said = mentioned.get(id(item))
            if said is not None:
                item.check_in_text = said[2]
            if not item.health_item_id:
                continue  # new from the check-in, or added by the assistant: nothing to compare with
            if said is not None:
                item.origin = "both"
                differs = bool(said[1]) and _detail_key(said[1]) != _detail_key(item.record_detail)
                flag = "changed" if differs else "match"
            elif said_none:
                flag = "said_none"
            elif raw_entries:
                flag = "not_mentioned"
            else:
                continue  # this check-in says nothing about this list
            if flag != item.flag:
                if flag == "changed":
                    item.detail = said[1]  # the patient's newer detail; the chart's stays in record_detail
                if flag in ("changed", "said_none", "not_mentioned"):
                    _reopen(item)
                item.flag = flag
    _save_checklist(row, data)


def _reopen(item: models.AssignmentReviewItem) -> None:
    """New information contradicts a cleared item: it has to be cleared again."""
    item.decision = None
    item.reviewed_at = None


# ---------------------------------------------------------------------------- output


def flag_text(item: models.AssignmentReviewItem) -> str | None:
    if item.flag == "changed":
        return f"The check-in says “{item.check_in_text}” — on record: {item.record_detail or 'no detail'}"
    if item.flag == "new":
        return f"New — from the check-in: “{item.check_in_text}”"
    if item.flag == "not_mentioned":
        return "On record, but not in the patient's check-in list — still current?"
    if item.flag == "said_none":
        return "On record, but the patient wrote “none” at check-in — please ask"
    if item.flag == "match":
        return "On record and in the patient's check-in answers"
    if item.origin == "record":
        return "On record"
    if item.origin == "assistant":
        return "Added during the review"
    return None


def item_out(item: models.AssignmentReviewItem) -> schemas.ReviewItemOut:
    return schemas.ReviewItemOut(
        id=item.id,
        kind=item.kind,
        name=item.name,
        detail=item.detail,
        origin=item.origin,
        flag=item.flag,
        flag_text=flag_text(item),
        record_detail=item.record_detail,
        check_in_text=item.check_in_text,
        decision=item.decision,
        note=item.note,
        reviewed_at=item.reviewed_at,
        health_item_id=item.health_item_id,
    )


def check_in_answers(db: Session, a: models.Appointment) -> schemas.StaffCheckInAnswers | None:
    c = completed_check_in(db, a)
    if c is None:
        return None
    return schemas.StaffCheckInAnswers(
        appointment_id=a.id,
        completed_at=c.completed_at,
        via=c.via,
        questions=check_ins.questionnaire().questions,
        answers=_answers(c),
        signer_name=c.answers_signer_name,
        signed_at=c.answers_signed_at,
    )


def progress(db: Session, row: models.AppointmentAssignment) -> tuple[list[str], list[str]]:
    """(the duty keys that are done, what is still missing — one message per problem)."""
    data = checklist(row)
    items = items_of(db, row)
    done: list[str] = []
    problems: list[str] = []
    if data.get("room_patient"):
        done.append("room_patient")
    else:
        problems.append("Room the patient and confirm their identity (name and date of birth).")
    v = vitals_of(row) or {}
    missing = [
        label
        for key, label in (
            ("bp_systolic", "blood pressure (systolic)"),
            ("bp_diastolic", "blood pressure (diastolic)"),
            ("pulse", "pulse"),
            ("temperature", "temperature"),
            ("weight", "weight"),
        )
        if v.get(key) is None
    ]
    if missing:
        problems.append("Vital signs: enter the " + ", ".join(missing) + ".")
    else:
        done.append("vitals")
    for kind, duty, none_flag, none_label in (
        ("medication", "medications", "no_medications", "the patient takes no medications"),
        ("allergy", "allergies", "no_allergies", "no known allergies"),
    ):
        mine = [i for i in items if i.kind == kind]
        open_ = [i.name for i in mine if i.decision is None]
        confirmed = [i.name for i in mine if i.decision == "confirmed"]
        label = KIND_LABEL[kind]
        before = len(problems)
        if open_:
            problems.append(f"{label}: clear every item — still open: {', '.join(open_)}.")
        if not open_ and not confirmed and not data.get(none_flag):
            problems.append(f"{label}: nothing is confirmed — confirm that {none_label}, or add what the patient says.")
        if confirmed and data.get(none_flag):
            problems.append(f"{label}: “{none_label}” is ticked, but {', '.join(confirmed)} is confirmed.")
        if len(problems) == before:
            done.append(duty)
    before = len(problems)
    if not data.get("check_in_reviewed"):
        problems.append("Check-in answers: review them with the patient and tick “reviewed”.")
    a = db.get(models.Appointment, row.appointment_id)
    if completed_check_in(db, a) is None and not data.get("check_in_note"):
        problems.append(
            "Check-in answers: the patient has not checked in — add a note on how the questions were covered."
        )
    if len(problems) == before:
        done.append("check_in_review")
    if data.get("provider_ready"):
        done.append("provider_ready")
    else:
        problems.append("Tell the provider the patient is ready, then tick it.")
    order = [k for k, _label in models.ASSISTANT_DUTIES]
    return sorted(done, key=order.index), problems


def review_out(
    db: Session, row: models.AppointmentAssignment, *, editable: bool, opens_at: datetime
) -> schemas.TaskReviewOut:
    a = db.get(models.Appointment, row.appointment_id)
    data = checklist(row)
    items = items_of(db, row)
    done, problems = progress(db, row)
    return schemas.TaskReviewOut(
        task_id=row.id,
        status=row.status,
        editable=editable,
        complete_opens_at=opens_at,
        room_patient=bool(data.get("room_patient")),
        vitals=vitals_out(vitals_of(row)),
        medications=[item_out(i) for i in items if i.kind == "medication"],
        allergies=[item_out(i) for i in items if i.kind == "allergy"],
        no_medications=bool(data.get("no_medications")),
        no_allergies=bool(data.get("no_allergies")),
        patient_said_no_medications=bool(data.get("patient_said_no_medications")),
        patient_said_no_allergies=bool(data.get("patient_said_no_allergies")),
        check_in=check_in_answers(db, a),
        check_in_reviewed=bool(data.get("check_in_reviewed")),
        check_in_note=data.get("check_in_note"),
        provider_ready=bool(data.get("provider_ready")),
        duties=[schemas.KeyLabel(key=k, label=v) for k, v in models.ASSISTANT_DUTIES],
        done=done,
        problems=problems,
    )


# ---------------------------------------------------------------------------- saving the assistant's input


def apply(db: Session, row: models.AppointmentAssignment, body: schemas.TaskReviewIn) -> None:
    """Save what the assistant typed / ticked / decided (a draft — completeness is checked at
    completion). ``422`` names the item when something is not acceptable. Caller commits."""
    now = _now()
    data = checklist(row)
    for flag in CHECKLIST_FLAGS:
        value = getattr(body, flag)
        if value is not None:
            data[flag] = value
    if "check_in_note" in body.model_fields_set:
        data["check_in_note"] = body.check_in_note
    _save_checklist(row, data)
    if body.vitals is not None:
        row.vitals = json.dumps(body.vitals.model_dump(), sort_keys=True)
    items = {i.id: i for i in items_of(db, row)}
    position = max((i.position for i in items.values()), default=0)
    for change in body.items:
        if change.id is not None:
            item = items.get(change.id)
            if item is None:
                raise HTTPException(status_code=422, detail=f"items: no item {change.id} on this review")
        else:
            if change.kind is None or not change.name:
                raise HTTPException(status_code=422, detail="items: a new item needs its kind and name")
            if sum(1 for i in items.values() if i.kind == change.kind) >= MAX_ADDED_PER_KIND:
                raise HTTPException(status_code=422, detail=f"items: too many {change.kind} items")
            position += 1
            item = models.AssignmentReviewItem(
                assignment_id=row.id, kind=change.kind, origin="assistant", name=change.name, position=position
            )
            db.add(item)
            db.flush()
            items[item.id] = item
        fields = change.model_fields_set
        if "name" in fields and change.name:
            item.name = change.name
        if "detail" in fields:
            item.detail = change.detail
        if "note" in fields:
            item.note = change.note
        if change.decision == "pending":
            item.decision, item.reviewed_at = None, None
        elif change.decision is not None:
            item.decision, item.reviewed_at = change.decision, now
        if item.decision == "removed" and item.health_item_id and not item.note:
            raise HTTPException(
                status_code=422,
                detail=f"{item.name}: please add a note saying why it is removed from the {item.kind} list",
            )
    db.flush()


def apply_to_chart(db: Session, row: models.AppointmentAssignment, worker: models.Worker | None) -> None:
    """The completed review updates the patient's medication and allergy lists. Caller commits."""
    now = _now()
    a = db.get(models.Appointment, row.appointment_id)
    patient = db.get(models.Patient, a.patient_id)
    for item in items_of(db, row):
        entry = db.get(models.PatientHealthItem, item.health_item_id) if item.health_item_id else None
        if item.decision == "confirmed":
            if entry is None:
                entry = health_records.add(
                    db,
                    patient,
                    item.kind,
                    item.name,
                    item.detail,
                    source="check_in" if item.origin == "check_in" else "assistant",
                )
                item.health_item_id = entry.id
            entry.name, entry.detail, entry.status = item.name, item.detail, "active"
            if item.note:
                entry.note = item.note
        elif item.decision == "removed" and entry is not None:
            entry.status = "inactive"
            entry.note = item.note
        else:
            continue
        entry.last_reviewed_at = now
        entry.last_reviewed_by = worker.id if worker else None
    db.flush()
