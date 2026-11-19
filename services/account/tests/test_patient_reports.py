"""On request the office prepares the immunization record / the annual health examination report of a
patient, and a doctor signs it (patient_reports.py, immunizations.py)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from helpers import login


@pytest.fixture(scope="module")
def seeded(client):
    import seed
    from database import SessionLocal

    with SessionLocal() as db:
        seed.seed_all(db)
        pat = seed._patient(db, seed.PATIENTS[0])[0]
        riley = seed._patient(db, seed.PATIENTS[2])[0]
        return {
            "pat": pat.id,
            "riley": riley.id,
            "doctor": seed._worker(db, seed.email_for("doctor")).id,
            "nurse": seed._worker(db, seed.email_for("nurse")).id,
        }


@pytest.fixture(scope="module")
def doctor(client, seeded):
    return login(client, "doctor")


@pytest.fixture(scope="module")
def patient(client, seeded):
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


FINDINGS = {
    "height": "165 cm",
    "vision": "20/20 both eyes (with glasses)",
    "hearing": "Normal",
    "systems": {
        "general": {"status": "normal"},
        "cardiovascular": {"status": "normal"},
        "respiratory": {"status": "normal"},
        "musculoskeletal": {"status": "normal"},
    },
    "fitness": "fit",
    "recommendations": "Keep running three times a week.",
    "next_exam_due": (date.today() + timedelta(days=365)).isoformat(),
}


def test_the_office_prepares_the_patients_request_and_the_doctor_signs_it(
    client, seeded, doctor, patient, admin_headers
):
    mine = client.get("/api/v1/me/reports", headers=patient).json()
    exam = next(r for r in mine if r["kind"] == "annual_exam")
    signed = next(r for r in mine if r["kind"] == "immunization")
    assert exam["status"] == "requested" and exam["content"] is None
    assert signed["status"] == "signed" and signed["content"]["immunizations"]  # the seeded immunization record
    assert signed["esign_reference"] is None and signed["delivery_note"] is None  # the office's business
    # One of each kind at a time.
    again = client.post(
        "/api/v1/me/reports", headers=patient, json={"patient_id": seeded["pat"], "kind": "annual_exam"}
    )
    assert again.status_code == 409

    queue = client.get("/api/v1/patient-reports?status=requested", headers=admin_headers).json()
    assert exam["id"] in [r["id"] for r in queue["items"]] and queue["counts"]["requested"] >= 1
    opts = client.get(f"/api/v1/patients/{seeded['pat']}/reports/options", headers=admin_headers).json()
    assert opts["suggested_worker_id"] == seeded["doctor"]
    visit = next(v for v in opts["visits"] if v["id"] == opts["suggested_appointment_id"])
    assert visit["reason"].startswith("Annual physical")

    url = f"/api/v1/patient-reports/{exam['id']}"
    bad = client.post(f"{url}/generate", headers=admin_headers, json={"worker_id": seeded["nurse"]})
    assert bad.status_code == 422 and "doctor" in bad.json()["detail"]
    before = len(outbox("dummy-doctor@example.com"))
    res = client.post(
        f"{url}/generate",
        headers=admin_headers,
        json={"worker_id": seeded["doctor"], "appointment_id": opts["suggested_appointment_id"]},
    )
    assert res.status_code == 200, res.text
    draft = res.json()
    assert draft["status"] == "draft" and draft["doctor_name"] == "Dr. Alex Example"
    c = draft["content"]
    assert c["exam_visit_number"] == visit["number"] and c["blood_pressure"] == "132/84 mmHg"
    assert c["purpose_label"] == "Sports — Exampleville Running Club (dummy)"
    assert any(s["vaccine"].startswith("Influenza") for s in c["immunizations"])
    mails = outbox("dummy-doctor@example.com")
    assert len(mails) == before + 1 and mails[0]["subject"].startswith("To sign:")
    assert "Running Club" in mails[0]["body"] and "hypertension" not in mails[0]["body"].lower()
    # The patient only sees that it is in progress.
    mine = client.get(f"/api/v1/me/reports/{exam['id']}", headers=patient).json()
    assert mine["status_label"].startswith("In progress") and mine["content"] is None

    # The doctor's queue; nobody else sees it.
    todo = client.get("/api/v1/staff/me/reports", headers=doctor).json()
    assert exam["id"] in [r["id"] for r in todo]
    mine_url = f"/api/v1/staff/me/reports/{exam['id']}"
    assert client.get(mine_url, headers=login(client, "nurse")).status_code == 404
    r = client.get(mine_url, headers=doctor).json()
    assert r["can_sign"] is True and r["missing"]  # the examination and the decision are still to do
    sign = {"signature": signature(), "send_to_patient": True}
    res = client.post(f"{mine_url}/sign", headers=doctor, json=sign)
    assert res.status_code == 422 and "Before signing" in res.json()["detail"]

    incomplete = {**FINDINGS, "fitness": "fit_with_restrictions", "systems": {"skin": {"status": "abnormal"}}}
    r = client.put(mine_url, headers=doctor, json={"findings": incomplete}).json()
    assert any("restrictions" in m for m in r["missing"]) and any("Skin" in m for m in r["missing"])
    assert client.put(mine_url, headers=doctor, json={"findings": {"systems": {"liver": {}}}}).status_code == 422
    r = client.put(mine_url, headers=doctor, json={"findings": FINDINGS, "doctor_note": "Cleared for running."})
    assert r.status_code == 200 and r.json()["missing"] == []
    draft_pdf = client.get(f"{mine_url}/pdf", headers=doctor)
    assert draft_pdf.status_code == 200 and b"DRAFT" in draft_pdf.content

    before = len(outbox("dummy-patient@example.com"))
    res = client.post(f"{mine_url}/sign", headers=doctor, json=sign)
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["status"] == "signed" and out["verification"].startswith(out["number"] + "-")
    assert out["sent_channels"] == ["email", "sms"]
    mail = outbox("dummy-patient@example.com")[0]
    assert len(outbox("dummy-patient@example.com")) == before + 1
    assert "annual health examination report" in mail["subject"].lower()
    assert mail["attachments"][0]["filename"].endswith("-annual-exam-v1.pdf")
    assert client.put(mine_url, headers=doctor, json={"doctor_note": "x"}).status_code == 409

    # The patient reads it and downloads the PDF; the office prints / resends it.
    mine = client.get(f"/api/v1/me/reports/{exam['id']}", headers=patient).json()
    assert mine["findings"]["fitness"] == "fit" and mine["doctor_note"] == "Cleared for running."
    assert mine["patient_viewed_at"]
    pdf = client.get(f"/api/v1/me/reports/{exam['id']}/pdf", headers=patient)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF") and b"ANNUAL HEALTH EXAMINATION" in pdf.content
    assert b"DRAFT" not in pdf.content
    assert client.get(f"{url}/pdf", headers=admin_headers).status_code == 200
    assert client.post(f"{url}/send", headers=admin_headers).json()["sent_channels"] == ["email", "sms"]

    # An amendment supersedes it; the patient sees the new version only.
    amended = client.post(f"{mine_url}/amend", headers=doctor, json={"reason": "Vision corrected."})
    assert amended.status_code == 200, amended.text
    v2 = amended.json()
    assert v2["version"] == 2 and v2["status"] == "draft" and v2["findings"]["fitness"] == "fit"
    assert client.post(f"{mine_url}/amend", headers=doctor, json={"reason": "again"}).status_code == 409
    v2_url = f"/api/v1/staff/me/reports/{v2['id']}"
    client.put(v2_url, headers=doctor, json={"findings": {**FINDINGS, "vision": "20/25 left, 20/20 right"}})
    assert client.post(f"{v2_url}/sign", headers=doctor, json=sign).json()["status"] == "signed"
    assert client.get(url, headers=admin_headers).json()["status"] == "superseded"
    kinds = [(r["kind"], r["version"]) for r in client.get("/api/v1/me/reports", headers=patient).json()]
    assert ("annual_exam", 2) in kinds and ("annual_exam", 1) not in kinds
    assert client.get(f"/api/v1/me/reports/{exam['id']}", headers=patient).status_code == 404


def test_a_desk_request_the_doctor_returns_then_canceled(client, seeded, doctor, admin_headers):
    res = client.post(
        f"/api/v1/patients/{seeded['riley']}/reports",
        headers=admin_headers,
        json={
            "kind": "immunization",
            "purpose": "school",
            "purpose_detail": "Dummy College",
            "worker_id": seeded["doctor"],
        },
    )
    assert res.status_code == 201, res.text
    r = res.json()
    assert r["status"] == "draft" and r["requested_via"] == "office"
    assert len(r["content"]["immunizations"]) == 4
    url = f"/api/v1/staff/me/reports/{r['id']}"
    assert client.post(f"{url}/decline", headers=doctor, json={"reason": "x"}).status_code == 422
    back = client.post(f"{url}/decline", headers=doctor, json={"reason": "Hepatitis B is missing — check the card."})
    assert back.json()["status"] == "declined"
    office = client.get(f"/api/v1/patient-reports/{r['id']}", headers=admin_headers).json()
    assert office["decline_reason"].startswith("Hepatitis B") and office["status_label"] == "Returned by the doctor"
    # Prepared again, then canceled — Riley is told.
    again = client.post(
        f"/api/v1/patient-reports/{r['id']}/generate", headers=admin_headers, json={"worker_id": seeded["doctor"]}
    )
    assert again.json()["status"] == "draft" and again.json()["decline_reason"] is None
    before = len(outbox("dummy-riley@example.com"))
    canceled = client.post(
        f"/api/v1/patient-reports/{r['id']}/cancel", headers=admin_headers, json={"reason": "The college withdrew it."}
    )
    assert canceled.json()["status"] == "canceled"
    assert len(outbox("dummy-riley@example.com")) == before + 1
    assert client.post(f"{url}/sign", headers=doctor, json={"signature": signature()}).status_code == 409


def test_the_patient_withdraws_a_request(client, seeded, patient):
    res = client.post(
        "/api/v1/me/reports",
        headers=patient,
        json={"patient_id": seeded["pat"], "kind": "immunization", "purpose": "travel", "note": "Trip (dummy)"},
    )
    assert res.status_code == 201, res.text
    rid = res.json()["id"]
    assert res.json()["status"] == "requested"
    past = client.post(
        "/api/v1/me/reports",
        headers=patient,
        json={"patient_id": seeded["pat"], "kind": "immunization", "needed_by": "2000-01-01"},
    )
    assert past.status_code == 422
    other = client.post(
        "/api/v1/me/reports", headers=patient, json={"patient_id": seeded["riley"], "kind": "immunization"}
    )
    assert other.status_code == 404  # not a record of this account
    out = client.post(f"/api/v1/me/reports/{rid}/cancel", headers=patient)
    assert out.status_code == 200 and out.json()["status"] == "canceled"
    assert client.get(f"/api/v1/me/reports/{rid}/pdf", headers=patient).status_code == 404


def test_immunizations_in_the_record_and_on_the_chart(client, seeded, admin_headers, doctor):
    url = f"/api/v1/patients/{seeded['riley']}/immunizations"
    rows = client.get(url, headers=admin_headers).json()
    assert len(rows) == 4 and rows[0]["date_given"] >= rows[-1]["date_given"]
    future = (date.today() + timedelta(days=3)).isoformat()
    assert (
        client.post(url, headers=admin_headers, json={"vaccine": "Hepatitis A", "date_given": future}).status_code
        == 422
    )
    res = client.post(url, headers=admin_headers, json={"vaccine": "Hepatitis A", "date_given": "1990-01-01"})
    assert res.status_code == 422  # before Riley was born
    bad = {"vaccine": "Hepatitis A", "date_given": "2020-01-01", "dose_number": 3, "series_doses": 2}
    assert client.post(url, headers=admin_headers, json=bad).status_code == 422
    res = client.post(
        url,
        headers=admin_headers,
        json={
            "vaccine": "Hepatitis A",
            "date_given": "2020-01-01",
            "dose_number": 1,
            "series_doses": 2,
            "lot_number": "DUMMY-LOT-HEPA",
            "route": "IM",
            "worker_id": seeded["nurse"],
        },
    )
    assert res.status_code == 201, res.text
    row = res.json()
    assert row["dose_label"] == "1 of 2" and row["administered_by"] == "Sam Sample" and "DUMMY-LOT-HEPA" in row["line"]
    err = client.patch(f"{url}/{row['id']}", headers=admin_headers, json={"status": "entered_in_error"})
    assert err.json()["status"] == "entered_in_error"
    assert client.get(url, headers=admin_headers).json()[-1]["id"] == row["id"]  # kept, at the end
    draft = client.post(
        f"/api/v1/patients/{seeded['riley']}/reports",
        headers=admin_headers,
        json={"kind": "immunization", "worker_id": seeded["doctor"]},
    ).json()
    assert all(s["vaccine"] != "Hepatitis A" for s in draft["content"]["immunizations"])

    # The provider sees them on the chart.
    from helpers import visit

    import models
    from database import SessionLocal

    aid = visit(seeded["doctor"], 13, days_ahead=9, patient_id=seeded["riley"])
    chart = client.get(f"/api/v1/staff/me/appointments/{aid}/chart", headers=doctor).json()
    assert any(i["vaccine"].startswith("Varicella") for i in chart["immunizations"])
    with SessionLocal() as db:
        assert db.get(models.PatientImmunization, row["id"]).status == "entered_in_error"
