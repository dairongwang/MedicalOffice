"""The dummy data (seed.py) and the public flows of the staff dashboard that use it."""

from __future__ import annotations

import re
from datetime import date

import pytest
from helpers import login


@pytest.fixture(scope="module")
def everything(client):
    import seed
    from database import SessionLocal

    with SessionLocal() as db:
        seed.seed_all(db)
        seed.seed_all(db)  # idempotent: a second run adds nothing
    return seed


def test_everything_is_dummy_and_created_once(client, everything):
    import models
    from database import SessionLocal

    with SessionLocal() as db:
        emails = [a.email for a in db.query(models.Account).all() if a.email.endswith("@example.com")]
        seeded = [everything.email_for(k) for k in everything.KINDS + everything.STAFF_KINDS]
        assert set(seeded) <= set(emails)
        assert all("dummy" in e for e in seeded)
        assert all("Dummy" in everything.password_for(k) for k in everything.KINDS)
        assert db.query(models.Worker).filter(models.Worker.email_norm.like("dummy-%")).count() == 5
        assert db.query(models.AdCampaign).count() == len(everything.CAMPAIGNS)
        pat = db.query(models.Patient).filter_by(email_norm=everything.email_for("patient")).one()
        cards = db.query(models.PatientPaymentMethod).filter_by(account_id=pat.account_id, kind="card").all()
        assert len(cards) == 1 and cards[0].card_last4 == "4242" and cards[0].status == "active"
        for w in db.query(models.Worker).filter(models.Worker.phone.is_not(None)):
            assert re.search(r"555 010 0\d{3}$", w.phone), w.phone  # the range reserved for fiction
        reasons = {
            a.reason
            for a in db.query(models.Appointment).filter_by(patient_id=pat.id, status="scheduled")
            if a.end_at > models.utcnow()
        }
        assert {everything.CHECKUP_REASON, everything.FOLLOW_UP_REASON} <= reasons


def test_the_dummy_accounts_sign_in(client, everything):
    for kind in ("admin", "doctor", "nurse", "assistant", "assistant2", "pro"):
        assert login(client, kind)["Authorization"].startswith("Bearer ")
    me = client.get("/api/v1/me", headers=login(client, "admin")).json()
    assert me["role"] == "admin"


def test_the_two_factor_account_needs_a_code(client, everything):
    import clientcrypto
    import totp

    auth_key, _ = clientcrypto.derive_keys(everything.password_for("twofa"), everything.email_for("twofa"))
    res = client.post("/api/v1/auth/login", json={"email": everything.email_for("twofa"), "auth_key": auth_key})
    assert res.status_code == 202 and res.json()["two_factor_required"]
    challenge = res.json()["challenge"]
    code = totp.code_at(everything.TWOFA_SECRET)
    res = client.post("/api/v1/auth/login/2fa", json={"challenge": challenge, "code": code})
    assert res.status_code == 200, res.text


def test_a_member_of_staff_creates_their_account_with_the_code_from_the_inbox(client, everything):
    """What the staff dashboard's "Create staff account" page does (the front-desk record has no account)."""
    import clientcrypto
    import messaging

    email = everything.email_for("frontdesk")
    sent = client.post("/api/v1/staff/account/verify", json={"staff_number": "W-000003", "email": email})
    assert sent.status_code == 200, sent.text
    assert sent.json()["fake"] is True and sent.json()["outbox_url"] == "/api/v1/messages/fake/outbox"
    code = re.search(r"\b(\d{6})\b", messaging.OUTBOX.latest(to=email)["body"]).group(1)
    auth_key, _ = clientcrypto.derive_keys("Dummy-Frontdesk-pass-123", email)
    res = client.post(
        "/api/v1/staff/account",
        json={
            "enrollment": sent.json()["enrollment"],
            "code": code,
            "auth_key": auth_key,
            "wrapped_data_key": "dummy-wrapped-data-key",
            "device": {"platform": "staff-dashboard"},
        },
    )
    assert res.status_code == 201, res.text
    staff = client.get("/api/v1/staff/me", headers={"Authorization": f"Bearer {res.json()['token']}"}).json()
    assert staff["worker"]["staff_number"] == "W-000003" and staff["worker"]["role"] == "receptionist"
    # A wrong staff number / e-mail pair gives nothing away.
    bad = client.post("/api/v1/staff/account/verify", json={"staff_number": "W-000001", "email": email})
    assert bad.status_code == 404


def test_availability_by_staff_number_and_work_email(client, everything):
    creds = {"staff_number": "W-000005", "email": everything.email_for("assistant2")}
    week = client.post("/api/v1/workers/availability/lookup", json=creds)
    assert week.status_code == 200, week.text
    days = week.json()["availability"]
    assert [d["available"] for d in days] == [True, True, True, True, False, False, False]  # Mon–Thu afternoons
    assert days[0]["start_time"].startswith("13:00")
    days[4] = {**days[4], "available": True}
    saved = client.put("/api/v1/workers/availability", json={**creds, "availability": days})
    assert saved.status_code == 200 and saved.json()["availability"][4]["available"] is True
    signed_in = client.get("/api/v1/staff/me/availability", headers=login(client, "assistant2"))
    assert signed_in.status_code == 200 and signed_in.json()["availability"][4]["available"] is True


def test_apply_and_check_the_status(client, everything):
    body = {
        "first_name": "Dummy",
        "last_name": "Candidate",
        "email": "dummy-candidate@example.com",
        "date_of_birth": "1992-02-02",
        "role": "nurse",
        "license_number": "DUMMY-RN-9999",
        "license_expires": date(date.today().year + 2, 1, 1).isoformat(),
        "background_consent": True,
        "consent": True,
    }
    receipt = client.post("/api/v1/applications", json=body)
    assert receipt.status_code == 201, receipt.text
    r = receipt.json()
    assert r["age_check"] == "passed" and r["license_check"] == "in_progress" and r["number"].startswith("A-")
    status = client.post("/api/v1/applications/status", json={"number": r["number"], "email": body["email"]})
    assert status.status_code == 200 and status.json()["status"] == "screening"


def test_the_pharmacy_directory(client, everything, admin_headers):
    rows = client.get("/api/v1/pharmacies", headers=admin_headers).json()
    assert {p["source"] for p in rows} == {"seed"} and any(p["email"] is None for p in rows)
    new = client.post(
        "/api/v1/pharmacies",
        headers=admin_headers,
        json={"name": "Dummy Test Pharmacy", "email": "dummy-test-pharmacy@example.com"},
    )
    assert new.status_code == 201
    off = client.patch(f"/api/v1/pharmacies/{new.json()['id']}", headers=admin_headers, json={"active": False})
    assert off.json()["active"] is False
    active = [p["name"] for p in client.get("/api/v1/pharmacies", headers=admin_headers).json()]
    everyone = client.get("/api/v1/pharmacies?include_inactive=true", headers=admin_headers).json()
    assert "Dummy Test Pharmacy" not in active and "Dummy Test Pharmacy" in [p["name"] for p in everyone]
