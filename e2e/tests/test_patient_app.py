"""The patients' app (apps/web, the production build the API serves) in a real browser: the patient reads
the visit summary their doctor signed, and the medicines to take — one tab per record on the account."""

from __future__ import annotations

import pytest


def _sign_in(page, stack) -> None:
    if not stack.web:
        pytest.skip("the patients' app is not built (npm is missing or apps/web/node_modules is not installed)")
    page.goto(f"{stack.api}/patients/mine")
    page.click("#btn-mine-login")
    page.fill("#acc-email", "dummy-patient@example.com")
    page.fill("#acc-password", "Dummy-Patient-pass-123")
    page.click("#account-dialog button[type=submit]")


def test_the_patient_reads_the_signed_visit_summary(page, stack):
    _sign_in(page, stack)
    page.wait_for_selector("#mine-past")
    page.wait_for_selector("#mine-past [id^=btn-mine-summary-]")
    pats_visit = page.locator("#mine-past li.appt.past", has_text="Pat Dummy").filter(
        has=page.locator("[id^=btn-mine-summary-]")
    )
    button = pats_visit.first.locator("[id^=btn-mine-summary-]")
    button.click()
    view = page.locator(".visit-summary").first
    view.wait_for()
    page.wait_for_selector(".visit-summary .vs-signature")
    text = view.inner_text()
    assert "Electronically signed by Dr. Alex Example" in text
    assert "Essential hypertension (I10)" in text
    assert "Lisinopril" in text
    assert view.locator(".vs-signature img").count() == 1
    with page.expect_download() as download:
        view.locator("button", has_text="Download PDF").click()
    assert download.value.suggested_filename.endswith(".pdf")


def test_the_patient_sees_the_medicines_to_take_for_each_record(page, stack):
    _sign_in(page, stack)
    page.wait_for_selector("#mine-upcoming")
    assert page.locator("#medicines-card").count() == 0  # not on My appointments: a page of its own
    page.click("#subnav-medicines")  # the Patients tab's sub-navigation, next to My reports
    page.wait_for_url("**/patients/medicines")
    assert "active" in page.get_attribute("#subnav-medicines", "class")
    assert page.inner_text("#medicines-title") == "My medicines"
    page.wait_for_selector("#medicines-card [role=tab]")
    tabs = page.locator("#medicines-card [role=tab]")
    assert [t.split()[0] for t in tabs.all_inner_texts()] == ["Pat", "Sky"]
    assert tabs.nth(0).get_attribute("aria-selected") == "true"
    panel = page.locator("#medicines-card [role=tabpanel]")
    text = panel.inner_text()
    assert "Lisinopril 10 mg tablet" in text and "Take 1 tablet by mouth once daily" in text
    assert "Stand up slowly if you feel dizzy" in text  # the doctor's instructions for the patient
    assert "Essential hypertension" in text and "Penicillin" in text

    tabs.nth(0).focus()
    page.keyboard.press("ArrowRight")  # the tabs pattern: the arrow keys move between them
    page.wait_for_function(
        "document.querySelectorAll('#medicines-card [role=tab]')[1].getAttribute('aria-selected') === 'true'"
    )
    text = panel.inner_text()
    assert "Albuterol HFA 90 mcg/puff inhaler" in text and "Use it with the spacer" in text
    assert "Cetirizine 5 mg/5 mL oral solution" in text and "Take 5 mL by mouth once daily" in text
    assert "Mild intermittent asthma" in text and "Amoxicillin (hives)" in text
    panel.locator("summary", has_text="No longer taking").click()
    assert "Stopped after three days" in panel.inner_text()


def test_payment_methods_and_history_are_one_payments_tab(page, stack):
    _sign_in(page, stack)
    page.wait_for_selector("#mine-upcoming")
    assert page.locator("#pay-methods").count() == 0  # no longer on My appointments
    assert page.locator("#subnav-history").count() == 0
    page.click("#subnav-payments")
    page.wait_for_url("**/patients/payments")
    assert page.inner_text("#subnav-payments") == "Payments"
    assert page.inner_text("#payments-title") == "Payments"
    page.wait_for_selector("#pay-list")
    assert "4242" in page.inner_text("#pay-methods")  # the seeded card (the public test number)
    page.wait_for_selector("#payhist-totals")
    assert "Payment history" in page.inner_text("#payment-history")

    page.goto(f"{stack.api}/patients/history")  # the old address
    page.wait_for_url("**/patients/payments#payment-history")
    page.wait_for_selector("#payhist-totals")
