"""After the visit the patient gets a summary of it, signed by the doctor (visit_summaries.py)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from helpers import login, visit

URL = "/api/v1/staff/me/appointments/{}/visit-summary"


def past(provider_id: int, hour: int, days_ago: int, **kw) -> int:
    """A visit exactly ``days_ago`` days ago (office time zone = UTC in the tests) at ``hour``."""
    day = datetime.now(timezone.utc).date() - timedelta(days=days_ago)
    return visit(provider_id, hour, weekday=day.weekday(), days_ahead=-days_ago, **kw)


@pytest.fixture(scope="module")
def seeded(client):
    """The whole dummy data set: the doctor, the nurse, the assistants, Pat Dummy and her patient account."""
    import seed
    from database import SessionLocal

    with SessionLocal() as db:
        seed.seed_all(db)
        pat = seed._patient(db, seed.PATIENTS[0])[0]
        doctor = seed._worker(db, seed.email_for("doctor"))
        nurse = seed._worker(db, seed.email_for("nurse"))
        return {"pat": pat.id, "doctor": doctor.id, "nurse": nurse.id}


@pytest.fixture(scope="module")
def doctor_headers(client, seeded):
    return login(client, "doctor")


@pytest.fixture(scope="module")
def patient_headers(client, seeded):
    """The dummy patient account, signed in on the patients' app (audience "patient")."""
    import clientcrypto
    import seed

    auth_key, _ = clientcrypto.derive_keys(seed.password_for("patient"), seed.email_for("patient"))
    res = client.post(
        "/api/v1/auth/login", json={"email": seed.email_for("patient"), "auth_key": auth_key, "audience": "patient"}
    )
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


def signature(name: str = "Alex Example") -> dict:
    import seed

    return {"signer_name": name, "image_png": seed.signature_png(name), "consent": True}


def outbox(to: str) -> list[dict]:
    import messaging

    return messaging.OUTBOX.messages(to=to)


def test_prepared_on_the_chart_edited_and_signed_then_the_patient_gets_it(
    client, seeded, doctor_headers, patient_headers, admin_headers
):
    aid = past(seeded["doctor"], 10, 3, patient_id=seeded["pat"], reason="Sore throat (dummy)")
    # The patient sees nothing before the doctor signs.
    assert client.get(f"/api/v1/me/appointments/{aid}/visit-summary", headers=patient_headers).status_code == 404

    draft = client.get(URL.format(aid), headers=doctor_headers)
    assert draft.status_code == 200, draft.text
    body = draft.json()
    assert body["status"] == "draft" and body["version"] == 1 and body["generated_by"] == "provider"
    assert body["content"]["visit_number"].startswith("APT-")
    assert body["content"]["patient_name"] == "Pat Dummy"
    assert body["content"]["reason"] == "Sore throat (dummy)"
    assert "Penicillin — rash" in body["content"]["allergies"]  # the chart's allergy list
    assert body["can_sign"] is True and body["sign_blocker"] is None

    # The chart changes → the draft follows it (diagnosis + visit note).
    res = client.post(
        f"/api/v1/staff/me/appointments/{aid}/diagnoses",
        headers=doctor_headers,
        json={"description": "Acute pharyngitis", "code": "J02.9", "primary": True},
    )
    assert res.status_code == 201, res.text
    res = client.put(
        f"/api/v1/staff/me/appointments/{aid}/visit-note",
        headers=doctor_headers,
        json={"seen": True, "assessment": "Viral pharyngitis.", "plan": "Fluids, rest, lozenges."},
    )
    assert res.status_code == 200, res.text
    body = client.get(URL.format(aid), headers=doctor_headers).json()
    assert body["content"]["diagnoses"] == ["Acute pharyngitis (J02.9) — primary"]
    assert body["assessment"] == "Viral pharyngitis." and body["assessment_edited"] is False

    # The doctor rewrites the plan for the patient and adds instructions / the follow-up.
    res = client.put(
        URL.format(aid),
        headers=doctor_headers,
        json={
            "plan": "Drink plenty, rest, throat lozenges as needed.",
            "instructions": "Come back if the fever lasts more than three days.",
            "follow_up": "No follow-up needed.",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["plan"] == "Drink plenty, rest, throat lozenges as needed." and body["plan_edited"] is True
    assert body["assessment"] == "Viral pharyngitis."  # still the visit note's

    # The draft PDF says DRAFT.
    pdf = client.get(URL.format(aid) + "/pdf", headers=doctor_headers)
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF") and b"DRAFT" in pdf.content

    before = len(outbox("dummy-patient@example.com"))
    signed = client.post(
        URL.format(aid) + "/sign", headers=doctor_headers, json={"signature": signature(), "send_to_patient": True}
    )
    assert signed.status_code == 200, signed.text
    s = signed.json()
    assert s["status"] == "signed" and s["signer_name"] == "Alex Example" and s["signed_at"]
    assert s["verification"].startswith(s["number"] + "-") and s["esign_reference"].startswith("FAKE-ESIGN-")
    assert s["sent_channels"] == ["email", "sms"] and s["sent_at"]

    # E-mailed with the PDF attached (the fake outbox) — and a text without medical details.
    mails = outbox("dummy-patient@example.com")
    assert len(mails) == before + 1
    mail = mails[0]
    assert "visit summary" in mail["subject"].lower()
    assert mail["attachments"][0]["content_type"] == "application/pdf"
    texts = [m for m in outbox("+15550100301") if "visit summary" in m["body"]]
    assert texts and "pharyngitis" not in texts[0]["body"].lower()

    # A signed summary is frozen.
    assert client.put(URL.format(aid), headers=doctor_headers, json={"instructions": "x"}).status_code == 409
    again = client.post(URL.format(aid) + "/sign", headers=doctor_headers, json={"signature": signature()})
    assert again.status_code == 409

    # The patient reads it in the app (and the PDF); the first opening is recorded.
    mine = client.get(f"/api/v1/me/appointments/{aid}/visit-summary", headers=patient_headers)
    assert mine.status_code == 200, mine.text
    m = mine.json()
    assert m["status"] == "signed" and m["plan"] == "Drink plenty, rest, throat lozenges as needed."
    assert m["esign_reference"] is None and m["delivery_note"] is None  # the office's business only
    pdf = client.get(f"/api/v1/me/appointments/{aid}/visit-summary/pdf", headers=patient_headers)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF") and b"DRAFT" not in pdf.content
    listing = client.get("/api/v1/me/appointments", headers=patient_headers).json()
    row = next(a for a in listing["past"] if a["id"] == aid)
    assert row["visit_summary"]["status"] == "signed" and row["visit_summary"]["patient_viewed_at"]

    # The office sees it on the agenda, prints it and can send it again.
    office = client.get(f"/api/v1/appointments/{aid}/visit-summary", headers=admin_headers)
    assert office.status_code == 200 and office.json()["status"] == "signed"
    assert client.get(f"/api/v1/appointments/{aid}", headers=admin_headers).json()["visit_summary"]["status"] == (
        "signed"
    )
    assert client.get(f"/api/v1/appointments/{aid}/visit-summary/pdf", headers=admin_headers).status_code == 200
    resent = client.post(f"/api/v1/appointments/{aid}/visit-summary/send", headers=admin_headers)
    assert resent.status_code == 200 and len(outbox("dummy-patient@example.com")) == before + 2


def test_amendment_supersedes_the_signed_version(client, seeded, doctor_headers, patient_headers):
    aid = past(seeded["doctor"], 11, 3, patient_id=seeded["pat"], reason="Rash (dummy)")
    client.get(URL.format(aid), headers=doctor_headers)
    client.put(URL.format(aid), headers=doctor_headers, json={"instructions": "Use the cream twice a day."})
    v1 = client.post(URL.format(aid) + "/sign", headers=doctor_headers, json={"signature": signature()}).json()
    assert client.post(URL.format(aid) + "/amend", headers=doctor_headers, json={"reason": ""}).status_code == 422

    draft = client.post(URL.format(aid) + "/amend", headers=doctor_headers, json={"reason": "Wrong cream named."})
    assert draft.status_code == 200, draft.text
    d = draft.json()
    assert d["status"] == "draft" and d["version"] == 2 and d["instructions"] == "Use the cream twice a day."
    assert d["amendment_reason"] == "Wrong cream named."
    assert [v["version"] for v in d["previous_versions"]] == [1]
    # Only one draft at a time.
    assert client.post(URL.format(aid) + "/amend", headers=doctor_headers, json={"reason": "again"}).status_code == 409
    # Until v2 is signed the patient keeps reading v1.
    assert client.get(f"/api/v1/me/appointments/{aid}/visit-summary", headers=patient_headers).json()["version"] == 1

    client.put(URL.format(aid), headers=doctor_headers, json={"instructions": "Use the ointment once a day."})
    v2 = client.post(URL.format(aid) + "/sign", headers=doctor_headers, json={"signature": signature()}).json()
    assert v2["version"] == 2 and v2["status"] == "signed" and v2["verification"] != v1["verification"]
    assert v2["previous_versions"][0]["status"] == "superseded"
    mine = client.get(f"/api/v1/me/appointments/{aid}/visit-summary", headers=patient_headers).json()
    assert mine["version"] == 2 and mine["instructions"] == "Use the ointment once a day."
    old_pdf = client.get(URL.format(aid) + "/pdf?version=1", headers=doctor_headers)
    assert old_pdf.status_code == 200 and b"SUPERSEDED" in old_pdf.content


def test_only_a_doctor_signs_and_only_after_the_visit(client, seeded, doctor_headers):
    # A visit next week: nothing to summarize yet.
    future = visit(seeded["doctor"], 14, days_ahead=8, patient_id=seeded["pat"])
    res = client.get(URL.format(future), headers=doctor_headers)
    assert res.status_code == 409 and "started" in res.json()["detail"]

    # A nurse's visit: the nurse can prepare the draft but not sign it.
    nurse = login(client, "nurse")
    aid = past(seeded["nurse"], 10, 2, patient_id=seeded["pat"])
    body = client.get(URL.format(aid), headers=nurse).json()
    assert body["status"] == "draft" and body["can_sign"] is False and "doctor" in body["sign_blocker"]
    res = client.post(URL.format(aid) + "/sign", headers=nurse, json={"signature": signature("Sam Sample")})
    assert res.status_code == 403

    # Somebody else's visit is not there at all; a medical assistant has no visits of their own.
    assert client.get(URL.format(aid), headers=doctor_headers).status_code == 404
    assistant = login(client, "assistant")
    assert client.get(URL.format(aid), headers=assistant).status_code == 404

    # The signature must be drawn and agreed to.
    mine = past(seeded["doctor"], 15, 2, patient_id=seeded["pat"])
    client.get(URL.format(mine), headers=doctor_headers)
    bad = {"signature": {**signature(), "consent": False}}
    assert client.post(URL.format(mine) + "/sign", headers=doctor_headers, json=bad).status_code == 422
    bad = {"signature": {**signature(), "image_png": "data:image/png;base64," + "A" * 200}}
    assert client.post(URL.format(mine) + "/sign", headers=doctor_headers, json=bad).status_code == 422


def test_prepared_automatically_when_the_office_completes_the_visit(client, seeded, admin_headers, doctor_headers):
    aid = past(seeded["doctor"], 9, 4, patient_id=seeded["pat"], reason="Check-up (dummy)")
    before = len(outbox("dummy-doctor@example.com"))
    res = client.patch(f"/api/v1/appointments/{aid}", headers=admin_headers, json={"status": "completed"})
    assert res.status_code == 200, res.text
    assert res.json()["visit_summary"]["status"] == "draft"
    office = client.get(f"/api/v1/appointments/{aid}/visit-summary", headers=admin_headers).json()
    assert office["generated_by"] == "office" and office["provider_notified_at"]
    mails = outbox("dummy-doctor@example.com")
    assert len(mails) == before + 1 and "to sign" in mails[0]["subject"]
    assert "Sore" not in mails[0]["body"]  # no medical details in the notification
    # Not sent to the patient before it is signed.
    assert client.post(f"/api/v1/appointments/{aid}/visit-summary/send", headers=admin_headers).status_code == 409
    # The doctor finds it waiting on the chart.
    assert client.get(URL.format(aid), headers=doctor_headers).json()["generated_by"] == "office"


def test_the_job_prepares_drafts_for_visits_the_provider_saw(client, seeded, admin_headers, doctor_headers):
    import visit_summaries
    from database import SessionLocal

    seen = past(seeded["doctor"], 16, 1, patient_id=seeded["pat"])
    unseen = past(seeded["doctor"], 16, 2, patient_id=seeded["pat"])
    client.put(f"/api/v1/staff/me/appointments/{seen}/visit-note", headers=doctor_headers, json={"seen": True})
    with SessionLocal() as db:
        prepared = {s.appointment_id for s in visit_summaries.prepare_due(db)}
        assert seen in prepared and unseen not in prepared
        assert visit_summaries.prepare_due(db) == []  # once
    # The office can run the job on demand.
    res = client.post("/api/v1/appointments/visit-summaries/run", headers=admin_headers)
    assert res.status_code == 200 and res.json()["prepared"] == 0


def test_the_seed_has_a_signed_summary_for_the_past_visit(client, seeded, patient_headers):
    listing = client.get("/api/v1/me/appointments", headers=patient_headers).json()
    past = next(a for a in listing["past"] if a["reason"] == "Blood pressure check (dummy past visit).")
    assert past["visit_summary"]["status"] == "signed" and past["visit_summary"]["signer_name"] == "Alex Example"
    s = client.get(f"/api/v1/me/appointments/{past['id']}/visit-summary", headers=patient_headers).json()
    assert s["content"]["vitals"].startswith("BP 146/92")
    assert any("Lisinopril" in line for line in s["content"]["prescriptions"])
    assert s["signature_png"].startswith("data:image/png;base64,")
