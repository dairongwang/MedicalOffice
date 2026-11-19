"""My medicines in real browsers: the doctor prescribes on the chart with instructions for the patient, and the
patient finds the medicine — dosing, directions, the instructions — on the dashboard's tab for that record."""

from __future__ import annotations

from conftest import staff_sign_in

INSTRUCTIONS = "Take it with breakfast or dinner to spare the stomach. (Dummy e2e.)"


def test_the_doctor_prescribes_with_instructions_and_the_patient_reads_them(browser, stack):
    aid = stack.visit_today(minutes_ago=20)
    doctor = browser.new_page(viewport={"width": 1280, "height": 900})
    doctor.set_default_timeout(20_000)
    staff_sign_in(doctor, stack, "dummy-doctor@example.com", "Dummy-Doctor-pass-123")
    doctor.goto(f"{stack.staff}/#/patient-chart/{aid}")
    doctor.wait_for_selector("#rx-form")
    row = doctor.locator("#rx-rows .rx-row").first
    row.locator(".rx-drug").fill("Metformin")
    row.locator(".rx-strength").fill("500 mg")
    row.locator(".rx-sig").fill("Take 1 tablet by mouth twice daily with meals")
    row.locator(".rx-instructions").fill(INSTRUCTIONS)
    row.locator(".rx-quantity").fill("60")
    row.locator(".rx-refills").fill("2")
    doctor.select_option("#rx-pharmacy", "")
    doctor.check("#rx-sign")
    doctor.click("#btn-rx-issue")
    item = doctor.locator("#chart-prescription-list .rx-patient-instructions", has_text="breakfast")
    item.wait_for()
    assert item.inner_text() == f"For the patient: {INSTRUCTIONS}"

    if stack.web:
        patient = browser.new_page(viewport={"width": 1280, "height": 900})
        patient.set_default_timeout(20_000)
        patient.goto(f"{stack.api}/patients/medicines")
        patient.click("#btn-medicines-login")
        patient.fill("#acc-email", "dummy-patient@example.com")
        patient.fill("#acc-password", "Dummy-Patient-pass-123")
        patient.click("#account-dialog button[type=submit]")
        patient.wait_for_selector("#medicines-card [role=tabpanel]")
        medicine = patient.locator("#medicines-card [id^=meds-current-] .med", has_text="Metformin 500 mg tablet")
        text = medicine.inner_text()
        assert "Take 1 tablet by mouth twice daily with meals" in text and INSTRUCTIONS in text
        assert "60 tablets · 2 refills" in text and "Dr. Alex Example" in text
