"""My medicines — the patient dashboard's tab per patient record: the prescription medicines to take, with the
dosing, the directions and the prescriber's instructions (patient_medications.py)."""

from __future__ import annotations

import pytest
from helpers import login, visit

URL = "/api/v1/me/medications"


@pytest.fixture(scope="module")
def seeded(client):
    import seed
    from database import SessionLocal

    with SessionLocal() as db:
        seed.seed_all(db)
        return {
            "pat": seed._patient(db, seed.PATIENTS[0])[0].id,
            "sky": seed._patient(db, seed.SKY)[0].id,
            "doctor": seed._worker(db, seed.email_for("doctor")).id,
        }


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


def _tab(body: dict, patient_id: int) -> dict:
    return next(r for r in body["records"] if r["patient_id"] == patient_id)


def _current(tab: dict, name: str) -> dict | None:
    return next((m for m in tab["current"] if m["name"] == name), None)


def test_one_tab_per_record_with_the_medicines_dosing_and_instructions(client, seeded, patient):
    import seed

    res = client.get(URL, headers=patient)
    assert res.status_code == 200, res.text
    body = res.json()
    assert [r["first_name"] for r in body["records"]] == ["Pat", "Sky"]  # one tab each, in record order
    assert body["office_name"]

    pat = _tab(body, seeded["pat"])
    lisinopril = _current(pat, "Lisinopril")
    assert lisinopril["prescribed"] and lisinopril["status"] == "current"
    assert lisinopril["line"] == "Lisinopril 10 mg tablet"
    assert lisinopril["directions"] == "Take 1 tablet by mouth once daily"
    assert lisinopril["patient_instructions"] == seed.LISINOPRIL_INSTRUCTIONS
    assert lisinopril["indication"] == "Essential hypertension"
    assert lisinopril["prescription_number"].startswith("RX-") and lisinopril["prescriber_name"]
    assert (lisinopril["quantity"], lisinopril["refills"]) == (90, 3)
    assert lisinopril["sent_to_pharmacy"]
    statin = _current(pat, "Atorvastatin")  # on the list, not prescribed here: what the list says
    assert statin and not statin["prescribed"] and statin["directions"] == "20 mg at night"
    assert [a["name"] for a in pat["allergies"]] == ["Penicillin"]

    sky = _tab(body, seeded["sky"])
    assert {m["name"] for m in sky["current"]} == {"Albuterol HFA", "Cetirizine"}
    inhaler = _current(sky, "Albuterol HFA")
    assert inhaler["form"] == "inhaler" and "2 puffs" in inhaler["directions"]
    assert inhaler["patient_instructions"] == seed.ALBUTEROL_INSTRUCTIONS
    assert inhaler["indication"] == "Mild intermittent asthma, uncomplicated"
    assert _current(sky, "Cetirizine")["indication"] == "Allergic rhinitis due to pollen"
    assert [m["name"] for m in sky["stopped"]] == ["Amoxicillin"] and "hives" in sky["stopped"][0]["note"]
    assert len(sky["history"]) == 2 and all(h["patient_instructions"] for h in sky["history"])
    assert sky["pharmacy_name"] and [a["name"] for a in sky["allergies"]] == ["Amoxicillin"]


def test_a_new_prescription_with_instructions_shows_up_and_a_voided_one_goes_away(client, seeded, patient):
    doctor = login(client, "doctor")
    aid = visit(seeded["doctor"], 14, days_ahead=-3, patient_id=seeded["sky"], status="completed")
    instructions = "Rinse your mouth with water after each use. (Dummy.)"
    res = client.post(
        f"/api/v1/staff/me/appointments/{aid}/prescriptions",
        headers=doctor,
        json={
            "items": [
                {
                    "drug": "Fluticasone HFA",
                    "strength": "44 mcg/puff",
                    "form": "inhaler",
                    "sig": "Inhale 2 puffs by mouth twice daily",
                    "quantity": 1,
                    "quantity_unit": "inhaler",
                    "refills": 5,
                    "patient_instructions": f"  {instructions}  ",
                }
            ]
        },
    )
    assert res.status_code == 201, res.text
    rx = res.json()
    assert rx["items"][0]["patient_instructions"] == instructions  # trimmed
    pdf = client.get(f"/api/v1/staff/me/appointments/{aid}/prescriptions/{rx['id']}/pdf", headers=doctor)
    assert pdf.status_code == 200 and b"Patient instructions" in pdf.content

    sky = _tab(client.get(URL, headers=patient).json(), seeded["sky"])
    new = _current(sky, "Fluticasone HFA")
    assert new["patient_instructions"] == instructions and new["prescription_number"] == rx["number"]
    assert sky["history"][0]["prescription_number"] == rx["number"]  # newest first

    res = client.post(
        f"/api/v1/staff/me/appointments/{aid}/prescriptions/{rx['id']}/void",
        headers=doctor,
        json={"reason": "Wrong patient (dummy test)."},
    )
    assert res.status_code == 200, res.text
    sky = _tab(client.get(URL, headers=patient).json(), seeded["sky"])
    assert _current(sky, "Fluticasone HFA") is None
    assert all(m["name"] != "Fluticasone HFA" for m in sky["stopped"])  # the mistake is not shown at all
    assert all(h["prescription_number"] != rx["number"] for h in sky["history"])


def test_only_patient_accounts_read_it(client, seeded):
    assert client.get(URL).status_code == 401
    assert client.get(URL, headers=login(client, "admin")).status_code == 403
