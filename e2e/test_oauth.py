"""Sign in with Google / Facebook end to end, through the built-in fake providers: a new account
via Google (consent screen → passphrase → signed in), the same account on a "new device"
(Google → unlock with the passphrase, wrong one refused), cancelling at the provider, and a
password account linking Facebook from the account screen, signing in with it, unlinking."""

from __future__ import annotations

import time

from test_membership import sign_in


def fake_consent(pg, provider: str, email: str, *, cancel: bool = False):
    """The API's stand-in for the provider's consent screen."""
    pg.wait_for_selector("#fake-continue", timeout=20000)
    assert f"/api/v1/auth/oauth/fake/{provider}/authorize" in pg.url and "Built-in fake" in pg.inner_text("body")
    pg.fill("#email", email)
    pg.click("#fake-cancel" if cancel else "#fake-continue")


def test_new_account_with_google_then_new_device(browser, server, releaser):
    email, passphrase = f"gmail{int(time.time())}@gmail.com", "a long enough passphrase"
    ctx = browser.new_context(viewport={"width": 1200, "height": 1000})
    pg = ctx.new_page()
    requests: list[str] = []
    pg.on("request", lambda r: requests.append((r.post_data or "") + r.url))
    pg.goto(server + "/")
    pg.wait_for_timeout(1500)

    # 1. Register → "Google (fake)" → consent screen → back in the app: choose a passphrase.
    pg.click("#btn-register")
    pg.wait_for_selector("#acc-oauth-google")
    assert "(fake)" in pg.inner_text("#acc-oauth-google")
    pg.click("#acc-oauth-google")
    fake_consent(pg, "google", email)
    pg.wait_for_selector("#account-dialog-title:text-is('Create your passphrase')", timeout=20000)
    assert "Google" in pg.inner_text("#acc-passphrase-hint")
    assert "#" not in pg.url  # the ticket was taken out of the URL at once
    pg.fill("#acc-passphrase", passphrase)
    pg.fill("#acc-passphrase2", "something else")
    assert pg.is_disabled("#acc-passphrase-submit")
    pg.fill("#acc-passphrase2", passphrase)
    pg.click("#acc-passphrase-submit")
    pg.wait_for_selector("#account-dialog-title:text-is('Account & sync')", timeout=20000)
    pg.wait_for_selector("#btn-logout")
    assert email in pg.inner_text("#account-dialog")
    pg.wait_for_selector("#method-google.on")
    assert "Linked · " + email in pg.inner_text("#method-google")
    assert not any(passphrase in r for r in requests)  # the passphrase never left the browser
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(300)

    # 2. "New device": Google again → unlock with the passphrase (a wrong one is refused locally).
    pg.click("#btn-logout")
    pg.wait_for_selector("#btn-login")
    pg.click("#btn-login")
    pg.wait_for_selector("#acc-oauth-google")
    pg.click("#acc-oauth-google")
    fake_consent(pg, "google", email)
    pg.wait_for_selector("#account-dialog-title:text-is('Unlock your data')", timeout=20000)
    pg.fill("#acc-passphrase", "wrong passphrase")
    pg.click("#acc-passphrase-submit")
    pg.wait_for_selector("#account-dialog .callout.error:has-text('not right')")
    pg.fill("#acc-passphrase", passphrase)
    pg.click("#acc-passphrase-submit")
    pg.wait_for_selector("#account-dialog-title:text-is('Account & sync')", timeout=20000)
    pg.wait_for_selector("#account-dialog .device-list:has-text('-web')")  # whatever OS runs the test
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(300)

    # 3. The passphrase is also the password for e-mail sign-in.
    pg.click("#btn-logout")
    pg.wait_for_selector("#btn-login")
    sign_in(pg, email, passphrase)

    # 4. Cancelling at the provider comes back with a message, signed out.
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(300)
    pg.click("#btn-logout")
    pg.wait_for_selector("#btn-login")
    pg.click("#btn-login")
    pg.click("#acc-oauth-google")
    fake_consent(pg, "google", email, cancel=True)
    pg.wait_for_selector("#account-dialog .callout.error:has-text('cancelled')", timeout=20000)
    assert pg.locator("#btn-logout").count() == 0
    ctx.close()


def test_password_account_links_and_unlinks_facebook(browser, server, releaser):
    email, password = f"linker{int(time.time())}@example.com", "a fine password"
    ctx = browser.new_context(viewport={"width": 1200, "height": 1000})
    pg = ctx.new_page()
    pg.on("dialog", lambda d: d.accept())
    pg.goto(server + "/")
    pg.wait_for_timeout(1500)
    sign_in(pg, email, password, existing=False)
    pg.wait_for_selector("#method-facebook:not(.on)")
    pg.click("#acc-link-facebook")
    fake_consent(pg, "facebook", "linker.fb@example.org")
    pg.wait_for_selector("#method-facebook.on", timeout=20000)
    assert "linker.fb@example.org" in pg.inner_text("#method-facebook")
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(300)
    # Sign in with Facebook: the account's password unlocks the data.
    pg.click("#btn-logout")
    pg.wait_for_selector("#btn-login")
    pg.click("#btn-login")
    pg.click("#acc-oauth-facebook")
    fake_consent(pg, "facebook", "linker.fb@example.org")
    pg.wait_for_selector("#account-dialog-title:text-is('Unlock your data')", timeout=20000)
    pg.fill("#acc-passphrase", password)
    pg.click("#acc-passphrase-submit")
    pg.wait_for_selector("#account-dialog-title:text-is('Account & sync')", timeout=20000)
    assert email in pg.inner_text("#account-dialog")
    # Unlink.
    pg.click("#acc-unlink-facebook")
    pg.wait_for_selector("#method-facebook:not(.on)")
    ctx.close()


def test_new_account_with_apple_hide_my_email(browser, server, releaser):
    """Apple's consent screen posts the result back (form_post); "Hide my email" gives a relay address."""
    ctx = browser.new_context(viewport={"width": 1200, "height": 1000})
    pg = ctx.new_page()
    pg.goto(server + "/")
    pg.wait_for_timeout(1500)
    pg.click("#btn-register")
    pg.wait_for_selector("#acc-oauth-apple")
    pg.click("#acc-oauth-apple")
    pg.wait_for_selector("#fake-continue", timeout=20000)
    pg.fill("#email", f"apple{int(time.time())}@icloud.com")
    pg.check("#fake-private")
    pg.click("#fake-continue")
    pg.wait_for_selector("#account-dialog-title:text-is('Create your passphrase')", timeout=20000)
    assert "Apple" in pg.inner_text("#acc-passphrase-hint")
    pg.fill("#acc-passphrase", "relay passphrase 1")
    pg.fill("#acc-passphrase2", "relay passphrase 1")
    pg.click("#acc-passphrase-submit")
    pg.wait_for_selector("#account-dialog-title:text-is('Account & sync')", timeout=20000)
    pg.wait_for_selector("#method-apple.on")
    assert "@privaterelay.appleid.com" in pg.inner_text("#account-dialog")
    ctx.close()
