"""Changing a second factor from the account screen — a new phone number, another e-mail
address for the codes, a new authenticator app — each gated by password + a current code and a
code from the new destination / app. Through the built-in fakes (test inbox)."""

from __future__ import annotations

import sys
import time

from conftest import SERVICE
from test_membership import sign_in
from test_two_factor_devices import code_sent_to, inbox

sys.path.insert(0, str(SERVICE))
import totp  # noqa: E402


def test_change_phone_email_and_authenticator(browser, server, releaser):
    email, password = f"change{int(time.time())}@example.com", "a fine password"
    old_phone, new_phone = "+15555550151", "+15555550152"
    ctx = browser.new_context(viewport={"width": 1200, "height": 1000})
    pg = ctx.new_page()
    pg.goto(server + "/")
    pg.wait_for_timeout(1500)
    sign_in(pg, email, password, existing=False)

    # Set up text-message codes to the old number.
    pg.click("#tfa-setup-sms")
    pg.fill("#tfa-phone", old_phone)
    pg.fill("#tfa-password", password)
    pg.click("#tfa-continue")
    pg.wait_for_selector("#tfa-sent")
    pg.fill("#tfa-code", code_sent_to(server, old_phone))
    pg.click("#tfa-activate")
    pg.wait_for_selector("#tfa-recovery")
    pg.click("#tfa-done")
    pg.wait_for_selector("#tfa-method-sms.on")

    # 1. Change the number: password + a current code (texted to the OLD number) + a code at the NEW one.
    pg.click("#tfa-change-sms")
    pg.wait_for_selector("#tfa-continue")
    assert "prove it is you" in pg.inner_text("#two-factor")
    pg.fill("#tfa-phone", new_phone)
    pg.fill("#tfa-password", password)
    assert pg.is_disabled("#tfa-continue")  # no current code yet
    before_old = len(inbox(server, old_phone))
    pg.click("#tfa-send-sms")
    pg.wait_for_selector("#tfa-sent:has-text('Code sent to')")
    pg.fill("#tfa-code", code_sent_to(server, old_phone, after=before_old))
    pg.click("#tfa-continue")
    pg.wait_for_selector("#tfa-sent:has-text('switch your codes over')")
    assert new_phone[-4:] in pg.inner_text("#tfa-sent")
    pg.fill("#tfa-code", "000000")
    pg.click("#tfa-activate")
    pg.wait_for_selector("#tfa-error:has-text('does not match')")
    pg.fill("#tfa-code", code_sent_to(server, new_phone))
    pg.click("#tfa-activate")
    pg.wait_for_selector(f"#tfa-method-sms.on:has-text('{new_phone[-4:]}')")
    # The old number and the account e-mail were told.
    assert "was just changed" in inbox(server, old_phone)[0]["body"]
    assert any("two-factor sign-in changed" in m.get("subject", "") for m in inbox(server, email))

    # 2. E-mail codes to a separate address, then change that address.
    pg.click("#tfa-setup-email")
    pg.fill("#tfa-email", "codes-a@example.net")
    pg.fill("#tfa-password", password)
    pg.click("#tfa-continue")
    pg.wait_for_selector("#tfa-sent")
    pg.fill("#tfa-code", code_sent_to(server, "codes-a@example.net"))
    pg.click("#tfa-activate")
    pg.wait_for_selector("#tfa-method-email.on:has-text('c•••@example.net')")
    pg.click("#tfa-change-email")
    pg.fill("#tfa-email", "codes-b@example.org")
    pg.fill("#tfa-password", password)
    before_a = len(inbox(server, "codes-a@example.net"))
    pg.click("#tfa-send-email")  # the current code goes to the OLD codes address
    pg.wait_for_selector("#tfa-sent:has-text('Code sent to')")
    pg.fill("#tfa-code", code_sent_to(server, "codes-a@example.net", after=before_a))
    pg.click("#tfa-continue")
    pg.wait_for_selector("#tfa-sent:has-text('switch your codes over')")
    pg.fill("#tfa-code", code_sent_to(server, "codes-b@example.org"))
    pg.click("#tfa-activate")
    pg.wait_for_selector("#tfa-method-email.on:has-text('c•••@example.org')")

    # 3. A new authenticator app: set one up, then replace it (current code from the old app).
    pg.click("#tfa-setup-totp")
    pg.fill("#tfa-password", password)
    pg.click("#tfa-continue")
    pg.wait_for_selector("#tfa-qr path")
    old_secret = pg.inner_text("#tfa-secret").replace(" ", "")
    pg.fill("#tfa-code", totp.code_at(old_secret))
    pg.click("#tfa-activate")
    pg.wait_for_selector("#tfa-method-totp.on")
    pg.click("#tfa-change-totp")
    pg.fill("#tfa-password", password)
    used = {totp.code_at(old_secret)}
    while (current := totp.code_at(old_secret)) in used:
        time.sleep(1)
    pg.fill("#tfa-code", current)
    pg.click("#tfa-continue")
    pg.wait_for_selector("#tfa-qr path")
    assert "new app" in pg.inner_text("#two-factor")
    new_secret = pg.inner_text("#tfa-secret").replace(" ", "")
    assert new_secret != old_secret
    activated = totp.code_at(new_secret)
    pg.fill("#tfa-code", activated)
    pg.click("#tfa-activate")
    pg.wait_for_selector("#tfa-method-totp.on")
    assert "authenticator app, text message, e-mail" in pg.inner_text("#tfa-status")
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(300)

    # 4. Signing in: the authenticator is preferred; the NEW app's code works, the old one's does not.
    pg.click("#btn-logout")
    pg.wait_for_selector("#btn-login")
    pg.click("#btn-login")
    pg.fill("#acc-email", email)
    pg.fill("#acc-password", password)
    pg.click("#account-dialog button[type=submit]")
    pg.wait_for_selector("#account-dialog-title:text-is('Two-factor code')", timeout=20000)
    while totp.code_at(new_secret) == activated:  # a code signs in once per 30-second step
        time.sleep(1)
    pg.fill("#acc-code", totp.code_at(old_secret))
    pg.click("#account-dialog button[type=submit]")
    pg.wait_for_selector("#account-dialog .callout.error:has-text('not valid')")
    pg.fill("#acc-code", totp.code_at(new_secret))
    pg.click("#account-dialog button[type=submit]")
    pg.wait_for_selector("#account-dialog-title:text-is('Account & sync')", timeout=20000)
    ctx.close()
