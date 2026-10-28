"""Two-factor codes by text message (SMS) and e-mail: enrolment with a confirmation code,
sign-in with a sent code, switching/resending, removing one method, limits, provider failures —
against the built-in fake outbox — and the real adapters (SMTP, Twilio) against stubs."""

from __future__ import annotations

import base64
import re
import smtplib
from datetime import timedelta
from urllib.parse import parse_qs

import pytest
from conftest import auth_key_for, headers, register

import config
import database
import messaging
import models
import totp
import twofactor

EMAIL, PASSWORD = "ann@example.com", "correct horse"
PHONE = "+1 (415) 555-0100"
E164 = "+14155550100"


def last_code(to: str) -> str:
    """The code in the newest message the fake delivered to ``to``."""
    message = messaging.OUTBOX.latest(to=to)
    assert message, f"no message to {to}"
    return re.search(r"\b(\d{6})\b", message["body"]).group(1)


def password_step(client, email=EMAIL, password=PASSWORD):
    return client.post(
        "/api/v1/auth/login",
        json={"email": email, "auth_key": auth_key_for(password, email), "device": {"platform": "test"}},
    )


def code_step(client, challenge: str, code: str):
    return client.post("/api/v1/auth/login/2fa", json={"challenge": challenge, "code": code})


def setup(client, s, method, phone=None, password=PASSWORD, email=EMAIL):
    return client.post(
        "/api/v1/me/2fa/setup",
        json={"auth_key": auth_key_for(password, email), "method": method, "phone": phone},
        headers=headers(s),
    )


def enable_sms(client, s) -> list[str]:
    r = setup(client, s, "sms", PHONE)
    assert r.status_code == 200, r.text
    r = client.post("/api/v1/me/2fa/enable", json={"code": last_code(E164), "method": "sms"}, headers=headers(s))
    assert r.status_code == 200, r.text
    return r.json()["recovery_codes"]


@pytest.fixture(autouse=True)
def no_resend_wait(monkeypatch):
    monkeypatch.setattr(config, "TWO_FACTOR_RESEND_SECONDS", 0)


# ---------------------------------------------------------------------------- helpers


def test_phone_numbers_are_normalised_to_e164_and_masked():
    assert twofactor.normalize_phone(PHONE) == E164
    assert twofactor.normalize_phone("0049 151 2345 6789") == "+4915123456789"
    assert twofactor.normalize_phone("+44 20 7946 0958") == "+442079460958"
    for bad in ("", None, "555-0100", "+0 123 456 789", "+1 555 abc 0100", "+1234567", "+1234567890123456"):
        assert twofactor.normalize_phone(bad) is None, bad
    assert twofactor.mask_phone(E164) == "+•••••••0100"
    assert twofactor.mask_email("ann@example.com") == "a•••@example.com"


# ---------------------------------------------------------------------------- text messages


def test_sms_enrolment_sign_in_and_single_use(client):
    s = register(client)
    # A number in international format is required — nothing is sent otherwise.
    r = setup(client, s, "sms", "555 0100")
    assert r.status_code == 422 and "international format" in r.json()["detail"]
    assert messaging.OUTBOX.messages() == []
    # The code goes to the number being added; the answer says where (masked) and that the fake took it.
    r = setup(client, s, "sms", PHONE)
    assert r.status_code == 200, r.text
    delivery = r.json()["delivery"]
    assert delivery == {
        "method": "sms",
        "destination": "+•••••••0100",
        "expires_in": 600,
        "resend_after": 0,
        "fake_outbox_url": "/api/v1/messages/fake/outbox",
    }
    sms = messaging.OUTBOX.latest(to=E164)
    assert (
        sms["channel"] == "sms" and sms["subject"] == "" and "turn on two-factor codes by text message" in sms["body"]
    )
    # Wrong code: 403 (the session stays), nothing turned on, the phone is not stored yet.
    r = client.post("/api/v1/me/2fa/enable", json={"code": "000000", "method": "sms"}, headers=headers(s))
    assert r.status_code == 403 and "does not match" in r.json()["detail"]
    with database.SessionLocal() as db:
        assert db.query(models.Account).one().phone_number is None
    r = client.post("/api/v1/me/2fa/enable", json={"code": last_code(E164), "method": "sms"}, headers=headers(s))
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["recovery_codes"]) == 10  # the first method hands out the recovery codes
    assert body["status"]["methods"] == ["sms"] and body["status"]["phone"] == "+•••••••0100"
    with database.SessionLocal() as db:
        acc = db.query(models.Account).one()
        assert acc.phone_number == E164 and acc.sms_otp_enabled_at and db.query(models.OneTimeCode).count() == 0

    # Sign-in: the password step sends the text right away.
    r = password_step(client)
    assert r.status_code == 202
    challenge = r.json()
    assert challenge["default_method"] == "sms" and challenge["methods"] == ["sms", "recovery"]
    assert challenge["destinations"] == {"sms": "+•••••••0100"}
    assert challenge["sent"]["method"] == "sms" and challenge["send_error"] is None
    code = last_code(E164)
    assert "to sign in" in messaging.OUTBOX.latest(to=E164)["body"]
    assert code_step(client, challenge["challenge"], "000000").status_code == 401
    r = code_step(client, challenge["challenge"], code)
    assert r.status_code == 200 and r.json()["token"]
    assert code_step(client, challenge["challenge"], code).status_code == 401  # used up
    # E-mail is not on for this account: no codes by e-mail.
    r = client.post("/api/v1/auth/login/2fa/send", json={"challenge": challenge["challenge"], "method": "email"})
    assert r.status_code == 409


def test_email_enrolment_and_switching_channels_at_sign_in(client):
    s = register(client)
    codes = enable_sms(client, s)
    # E-mail as a second method: confirmed like the phone; no new recovery codes.
    r = setup(client, s, "email")
    assert r.status_code == 200 and r.json()["delivery"]["destination"] == "a•••@example.com"
    mail = messaging.OUTBOX.latest(to=EMAIL)
    assert mail["channel"] == "email" and mail["subject"].endswith("is your Personal Assistant code")
    r = client.post("/api/v1/me/2fa/enable", json={"code": last_code(EMAIL), "method": "email"}, headers=headers(s))
    assert r.status_code == 200 and r.json()["recovery_codes"] == []
    assert r.json()["status"]["methods"] == ["sms", "email"] and r.json()["status"]["recovery_codes_left"] == 10
    assert setup(client, s, "email").status_code == 403  # already on: changing it needs a current code

    # Sign-in: text by default, e-mail on request — only the newest code counts.
    challenge = password_step(client).json()
    assert challenge["methods"] == ["sms", "email", "recovery"]
    assert challenge["destinations"] == {"sms": "+•••••••0100", "email": "a•••@example.com"}
    sms_code = last_code(E164)
    r = client.post("/api/v1/auth/login/2fa/send", json={"challenge": challenge["challenge"], "method": "email"})
    assert r.status_code == 200 and r.json()["method"] == "email"
    mail_code = last_code(EMAIL)
    if sms_code != mail_code:
        assert code_step(client, challenge["challenge"], sms_code).status_code == 401  # replaced
    assert code_step(client, challenge["challenge"], mail_code).status_code == 200
    assert codes  # recovery codes from the first method still valid
    assert code_step(client, password_step(client).json()["challenge"], codes[0]).status_code == 200


def test_authenticator_stays_preferred_and_sent_codes_still_work(client):
    s = register(client)
    enable_sms(client, s)
    r = setup(client, s, "totp")
    secret = r.json()["secret"]
    r = client.post(
        "/api/v1/me/2fa/enable",
        json={"code": totp.code_at(secret, totp._now() - 30), "method": "totp"},
        headers=headers(s),
    )
    assert r.status_code == 200 and r.json()["status"]["methods"] == ["totp", "sms"]
    messaging.OUTBOX.clear()
    challenge = password_step(client).json()
    # The authenticator is preferred: no text is sent unless asked for.
    assert challenge["default_method"] == "totp" and challenge["sent"] is None and messaging.OUTBOX.messages() == []
    client.post("/api/v1/auth/login/2fa/send", json={"challenge": challenge["challenge"], "method": "sms"})
    assert code_step(client, challenge["challenge"], last_code(E164)).status_code == 200


def test_remove_one_method_then_the_last_turns_everything_off(client):
    s = register(client)
    enable_sms(client, s)
    setup(client, s, "email")
    client.post("/api/v1/me/2fa/enable", json={"code": last_code(EMAIL), "method": "email"}, headers=headers(s))
    # A code to confirm the change, by e-mail (a phone-less moment).
    r = client.post("/api/v1/me/2fa/send", json={"method": "email"}, headers=headers(s))
    assert r.status_code == 200
    assert "confirm a change to your sign-in security" in messaging.OUTBOX.latest(to=EMAIL)["body"]
    r = client.post(
        "/api/v1/me/2fa/disable",
        json={"auth_key": auth_key_for(PASSWORD, EMAIL), "code": last_code(EMAIL), "method": "sms"},
        headers=headers(s),
    )
    assert r.status_code == 200, r.text
    assert r.json()["methods"] == ["email"] and r.json()["phone"] is None and r.json()["recovery_codes_left"] == 10
    with database.SessionLocal() as db:
        assert db.query(models.Account).one().phone_number is None  # the number is gone with the method
    assert client.post("/api/v1/me/2fa/send", json={"method": "sms"}, headers=headers(s)).status_code == 409
    # Removing the last method turns the second factor off entirely.
    client.post("/api/v1/me/2fa/send", json={"method": "email"}, headers=headers(s))
    r = client.post(
        "/api/v1/me/2fa/disable",
        json={"auth_key": auth_key_for(PASSWORD, EMAIL), "code": last_code(EMAIL), "method": "email"},
        headers=headers(s),
    )
    assert r.json()["enabled"] is False and r.json()["recovery_codes_left"] == 0
    assert password_step(client).status_code == 200
    with database.SessionLocal() as db:
        actions = [a.action for a in db.query(models.AuditLog).order_by(models.AuditLog.id)]
    assert actions == ["2fa.enable", "2fa.enable", "2fa.method_removed", "2fa.disable"]


def test_new_recovery_codes_with_a_texted_code(client):
    s = register(client)
    old = enable_sms(client, s)
    client.post("/api/v1/me/2fa/send", json={"method": "sms"}, headers=headers(s))
    r = client.post(
        "/api/v1/me/2fa/recovery-codes",
        json={"auth_key": auth_key_for(PASSWORD, EMAIL), "code": last_code(E164)},
        headers=headers(s),
    )
    assert r.status_code == 200 and not set(r.json()["recovery_codes"]) & set(old)


# ---------------------------------------------------------------------------- limits


def test_sent_codes_expire_and_die_after_too_many_wrong_tries(client, monkeypatch):
    s = register(client)
    enable_sms(client, s)
    monkeypatch.setattr(twofactor.LIMITER, "limit", 100)  # isolate the per-code limit
    challenge = password_step(client).json()["challenge"]
    code = last_code(E164)
    for _ in range(twofactor.MAX_ATTEMPTS):
        assert code_step(client, challenge, "000000" if code != "000000" else "111111").status_code == 401
    assert code_step(client, challenge, code).status_code == 401  # void after MAX_ATTEMPTS wrong tries
    # A new code works — until it expires.
    client.post("/api/v1/auth/login/2fa/send", json={"challenge": challenge, "method": "sms"})
    code = last_code(E164)
    with database.SessionLocal() as db:
        row = db.query(models.OneTimeCode).one()
        row.expires_at = models.utcnow() - timedelta(seconds=1)
        db.commit()
    assert code_step(client, challenge, code).status_code == 401
    with database.SessionLocal() as db:
        assert db.query(models.OneTimeCode).count() == 0  # the expired row is gone
        assert twofactor.prune_codes(db) == 0


def test_sending_is_limited_per_account(client, monkeypatch):
    s = register(client)
    enable_sms(client, s)  # 1 send
    challenge = password_step(client).json()["challenge"]  # 2
    for _ in range(config.TWO_FACTOR_MAX_SENDS - 2):
        assert (
            client.post("/api/v1/auth/login/2fa/send", json={"challenge": challenge, "method": "sms"}).status_code
            == 200
        )
    r = client.post("/api/v1/auth/login/2fa/send", json={"challenge": challenge, "method": "sms"})
    assert r.status_code == 429 and "Too many codes" in r.json()["detail"] and "Retry-After" in r.headers
    # The password step still answers (with the reason) — a recovery code gets the user in.
    r = password_step(client)
    assert r.status_code == 202 and r.json()["sent"] is None and "Too many codes" in r.json()["send_error"]
    # Resend cooldown: the same code target cannot be spammed back to back.
    monkeypatch.setattr(config, "TWO_FACTOR_RESEND_SECONDS", 30)
    twofactor.SEND_LIMITER.limit = 100
    other = register(client, email="bob@example.com")
    r = setup(client, other, "email", email="bob@example.com")
    assert r.status_code == 200
    r = setup(client, other, "email", email="bob@example.com")
    assert r.status_code == 429 and "just sent" in r.json()["detail"]


# ---------------------------------------------------------------------------- providers


def test_provider_failure_is_reported_not_fatal(client, monkeypatch):
    s = register(client)
    enable_sms(client, s)

    def broken(self, to, body, subject=""):
        raise messaging.MessagingError("provider down")

    monkeypatch.setattr(messaging.SmsChannel, "send", broken)
    r = password_step(client)
    assert r.status_code == 202 and r.json()["sent"] is None and "provider down" in r.json()["send_error"]
    r = client.post("/api/v1/auth/login/2fa/send", json={"challenge": r.json()["challenge"], "method": "sms"})
    assert r.status_code == 503 and "could not be sent" in r.json()["detail"]
    with database.SessionLocal() as db:
        assert db.query(models.OneTimeCode).count() == 0  # no code row for a message that never left


def test_without_provider_or_fake_only_the_authenticator_is_offered(client, monkeypatch):
    monkeypatch.setattr(config, "MESSAGING_FAKE_PROVIDERS", False)
    s = register(client)
    status = client.get("/api/v1/me/2fa", headers=headers(s)).json()
    assert status["available"] == ["totp"] and status["fake_channels"] == []
    r = setup(client, s, "sms", PHONE)
    assert r.status_code == 503 and "not available" in r.json()["detail"]
    assert client.get("/api/v1/messages/fake/outbox").status_code == 404
    # Configured providers are offered (and are not fakes).
    monkeypatch.setattr(config, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(config, "TWILIO_ACCOUNT_SID", "AC123")
    monkeypatch.setattr(config, "TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setattr(config, "TWILIO_FROM", "+15005550006")
    status = client.get("/api/v1/me/2fa", headers=headers(s)).json()
    assert status["available"] == ["totp", "sms", "email"] and status["fake_channels"] == []


class StubSMTP:
    """Stands in for smtplib.SMTP: records what would have been sent."""

    sent: list = []
    fail = False

    def send_message(self, msg):
        if StubSMTP.fail:
            raise smtplib.SMTPRecipientsRefused({msg["To"]: (550, b"no such user")})
        StubSMTP.sent.append(msg)

    def quit(self):
        pass


def test_smtp_adapter_builds_a_proper_message(monkeypatch):
    monkeypatch.setattr(config, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(config, "EMAIL_FROM", "Personal Assistant <no-reply@example.com>")
    StubSMTP.sent, StubSMTP.fail = [], False
    channel = messaging.EmailChannel(smtp_factory=StubSMTP)
    assert channel.available and not channel.fake
    subject, body = twofactor._message("login", "email", "123456")
    message = channel.send("ann@example.com", body, subject)
    assert message.provider == "smtp" and messaging.OUTBOX.messages() == []  # the real path, not the fake
    mail = StubSMTP.sent[0]
    assert mail["To"] == "ann@example.com" and mail["From"] == "Personal Assistant <no-reply@example.com>"
    assert mail["Subject"] == "123456 is your Personal Assistant code" and mail["Auto-Submitted"] == "auto-generated"
    assert "123456" in mail.get_content() and "@example.com>" in mail["Message-ID"]
    StubSMTP.fail = True
    with pytest.raises(messaging.MessagingError, match="mail server"):
        channel.send("nobody@example.com", body, subject)


class StubTransport:
    def __init__(self, status=201, body=None):
        self.calls = []
        self.status, self.body = status, body or {"sid": "SM123"}

    def request(self, method, url, *, headers=None, data=None):
        self.calls.append((method, url, headers, data))
        return self.status, self.body


def test_twilio_adapter_posts_the_documented_request(monkeypatch):
    monkeypatch.setattr(config, "TWILIO_ACCOUNT_SID", "AC123")
    monkeypatch.setattr(config, "TWILIO_AUTH_TOKEN", "secret")
    monkeypatch.setattr(config, "TWILIO_FROM", "+15005550006")
    monkeypatch.setattr(config, "TWILIO_MESSAGING_SERVICE_SID", "")
    monkeypatch.setattr(config, "SMS_WEBOTP_HOST", "app.example.com")
    transport = StubTransport()
    channel = messaging.SmsChannel(transport=transport)
    _, body = twofactor._message("login", "sms", "654321")
    assert body.endswith(
        "\n\n@app.example.com #654321"
    )  # WebOTP line: Android/Chrome offers the code on that site only
    assert channel.send(E164, body).provider == "SM123"
    method, url, hdrs, data = transport.calls[0]
    assert method == "POST" and url == "https://api.twilio.com/2010-04-01/Accounts/AC123/Messages.json"
    assert hdrs["Authorization"] == "Basic " + base64.b64encode(b"AC123:secret").decode()
    form = {k: v[0] for k, v in parse_qs(data.decode()).items()}
    assert form == {"To": E164, "From": "+15005550006", "Body": body}
    monkeypatch.setattr(config, "TWILIO_MESSAGING_SERVICE_SID", "MG999")
    channel.send(E164, "x")
    form = {k: v[0] for k, v in parse_qs(transport.calls[1][3].decode()).items()}
    assert form["MessagingServiceSid"] == "MG999" and "From" not in form
    refused = messaging.SmsChannel(transport=StubTransport(400, {"message": "invalid 'To' number"}))
    with pytest.raises(messaging.MessagingError, match="invalid 'To' number"):
        refused.send("+1", "x")


def test_fake_outbox_pages(client):
    s = register(client)
    setup(client, s, "sms", PHONE)
    page = client.get("/api/v1/messages/fake/outbox")
    assert page.status_code == 200 and "Test inbox" in page.text and E164 in page.text
    assert 'name=viewport content="width=device-width' in page.text  # readable on a phone
    data = client.get("/api/v1/messages/fake/outbox.json", params={"to": E164}).json()["messages"]
    assert len(data) == 1 and data[0]["channel"] == "sms"
    assert client.get("/api/v1/messages/fake/outbox.json?channel=email").json()["messages"] == []
    assert client.post("/api/v1/messages/fake/outbox/clear").json() == {"ok": True}
    assert client.get("/api/v1/messages/fake/outbox.json").json()["messages"] == []
    assert (config.DATA_DIR / "message_outbox.json").is_file()  # `python dev.py outbox` reads this


# ---------------------------------------------------------------------------- support & deletion


def test_admin_sees_methods_and_masked_phone_and_reset_clears_them(client, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_EMAILS", {"admin@example.com"})
    admin = register(client, email="admin@example.com", password="Admin-pass-123")
    s = register(client)
    enable_sms(client, s)
    detail = client.get(f"/api/v1/admin/accounts/{s['account_id']}", headers=headers(admin)).json()
    assert detail["two_factor_methods"] == ["sms"] and detail["two_factor_phone"] == "+•••••••0100"
    assert E164 not in str(detail)  # support sees the last digits only
    password_step(client)  # leaves an outstanding login code
    r = client.post(f"/api/v1/admin/accounts/{s['account_id']}/2fa/reset", headers=headers(admin))
    assert r.status_code == 200 and r.json()["two_factor_methods"] == [] and r.json()["two_factor_phone"] is None
    with database.SessionLocal() as db:
        acc = db.get(models.Account, s["account_id"])
        assert acc.phone_number is None and acc.sms_otp_enabled_at is None
        assert db.query(models.OneTimeCode).count() == 0


def test_purge_forgets_phone_and_codes(client):
    s = register(client)
    enable_sms(client, s)
    client.post("/api/v1/me/2fa/send", json={"method": "sms"}, headers=headers(s))
    assert client.delete("/api/v1/me", headers=headers(s)).status_code == 204
    with database.SessionLocal() as db:
        acc = db.query(models.Account).one()
        assert acc.phone_number is None and acc.sms_otp_enabled_at is None
        assert db.query(models.OneTimeCode).count() == 0
