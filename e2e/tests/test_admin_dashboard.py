"""The office administration dashboard (apps/admin) in a real browser: the pharmacy directory and the visit
summaries on the agenda."""

from __future__ import annotations

from conftest import admin_sign_in


def test_the_pharmacy_directory(page, stack):
    admin_sign_in(page, stack)
    page.click("#tab-pharmacies")
    page.wait_for_selector("#pharmacy-count")
    assert page.inner_text("#pharmacy-count").startswith("3 pharmacies")
    assert "no e-mail" in page.inner_text("#pharmacy-list")

    page.fill("#pharmacy-add-name", "Dummy E2E Pharmacy")
    page.fill("#pharmacy-add-email", "not-an-address")
    page.click("#btn-pharmacy-add")
    page.wait_for_selector("#pharmacy-add-msg .callout.error")
    page.fill("#pharmacy-add-email", "dummy-e2e-pharmacy@example.com")
    page.click("#btn-pharmacy-add")
    page.wait_for_function("document.querySelector('#pharmacy-count')?.textContent.startsWith('4 ')")
    row = page.locator(".row.pharmacy", has_text="Dummy E2E Pharmacy")
    row.locator("button", has_text="Deactivate").click()
    page.wait_for_function("document.querySelector('#pharmacy-count')?.textContent.startsWith('3 ')")
    page.check("#pharmacies-inactive")
    page.wait_for_selector(".row.pharmacy.inactive")
    assert "Dummy E2E Pharmacy" in page.inner_text(".row.pharmacy.inactive")


def test_the_agenda_shows_the_signed_visit_summary(page, stack):
    admin_sign_in(page, stack)
    page.goto(f"{stack.admin}/#/appointments?from=2000-01-01&to=2100-01-01&q=APT-000001")
    badge = page.locator(".agenda-summary-badge").first
    badge.wait_for()
    assert badge.inner_text().lower() == "summary signed"
    assert "signed by Alex Example" in page.inner_text(".agenda-summary")
    assert page.locator("[id^=btn-agenda-summary-pdf-]").count() >= 1
    assert page.locator("[id^=btn-agenda-summary-send-]").count() >= 1
