"""The staff dashboard (apps/staff) in a real browser: sign-in, applying, creating a staff account, the
week — and the doctor signing the visit summary the patient gets after the visit."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from conftest import draw_signature, staff_sign_in


def test_the_sign_in_page_lists_the_dummy_accounts(page, stack):
    page.goto(f"{stack.staff}/#/login")
    page.wait_for_selector("#login-form")
    hint = page.inner_text("#login-dummy-hint")
    assert "dummy-doctor@example.com" in hint and "Dummy-Doctor-pass-123" in hint
    page.fill("#login-email", "dummy-doctor@example.com")
    page.fill("#login-password", "not-the-password")
    page.click("#btn-login")
    page.wait_for_selector("#login-msg .callout.error")
    assert "incorrect" in page.inner_text("#login-msg")


def test_the_doctor_signs_the_visit_summary_and_the_patient_gets_it(page, stack):
    aid = stack.visit_today()
    staff_sign_in(page, stack, "dummy-doctor@example.com", "Dummy-Doctor-pass-123")
    assert page.url.endswith("#/my-appointments")
    page.goto(f"{stack.staff}/#/patient-chart/{aid}")
    page.wait_for_selector("#chart-patient")
    page.wait_for_selector("#summary-preview")
    assert "draft" in page.inner_text("#summary-head").lower()
    assert "Sore throat (dummy e2e visit)" in page.inner_text("#summary-preview")

    # The assessment / plan come from the visit note — saving the note updates the draft.
    page.click("#btn-visit-start")
    page.wait_for_selector("#chart-visit-note .badge >> text=seen from")
    page.fill("#visit-assessment", "Viral sore throat (dummy).")
    page.click("#btn-visit-save")
    page.wait_for_function("document.querySelector('#summary-assessment')?.value === 'Viral sore throat (dummy).'")
    page.fill("#summary-instructions", "Rest, drink plenty and come back if the fever lasts three days.")
    page.fill("#summary-follow-up", "No follow-up needed.")
    page.click("#btn-summary-save")
    page.wait_for_function("document.querySelector('#summary-instructions')?.value.startsWith('Rest')")

    # Signing needs the drawn signature and the consent.
    page.click("#btn-summary-sign")
    page.wait_for_selector("#summary-sign-form .callout.error")
    draw_signature(page, "#summary-sign-form canvas.sig-canvas")
    page.check("#summary-consent")
    before = len(stack.outbox("dummy-patient@example.com"))
    page.click("#btn-summary-sign")
    page.wait_for_selector("#summary-signature")
    assert "Signed by Alex Example" in page.inner_text("#summary-signature")
    assert "Sent to the patient" in page.inner_text("#summary-delivery")
    assert "signed" in page.inner_text("#summary-head").lower()
    assert page.locator("#summary-signature img.signature-img").count() == 1

    mails = stack.outbox("dummy-patient@example.com")
    assert len(mails) == before + 1
    assert "visit summary" in mails[0]["subject"].lower()
    assert mails[0]["attachments"][0]["filename"].endswith(".pdf")

    # My appointments says so too.
    today = datetime.now(timezone.utc).date()
    page.goto(f"{stack.staff}/#/my-appointments?from={today - timedelta(days=1)}&to={today}")
    row = page.locator(f"#mine-appt-{aid}")
    row.wait_for()
    assert "summary signed" in row.inner_text().lower()


def test_a_candidate_applies_and_checks_the_status(page, stack):
    page.goto(f"{stack.staff}/#/apply")
    page.wait_for_selector("#apply-form")
    page.fill("#apply-first", "Dummy")
    page.fill("#apply-last", "Candidate")
    page.fill("#apply-email", "dummy-candidate-e2e@example.com")
    page.fill("#apply-dob", "1991-05-05")
    page.select_option("#apply-role", "nurse")
    assert "passed" in page.inner_text("#apply-age-check").lower()
    page.fill("#apply-license", "DUMMY-RN-4242")
    page.fill("#apply-authority", "Example State Board of Nursing")
    page.check("#apply-background-consent")
    page.check("#apply-consent")
    page.click("#btn-apply-submit")
    page.wait_for_selector("#apply-receipt")
    receipt = page.inner_text("#apply-receipt")
    number = re.search(r"A-\d{6}", receipt).group(0)
    assert "was received" in receipt

    page.click("#link-apply-check")
    page.wait_for_selector("#apply-status-card")
    assert number in page.inner_text("#apply-status-card")
    assert "verifying your credentials" in page.inner_text("#apply-status-message")


def test_the_receptionist_creates_a_staff_account_with_the_emailed_code(page, stack):
    page.goto(f"{stack.staff}/#/create-account")
    page.fill("#enroll-number", "3")  # "W-000003", "w3" and "3" all work
    page.fill("#enroll-email", "dummy-frontdesk@example.com")
    page.click("#btn-enroll-send")
    page.wait_for_selector("#enroll-code-form")
    assert "fake outbox" in page.inner_text("#enroll-outbox")
    body = stack.outbox("dummy-frontdesk@example.com")[0]["body"]
    code = re.search(r"\b(\d{6})\b", body).group(1)
    page.fill("#enroll-code", code)
    page.fill("#enroll-password", "short")
    page.fill("#enroll-repeat", "short")
    page.click("#btn-enroll-create")
    page.wait_for_selector("#enroll-code-msg .callout.error")
    page.fill("#enroll-password", "Dummy-Frontdesk-pass-123")
    page.fill("#enroll-repeat", "Dummy-Frontdesk-pass-123")
    page.click("#btn-enroll-create")
    page.wait_for_selector("#btn-logout")
    assert "Robin Placeholder" in page.inner_text("#who")


def test_a_nurse_changes_her_week_with_staff_number_and_work_email(page, stack):
    page.goto(f"{stack.staff}/#/availability")
    page.fill("#availability-number", "W-000002")
    page.fill("#availability-email", "dummy-nurse@example.com")
    page.click("#btn-availability-lookup")
    page.wait_for_selector("#availability-card")
    assert "Sam Sample" in page.inner_text("#availability-worker")
    page.check("#day-on-5")  # Saturday
    page.click("#btn-availability-save")
    page.wait_for_function("document.querySelector('#btn-availability-save').textContent === 'Saved'")
    again = stack.get("/api/v1/appointments/providers")
    nurse = next(p for p in again["providers"] if p["last_name"] == "Sample")
    assert 5 in nurse["weekdays"]
