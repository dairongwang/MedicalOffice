"""Two-factor authentication end to end with the authenticator app: a user turns it on from the
account screen (password → QR code / key → first code → recovery codes), signs in through the
code step, signs in with a recovery code, turns it off again; an administrator with a second
factor signs in to the ads admin console; an administrator resets a locked-out user's second
factor from the admin panel. Codes by text message / e-mail on every kind of device:
test_two_factor_devices.py."""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

from conftest import SERVICE
from test_ads_admin import ADMIN
from test_membership import sign_in

sys.path.insert(0, str(SERVICE))
import clientcrypto  # noqa: E402  (the service's Python twin of crypto.ts)
import totp  # noqa: E402  (the same RFC 6238 arithmetic an authenticator app runs)


def _api(server: str, method: str, path: str, body: dict | None = None, token: str | None = None):
    req = urllib.request.Request(
        server + "/api/v1" + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})},
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, (json.loads(r.read() or b"null"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def _login_api(server: str, email: str, password: str) -> dict:
    auth_key = clientcrypto.derive_keys(password, email)[0]
    status, body = _api(server, "POST", "/auth/login", {"email": email, "auth_key": auth_key})
    assert status == 200, body  # 202 would mean a second factor — not for the seeded administrator
    return body


def wait_step(pg, step: str):
    """The code step replaces the password form; the account view follows a successful login."""
    pg.wait_for_selector(f"#account-dialog-title:text-is('{step}')", timeout=20000)


def next_code(secret: str, used: set[str]) -> str:
    """A code that has not been used yet (a code signs in once per 30-second step)."""
    while True:
        code = totp.code_at(secret)
        if code not in used:
            used.add(code)
            return code
        time.sleep(1)


def test_user_turns_it_on_signs_in_with_code_and_recovery_and_turns_it_off(browser, server, releaser):
    email, password = f"tfa{int(time.time())}@example.com", "a fine password"
    ctx = browser.new_context(viewport={"width": 1200, "height": 1000})
    pg = ctx.new_page()
    responses: list[tuple[str, int, str]] = []

    def on_response(r):
        if "/api/v1/auth/" in r.url or "/api/v1/me/2fa" in r.url:
            try:
                responses.append((r.url.split("/api/v1/")[-1], r.status, r.text()))
            except Exception:  # noqa: BLE001 — a response body that is already gone
                responses.append((r.url.split("/api/v1/")[-1], r.status, ""))

    pg.on("response", on_response)
    requests: list[str] = []
    pg.on("request", lambda r: requests.append((r.post_data or "") + r.url))
    pg.goto(server + "/")
    pg.wait_for_timeout(1500)

    # 1. A fresh account: the account screen offers to turn the second factor on.
    sign_in(pg, email, password, existing=False)
    pg.wait_for_selector("#tfa-status:has-text('Off')")
    pg.click("#tfa-setup-totp")
    pg.wait_for_selector("#tfa-password")
    pg.fill("#tfa-password", "not my password")
    pg.click("#tfa-continue")
    pg.wait_for_selector("#tfa-error:has-text('password is incorrect')")
    pg.fill("#tfa-password", password)
    pg.click("#tfa-continue")
    # 2. The QR code and the key are rendered on the device; the key is what the app would scan.
    pg.wait_for_selector("#tfa-qr path")
    secret = pg.inner_text("#tfa-secret").replace(" ", "")
    assert len(secret) == 32
    setup = next(json.loads(body) for url, status, body in responses if url == "me/2fa/setup" and status == 200)
    assert setup["secret"] == secret and "otpauth://totp/" in setup["otpauth_uri"]
    used: set[str] = set()
    # A wrong code does not activate anything …
    pg.fill("#tfa-code", "000000")
    pg.click("#tfa-activate")
    pg.wait_for_selector("#tfa-error:has-text('does not match')")
    # … the code from the "authenticator" does, and the recovery codes are shown once.
    pg.fill("#tfa-code", next_code(secret, used))
    pg.click("#tfa-activate")
    pg.wait_for_selector("#tfa-recovery")
    codes = [c.strip() for c in pg.inner_text("#tfa-recovery ul").split("\n") if c.strip()]
    assert len(codes) == 10 and all(len(c) == 11 and c[5] == "-" for c in codes)
    pg.click("#tfa-done")
    pg.wait_for_selector("#tfa-status:has-text('On')")
    assert "10 recovery codes left" in pg.inner_text("#tfa-status")
    assert not any(password in r for r in requests)  # the password never left the browser
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(300)

    # 3. Sign out, sign in: the password alone yields the code step — and hands nothing out.
    pg.click("#btn-logout")
    pg.wait_for_selector("#btn-login")
    responses.clear()
    pg.click("#btn-login")
    wait_step(pg, "Sign in")
    pg.fill("#acc-email", email)
    pg.fill("#acc-password", password)
    pg.click("#account-dialog button[type=submit]")
    wait_step(pg, "Two-factor code")
    challenge = next(json.loads(body) for url, status, body in responses if url == "auth/login" and status == 202)
    assert challenge["two_factor_required"] and "wrapped_data_key" not in json.dumps(challenge)
    assert pg.locator("#btn-logout").count() == 0  # not signed in yet
    pg.fill("#acc-code", "123456")
    pg.click("#account-dialog button[type=submit]")
    pg.wait_for_selector("#account-dialog .callout.error:has-text('not valid')")
    pg.fill("#acc-code", next_code(secret, used))
    pg.click("#account-dialog button[type=submit]")
    wait_step(pg, "Account & sync")
    pg.wait_for_selector("#btn-logout")
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(300)

    # 4. Lost the phone: a recovery code signs in (once) — spaces/case do not matter.
    pg.click("#btn-logout")
    pg.wait_for_selector("#btn-login")
    pg.click("#btn-login")
    wait_step(pg, "Sign in")
    pg.fill("#acc-email", email)
    pg.fill("#acc-password", password)
    pg.click("#account-dialog button[type=submit]")
    wait_step(pg, "Two-factor code")
    pg.click("#acc-code-back")  # changed my mind → back to the password step
    wait_step(pg, "Sign in")
    pg.fill("#acc-email", email)
    pg.fill("#acc-password", password)
    pg.click("#account-dialog button[type=submit]")
    wait_step(pg, "Two-factor code")
    pg.fill("#acc-code", codes[0].upper().replace("-", " "))
    pg.click("#account-dialog button[type=submit]")
    wait_step(pg, "Account & sync")
    pg.wait_for_selector("#tfa-status:has-text('9 recovery codes left')")

    # 5. Turn it off: password + a code. Afterwards the password alone signs in again.
    pg.click("#tfa-disable")
    pg.wait_for_selector("#tfa-disable-confirm")
    pg.fill("#tfa-password", password)
    pg.fill("#tfa-code", codes[0])  # already used
    pg.click("#tfa-disable-confirm")
    pg.wait_for_selector("#tfa-error:has-text('not valid')")
    pg.fill("#tfa-code", codes[1])
    pg.click("#tfa-disable-confirm")
    pg.wait_for_selector("#tfa-status:has-text('Off')")
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(300)
    pg.click("#btn-logout")
    pg.wait_for_selector("#btn-login")
    sign_in(pg, email, password)
    ctx.close()


def test_administrator_with_second_factor_uses_the_console_and_support_resets_a_user(
    browser, server, releaser, ads_admin_origin
):
    # An administrator with a second factor (set up through the API — the UI flow is covered above).
    email, password = f"tfadmin{int(time.time())}@example.com", "Admin-2fa-pass"
    auth_key, wrap_key = clientcrypto.derive_keys(password, email)
    status, s = _api(
        server,
        "POST",
        "/auth/register",
        {
            "email": email,
            "auth_key": auth_key,
            "wrapped_data_key": clientcrypto.wrap_data_key(b"\x00" * 32, wrap_key),
            "kdf_params": clientcrypto.KDF_PARAMS,
        },
    )
    assert status == 201, s
    boss = _login_api(server, *ADMIN)
    status, _ = _api(server, "PATCH", f"/admin/accounts/{s['account_id']}/role", {"role": "admin"}, boss["token"])
    assert status == 200
    status, setup = _api(server, "POST", "/me/2fa/setup", {"auth_key": auth_key}, s["token"])
    assert status == 200, setup
    used: set[str] = set()
    status, enabled = _api(server, "POST", "/me/2fa/enable", {"code": next_code(setup["secret"], used)}, s["token"])
    assert status == 200 and len(enabled["recovery_codes"]) == 10

    # 1. The ads admin console asks for the code after the password.
    ctx = browser.new_context(viewport={"width": 1280, "height": 1000})
    pg = ctx.new_page()
    pg.goto(ads_admin_origin)
    pg.wait_for_selector("#login-form")
    pg.fill("#login-email", email)
    pg.fill("#login-password", password)
    pg.click("#login-submit")
    pg.wait_for_selector("#login-code")
    pg.fill("#login-code", "000000")
    pg.click("#login-submit")
    pg.wait_for_selector("#login-error:has-text('not valid')")
    pg.fill("#login-code", next_code(setup["secret"], used))
    pg.click("#login-submit")
    pg.wait_for_selector("#kpi-impressions", timeout=20000)
    assert email in pg.inner_text("#who")

    # 2. Support: the seeded administrator resets the second factor from the user app's admin panel.
    admin_ctx = browser.new_context(viewport={"width": 1200, "height": 1000})
    admin = admin_ctx.new_page()
    admin.on("dialog", lambda d: d.accept())
    admin.goto(server + "/")
    admin.wait_for_timeout(1500)
    sign_in(admin, *ADMIN)
    admin.keyboard.press("Escape")
    admin.wait_for_timeout(300)
    admin.click("#btn-admin")
    admin.wait_for_selector("#admin-dialog .admin-stats")
    admin.fill("#admin-search", email)
    admin.press("#admin-search", "Enter")
    admin.wait_for_selector(f"#admin-accounts .admin-email:has-text('{email}')")
    admin.click("#admin-accounts .admin-row")
    admin.wait_for_selector("#admin-2fa:has-text('on')")
    admin.click("#admin-reset-2fa")
    admin.wait_for_selector("#admin-2fa:has-text('off')")
    assert admin.is_disabled("#admin-reset-2fa")
    admin.click("#admin-accounts details summary")
    assert "2fa.reset" in admin.inner_text("#admin-accounts details")
    admin_ctx.close()

    # 3. The reset signed the console session out; the password alone gets back in now.
    pg.reload()
    pg.wait_for_selector("#login-form", timeout=20000)
    pg.fill("#login-email", email)
    pg.fill("#login-password", password)
    pg.click("#login-submit")
    pg.wait_for_selector("#kpi-impressions", timeout=20000)
    assert pg.locator("#login-code").count() == 0
    ctx.close()
