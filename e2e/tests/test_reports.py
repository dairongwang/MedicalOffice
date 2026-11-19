"""Reports on request, in real browsers across the three front-ends: the patient's request for an annual
health examination report (seeded) is prepared by the office, completed and signed by the doctor, and read by
the patient; the office enters a vaccine in the record."""

from __future__ import annotations

from conftest import admin_sign_in, draw_signature, staff_sign_in


def test_the_office_prepares_the_doctor_signs_the_patient_reads(browser, stack):
    office = browser.new_page(viewport={"width": 1280, "height": 900})
    office.set_default_timeout(20_000)
    admin_sign_in(office, stack)
    office.click("#tab-reports")
    office.wait_for_selector("#reports-count")
    row = office.locator(".row.report", has_text="Annual health examination report").first
    assert "Running Club" in row.inner_text() and "to prepare" in row.inner_text().lower()
    row.locator("[id^=btn-report-prepare-]").click()
    office.wait_for_selector("[id^=report-visit-]")
    assert "Annual physical" in office.locator("[id^=report-visit-] option:checked").inner_text()
    row.locator("[id^=btn-report-generate-]").click()
    office.wait_for_function(
        "document.querySelector('#reports-count') && "
        "[...document.querySelectorAll('.row.report')].some(r => r.dataset.status === 'draft')"
    )
    report_id = office.locator(".row.report[data-status=draft]").first.get_attribute("data-id")
    mails = stack.outbox("dummy-doctor@example.com")
    assert mails[0]["subject"].startswith("To sign:")

    # The doctor completes their part and signs.
    doctor = browser.new_page(viewport={"width": 1280, "height": 900})
    doctor.set_default_timeout(20_000)
    staff_sign_in(doctor, stack, "dummy-doctor@example.com", "Dummy-Doctor-pass-123")
    doctor.click("#tab-reports")
    doctor.click(f"#report-link-{report_id}")
    doctor.wait_for_selector("#report-doctor-form")
    assert "APT-" in doctor.inner_text("#report-exam-visit")
    assert "Choose the decision" in doctor.inner_text("#report-missing")
    doctor.fill("#report-height", "165 cm")
    doctor.fill("#report-vision", "20/20 both eyes")
    for system in ("general", "cardiovascular", "respiratory", "musculoskeletal"):
        doctor.select_option(f"#report-system-{system}", "normal")
    doctor.select_option("#report-fitness", "fit")
    doctor.fill("#report-recommendations", "Keep training; recheck the blood pressure in three months.")
    doctor.click("#btn-report-save")
    doctor.wait_for_function("document.querySelector('#report-missing').textContent === ''")
    draw_signature(doctor, "#report-sign-form canvas.sig-canvas")
    doctor.check("#report-consent")
    before = len(stack.outbox("dummy-patient@example.com"))
    doctor.click("#btn-report-sign")
    doctor.wait_for_selector("#report-signature")
    assert "Signed by Alex Example" in doctor.inner_text("#report-signature")
    assert "Sent to the patient" in doctor.inner_text("#report-delivery")
    mails = stack.outbox("dummy-patient@example.com")
    assert len(mails) == before + 1 and mails[0]["attachments"][0]["filename"].endswith(".pdf")

    # The office sees it signed.
    office.click("#reports-filter-signed")
    office.wait_for_selector(f"#report-signed-{report_id}")

    # The patient reads it in the app.
    if stack.web:
        patient = browser.new_page(viewport={"width": 1280, "height": 900})
        patient.set_default_timeout(20_000)
        patient.goto(f"{stack.api}/patients/reports")
        patient.click("#btn-reports-login")
        patient.fill("#acc-email", "dummy-patient@example.com")
        patient.fill("#acc-password", "Dummy-Patient-pass-123")
        patient.click("#account-dialog button[type=submit]")
        patient.wait_for_selector(f"#btn-report-view-{report_id}")
        patient.click(f"#btn-report-view-{report_id}")
        view = patient.locator(f"#report-view-{report_id}")
        view.wait_for()
        text = view.inner_text()
        assert "Fit for the stated purpose without restrictions." in text
        assert "Electronically signed by Dr. Alex Example" in text and "165 cm" in text
        with patient.expect_download() as download:
            patient.click(f"#btn-report-pdf-{report_id}")
        assert download.value.suggested_filename.endswith(".pdf")

        # …and asks for another one.
        patient.check("#report-kind-immunization")
        patient.select_option("#report-purpose", "travel")
        patient.fill("#report-detail", "Trip abroad (dummy)")
        patient.click("#btn-report-request")
        patient.wait_for_selector(".report-row[data-status=requested]")
        patient.close()
    office.close()
    doctor.close()


def test_the_office_enters_a_vaccine(page, stack):
    admin_sign_in(page, stack)
    page.goto(f"{stack.admin}/#/patients/3/reports")
    page.wait_for_selector("#patient-immunization-list")
    count = page.locator("#patient-immunization-list > li").count()
    page.fill("#immunization-vaccine", "Influenza, inactivated (flu shot)")
    page.fill("#immunization-date", "2026-10-01")
    page.fill("#immunization-lot", "DUMMY-LOT-E2E")
    page.select_option("#immunization-route", "IM")
    page.click("#btn-immunization-add")
    page.wait_for_function(f"document.querySelectorAll('#patient-immunization-list > li').length === {count + 1}")
    assert "DUMMY-LOT-E2E" in page.inner_text("#patient-immunization-list")
    assert page.locator("#patient-reports").count() == 1
