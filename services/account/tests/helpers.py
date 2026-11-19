"""Shared helpers of the API tests (visits made directly in the database, signing in, periods)."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, time, timedelta, timezone


def login(client, kind: str) -> dict:
    import clientcrypto
    import seed

    auth_key, _ = clientcrypto.derive_keys(seed.password_for(kind), seed.email_for(kind))
    res = client.post("/api/v1/auth/login", json={"email": seed.email_for(kind), "auth_key": auth_key})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


def period(days: int = 60) -> str:
    today = datetime.now(timezone.utc).date()
    return f"from={today.isoformat()}&to={(today + timedelta(days=days)).isoformat()}"


def new_patient(db, last_name: str):
    import patients
    import schemas

    return patients.create(
        db, schemas.PatientIn(first_name="Chart", last_name=last_name, date_of_birth="1975-05-05", consent=True)
    )


def visit(
    provider_id: int,
    hour: int,
    *,
    weekday: int = 4,
    days_ahead: int = 8,
    patient_id: int | None = None,
    status: str = "scheduled",
    reason: str = "Dummy visit",
) -> int:
    """An appointment made directly in the database: the next ``weekday`` at least ``days_ahead`` days
    away (negative = in the past) at ``hour`` office time, for a fresh dummy patient unless one is given."""
    import appointments
    import models
    from database import SessionLocal

    tz = appointments.office_tz()
    day = datetime.now(timezone.utc).astimezone(tz).date() + timedelta(days=days_ahead)
    while day.weekday() != weekday:
        day += timedelta(days=1)
    start = datetime.combine(day, time(hour, 0), tz).astimezone(timezone.utc)
    with SessionLocal() as db:
        if patient_id is None:
            patient_id = new_patient(db, f"Patient{secrets.token_hex(3)}").id
        a = models.Appointment(
            patient_id=patient_id,
            provider_id=provider_id,
            start_at=start,
            end_at=start + timedelta(minutes=30),
            status=status,
            reason=reason,
        )
        db.add(a)
        db.commit()
        return a.id


def patient_of(aid: int) -> int:
    import models
    from database import SessionLocal

    with SessionLocal() as db:
        return db.get(models.Appointment, aid).patient_id


def checked_in(aid: int, answers: dict) -> None:
    """A completed check-in of the visit with these answers (made directly)."""
    import appointments
    import models
    from database import SessionLocal

    with SessionLocal() as db:
        now = appointments._now()
        db.add(
            models.AppointmentCheckIn(
                appointment_id=aid,
                status="completed",
                via="account",
                token_hash=secrets.token_hex(32),
                link="http://localhost/check-in/dummy",
                expires_at=now,
                answers=json.dumps(answers),
                answers_signer_name="Dummy Signer",
                answers_signed_at=now,
                completed_at=now,
                created_at=now,
            )
        )
        a = db.get(models.Appointment, aid)
        a.checked_in_at, a.checked_in_via = now, "account"
        db.commit()


VITALS = {"bp_systolic": 128, "bp_diastolic": 82, "pulse": 72, "temperature": 98.6, "weight": 154}
