"""Two-factor authentication by text message, e-mail and authenticator app on every kind of
device the app runs on: iPhone, Android phone, iPad, Android tablet, Windows, macOS and Linux.

Each device profile (screen size, touch, pixel ratio, user agent) sets up one method from the
account screen, signs out and signs back in through the code step. No e-mail server or SMS
provider is configured, so the built-in fake delivers the codes to the test inbox — which the
test reads like a person would read their phone (``/api/v1/messages/fake/outbox.json``).

The profiles run in whichever engine the e2e run uses (E2E_BROWSER: chromium, msedge, chrome,
firefox, webkit) — with WebKit the iPhone/iPad/macOS rows run on Safari's engine."""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse
import urllib.request

import pytest
from conftest import SERVICE
from test_ads_admin import ADMIN
from test_membership import sign_in

sys.path.insert(0, str(SERVICE))
import clientcrypto  # noqa: E402
import totp  # noqa: E402

# Screen, input and browser identity of each platform (values as in Playwright's device list).
DEVICES = {
    "iphone": dict(
        viewport={"width": 390, "height": 844},
        device_scale_factor=3,
        is_mobile=True,
        has_touch=True,
        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    ),
    "android-phone": dict(
        viewport={"width": 412, "height": 915},
        device_scale_factor=2.625,
        is_mobile=True,
        has_touch=True,
        user_agent="Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Mobile Safari/537.36",
    ),
    "ipad": dict(
        viewport={"width": 810, "height": 1080},
        device_scale_factor=2,
        is_mobile=True,
        has_touch=True,
        user_agent="Mozilla/5.0 (iPad; CPU OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.4 Mobile/15E148 Safari/604.1",
    ),
    "android-tablet": dict(
        viewport={"width": 712, "height": 1138},
        device_scale_factor=2.25,
        is_mobile=True,
        has_touch=True,
        user_agent="Mozilla/5.0 (Linux; Android 13; SM-X700) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36",
    ),
    "windows": dict(
        viewport={"width": 1366, "height": 768},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    ),
    "macos": dict(
        viewport={"width": 1440, "height": 900},
        device_scale_factor=2,
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.4 Safari/605.1.15",
    ),
    "linux": dict(
        viewport={"width": 1280, "height": 800},
        user_agent="Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    ),
}
# The method each profile sets up, and the platform label the app reports for it (device list).
MATRIX = [
    ("iphone", "sms", "ios-web"),
    ("android-phone", "sms", "android-web"),
    ("ipad", "email", "ios-web"),
    ("android-tablet", "email", "android-web"),
    ("windows", "totp", "windows-web"),
    ("macos", "email", "macos-web"),
    ("linux", "sms", "linux-web"),
]


def context_for(browser, name: str):
    options = dict(DEVICES[name])
    engine = getattr(browser, "e2e_name", "")
    if "firefox" in engine:  # Firefox has no mobile emulation; screen + touch still apply
        options.pop("is_mobile", None)
    return browser.new_context(**options)


def inbox(server: str, to: str) -> list[dict]:
    query = urllib.parse.urlencode({"to": to})
    with urllib.request.urlopen(f"{server}/api/v1/messages/fake/outbox.json?{query}") as r:
        return json.loads(r.read())["messages"]


def code_sent_to(server: str, to: str, after: int = 0) -> str:
    """The code in the newest message to ``to`` (waits until more than ``after`` messages exist)."""
    for _ in range(50):
        messages = inbox(server, to)
        if len(messages) > after:
            return re.search(r"\b(\d{6})\b", messages[0]["body"]).group(1)
        time.sleep(0.2)
    raise AssertionError(f"no code was sent to {to}")


def fresh_totp(secret: str, used: set[str]) -> str:
    while (code := totp.code_at(secret)) in used:
        time.sleep(1)
    used.add(code)
    return code


@pytest.mark.parametrize(("device", "method", "platform"), MATRIX, ids=[f"{d}-{m}" for d, m, _ in MATRIX])
def test_second_factor_on_every_device(browser, server, releaser, device, method, platform):
    email, password = f"{device}-{method}-{int(time.time() * 1000)}@example.com", "a fine password"
    phone = f"+1555555{int(time.time() * 1000) % 10000:04d}"  # fictional 555 numbers, one per run
    to = phone if method == "sms" else email
    ctx = context_for(browser, device)
    pg = ctx.new_page()
    pg.goto(server + "/")
    pg.wait_for_timeout(1500)
    sign_in(pg, email, password, existing=False)
    pg.wait_for_selector("#tfa-status:has-text('Off')")

    # 1. Set up the method from the account screen.
    pg.click(f"#tfa-setup-{method}")
    if method == "sms":
        pg.fill("#tfa-phone", phone[:2] + " " + phone[2:5] + " " + phone[5:])  # typed with spaces, as people do
    pg.fill("#tfa-password", password)
    pg.click("#tfa-continue")
    used: set[str] = set()
    if method == "totp":
        pg.wait_for_selector("#tfa-qr path")
        secret = pg.inner_text("#tfa-secret").replace(" ", "")
        # Phones/tablets cannot scan their own screen: they get a link into the authenticator app.
        touch = DEVICES[device].get("has_touch", False)
        assert pg.locator("#tfa-open-app").count() == (1 if touch else 0)
        code = fresh_totp(secret, used)
    else:
        pg.wait_for_selector("#tfa-sent")
        assert ("text message" if method == "sms" else "e-mail") in pg.inner_text("#tfa-sent")
        assert pg.locator(".tfa-outbox-link").count() == 1  # the fake says where the message went
        code = code_sent_to(server, to)
    assert pg.get_attribute("#tfa-code", "autocomplete") == "one-time-code"  # iOS/macOS autofill
    pg.fill("#tfa-code", code)
    pg.click("#tfa-activate")
    pg.wait_for_selector("#tfa-recovery")
    codes = [c.strip() for c in pg.inner_text("#tfa-recovery ul").split("\n") if c.strip()]
    assert len(codes) == 10
    pg.click("#tfa-done")
    pg.wait_for_selector(f"#tfa-method-{method}.on")
    if method == "sms":
        assert phone[-4:] in pg.inner_text("#tfa-method-sms") and phone[2:8] not in pg.inner_text("#tfa-method-sms")
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(300)

    # 2. Sign out, sign in: the password step leads to the code step.
    pg.click("#btn-logout")
    pg.wait_for_selector("#btn-login")
    before = len(inbox(server, to)) if method != "totp" else 0
    pg.click("#btn-login")
    pg.wait_for_selector("#account-dialog-title:text-is('Sign in')")
    pg.fill("#acc-email", email)
    pg.fill("#acc-password", password)
    pg.click("#account-dialog button[type=submit]")
    pg.wait_for_selector("#account-dialog-title:text-is('Two-factor code')", timeout=20000)
    hint = pg.inner_text("#acc-code-hint")
    if method == "totp":
        assert "authenticator app" in hint
        code = fresh_totp(secret, used)
    else:
        assert "We sent a 6-digit code" in hint and to[-4:] in hint
        assert pg.locator("#acc-outbox-link").count() == 1
        code = code_sent_to(server, to, after=before)
    pg.fill("#acc-code", code)
    pg.click("#account-dialog button[type=submit]")
    pg.wait_for_selector("#account-dialog-title:text-is('Account & sync')", timeout=20000)
    # The device list shows the platform of this device.
    pg.wait_for_selector(f"#account-dialog .device-list:has-text('{platform}')")
    # Nothing overflows the screen horizontally (phones, tablets, desktops).
    assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    assert pg.evaluate(
        "(() => { const d = document.querySelector('#account-dialog'); return d.scrollWidth <= d.clientWidth + 1; })()"
    )
    ctx.close()


def test_switching_between_text_and_email_and_recovery_on_a_phone(browser, server, releaser):
    """An account with text message and e-mail: the text comes first; on the code step the user
    can ask for an e-mail instead, resend, or fall back to a recovery code."""
    email, password = f"switch{int(time.time())}@example.com", "a fine password"
    phone = "+15555550142"
    pg_ctx = context_for(browser, "android-phone")
    pg = pg_ctx.new_page()
    pg.goto(server + "/")
    pg.wait_for_timeout(1500)
    sign_in(pg, email, password, existing=False)
    for method, to in (("sms", phone), ("email", email)):
        pg.click(f"#tfa-setup-{method}")
        if method == "sms":
            pg.fill("#tfa-phone", phone)
        pg.fill("#tfa-password", password)
        pg.click("#tfa-continue")
        pg.wait_for_selector("#tfa-sent")
        pg.fill("#tfa-code", code_sent_to(server, to))
        pg.click("#tfa-activate")
        if method == "sms":
            pg.wait_for_selector("#tfa-recovery")
            codes = [c.strip() for c in pg.inner_text("#tfa-recovery ul").split("\n") if c.strip()]
            pg.click("#tfa-done")
        pg.wait_for_selector(f"#tfa-method-{method}.on")
    assert "text message, e-mail" in pg.inner_text("#tfa-status")
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(300)
    pg.click("#btn-logout")
    pg.wait_for_selector("#btn-login")

    sms_before, mail_before = len(inbox(server, phone)), len(inbox(server, email))
    pg.click("#btn-login")
    pg.fill("#acc-email", email)
    pg.fill("#acc-password", password)
    pg.click("#account-dialog button[type=submit]")
    pg.wait_for_selector("#account-dialog-title:text-is('Two-factor code')", timeout=20000)
    assert "text message" in pg.inner_text("#acc-code-hint")
    assert len(inbox(server, phone)) == sms_before + 1 and len(inbox(server, email)) == mail_before
    # Ask for an e-mail instead.
    pg.click("#acc-method-email")
    pg.wait_for_selector("#acc-code-hint:has-text('by e-mail')")
    mail_code = code_sent_to(server, email, after=mail_before)
    # A wrong code first, then the right one.
    pg.fill("#acc-code", "000000" if mail_code != "000000" else "111111")
    pg.click("#account-dialog button[type=submit]")
    pg.wait_for_selector("#account-dialog .callout.error:has-text('not valid')")
    pg.fill("#acc-code", mail_code)
    pg.click("#account-dialog button[type=submit]")
    pg.wait_for_selector("#account-dialog-title:text-is('Account & sync')", timeout=20000)
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(300)

    # Phone lost: a recovery code on the code step.
    pg.click("#btn-logout")
    pg.wait_for_selector("#btn-login")
    pg.click("#btn-login")
    pg.fill("#acc-email", email)
    pg.fill("#acc-password", password)
    pg.click("#account-dialog button[type=submit]")
    pg.wait_for_selector("#account-dialog-title:text-is('Two-factor code')", timeout=20000)
    pg.click("#acc-method-recovery")
    pg.wait_for_selector("#acc-code-hint:has-text('recovery codes you saved')")
    pg.fill("#acc-code", codes[0])
    pg.click("#account-dialog button[type=submit]")
    pg.wait_for_selector("#account-dialog-title:text-is('Account & sync')", timeout=20000)

    # Remove the text-message method with a code sent by e-mail.
    pg.click("#tfa-remove-sms")
    pg.fill("#tfa-password", password)
    before = len(inbox(server, email))
    pg.click("#tfa-send-email")
    pg.wait_for_selector("#tfa-sent")
    pg.fill("#tfa-code", code_sent_to(server, email, after=before))
    pg.click("#tfa-disable-confirm")
    pg.wait_for_selector("#tfa-method-sms:not(.on)")
    assert pg.locator("#tfa-method-email.on").count() == 1
    pg_ctx.close()


def test_test_inbox_is_readable_on_a_phone(browser, server, releaser):
    ctx = context_for(browser, "iphone")
    pg = ctx.new_page()
    pg.goto(server + "/api/v1/messages/fake/outbox")
    pg.wait_for_selector("#messages")
    assert "Built-in fake" in pg.inner_text("body")
    assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    ctx.close()


def test_administrator_with_email_codes_on_the_ads_admin_console(browser, server, releaser, ads_admin_origin):
    email, password = f"mailadmin{int(time.time())}@example.com", "Admin-mail-pass"
    auth_key, wrap_key = clientcrypto.derive_keys(password, email)

    def call(method, path, body=None, token=None):
        req = urllib.request.Request(
            server + "/api/v1" + path,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})},
        )
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read() or b"null")

    s = call(
        "POST",
        "/auth/register",
        {
            "email": email,
            "auth_key": auth_key,
            "wrapped_data_key": clientcrypto.wrap_data_key(b"\x00" * 32, wrap_key),
            "kdf_params": clientcrypto.KDF_PARAMS,
        },
    )
    boss = call("POST", "/auth/login", {"email": ADMIN[0], "auth_key": clientcrypto.derive_keys(*reversed(ADMIN))[0]})
    call("PATCH", f"/admin/accounts/{s['account_id']}/role", {"role": "admin"}, boss["token"])
    call("POST", "/me/2fa/setup", {"auth_key": auth_key, "method": "email"}, s["token"])
    call("POST", "/me/2fa/enable", {"code": code_sent_to(server, email), "method": "email"}, s["token"])

    ctx = browser.new_context(viewport={"width": 1280, "height": 1000})
    pg = ctx.new_page()
    pg.goto(ads_admin_origin)
    pg.wait_for_selector("#login-form")
    before = len(inbox(server, email))
    pg.fill("#login-email", email)
    pg.fill("#login-password", password)
    pg.click("#login-submit")
    pg.wait_for_selector("#login-code")
    assert "by e-mail" in pg.inner_text("#login-2fa-hint")
    assert pg.locator("#login-outbox a").count() == 1
    code = code_sent_to(server, email, after=before)
    # Resending right away is refused politely (no mail bombing) and the code step stays.
    pg.click("#login-send-email")
    pg.wait_for_selector("#login-error:has-text('just sent')")
    assert len(inbox(server, email)) == before + 1
    pg.fill("#login-code", "000000" if code != "000000" else "111111")
    pg.click("#login-submit")
    pg.wait_for_selector("#login-error:has-text('not valid')")
    pg.fill("#login-code", code)
    pg.click("#login-submit")
    pg.wait_for_selector("#kpi-impressions", timeout=20000)
    ctx.close()
