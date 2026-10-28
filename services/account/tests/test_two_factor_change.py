"""Changing a second factor that is already on — a new phone number, another e-mail address
for the codes, a new authenticator app — and who may do it: the signed-in owner with the
password AND a current code, plus proof of the new destination; the old one is told."""

from __future__ import annotations

import re

import pytest
from conftest import auth_key_for, headers, register
from test_two_factor_channels import E164, code_step, enable_sms, last_code, password_step

import config
import database
import messaging
import models
import totp
import twofactor

EMAIL, PASSWORD = "ann@example.com", "correct horse"
NEW_PHONE, NEW_E164 = "+49 151 2345 6789", "+4915123456789"
NEW_MASK = "+•••••••••6789"


@pytest.fixture(autouse=True)
def no_resend_wait(client, monkeypatch):  # after `client`, which builds fresh limiters
    monkeypatch.setattr(config, "TWO_FACTOR_RESEND_SECONDS", 0)
    monkeypatch.setattr(twofactor.SEND_LIMITER, "limit", 100)  # these flows send many codes on purpose


def setup(client, s, method, *, code=None, phone=None, email=None, password=PASSWORD):
    return client.post(
        "/api/v1/me/2fa/setup",
        json={
            "auth_key": auth_key_for(password, EMAIL),
            "method": method,
            "phone": phone,
            "email": email,
            "code": code,
        },
        headers=headers(s),
    )


def verify_code(client, s, channel="sms") -> str:
    """A current code for the 'verify' purpose, the way the account screen gets one."""
    assert client.post("/api/v1/me/2fa/send", json={"method": channel}, headers=headers(s)).status_code == 200
    return last_code(E164 if channel == "sms" else EMAIL)


def test_change_phone_number_needs_password_and_a_current_code_and_proof_of_the_new_number(client):
    s = register(client)
    enable_sms(client, s)
    # 1. Without a current code the change does not even start; a wrong password / wrong code neither.
    r = setup(client, s, "sms", phone=NEW_PHONE)
    assert r.status_code == 403 and "current code" in r.json()["detail"]
    r = setup(client, s, "sms", phone=NEW_PHONE, code=verify_code(client, s), password="wrong")
    assert r.status_code == 403 and "password" in r.json()["detail"].lower()
    r = setup(client, s, "sms", phone=NEW_PHONE, code="000000")
    assert r.status_code == 403 and "not valid" in r.json()["detail"]
    assert messaging.OUTBOX.latest(to=NEW_E164) is None  # nothing reached the new number yet
    # 2. Password + current code: a code goes to the NEW number; the old one still receives sign-in codes.
    r = setup(client, s, "sms", phone=NEW_PHONE, code=verify_code(client, s))
    assert r.status_code == 200, r.text
    assert r.json()["replacing"] is True and r.json()["delivery"]["destination"] == NEW_MASK
    new_code = last_code(NEW_E164)
    with database.SessionLocal() as db:
        assert db.query(models.Account).one().phone_number == E164  # unchanged until confirmed
    # The same number again is refused; a wrong confirmation code changes nothing.
    r = setup(client, s, "sms", phone="+1 415 555 0100", code=verify_code(client, s))
    assert r.status_code == 422 and "already the number" in r.json()["detail"]
    r = client.post("/api/v1/me/2fa/enable", json={"code": "000000", "method": "sms"}, headers=headers(s))
    assert r.status_code == 403
    # 3. The code from the new phone swaps the number; recovery codes stay; both old destinations are told.
    messaging.OUTBOX.clear()
    r = client.post("/api/v1/me/2fa/enable", json={"code": new_code, "method": "sms"}, headers=headers(s))
    assert r.status_code == 200, r.text
    assert r.json()["recovery_codes"] == [] and r.json()["status"]["phone"] == NEW_MASK
    assert r.json()["status"]["recovery_codes_left"] == 10
    notices = {(m["channel"], m["to"]) for m in messaging.OUTBOX.messages()}
    assert notices == {("email", EMAIL), ("sms", E164)}
    assert "was just changed" in messaging.OUTBOX.latest(to=E164)["body"]
    # 4. Sign-in codes now go to the new number only.
    messaging.OUTBOX.clear()
    challenge = password_step(client).json()
    assert challenge["destinations"]["sms"] == NEW_MASK and messaging.OUTBOX.latest(to=E164) is None
    assert code_step(client, challenge["challenge"], last_code(NEW_E164)).status_code == 200
    with database.SessionLocal() as db:
        actions = [a.action for a in db.query(models.AuditLog).order_by(models.AuditLog.id)]
    assert actions[-1] == "2fa.method_changed"


def test_change_the_email_address_for_codes_and_back_to_the_account_address(client):
    s = register(client)
    enable_sms(client, s)
    # Turn e-mail codes on for another address than the sign-in e-mail from the start.
    r = setup(client, s, "email", email="Codes@Other.example")
    assert r.status_code == 200 and r.json()["delivery"]["destination"] == "c•••@other.example"
    r = client.post(
        "/api/v1/me/2fa/enable", json={"code": last_code("codes@other.example"), "method": "email"}, headers=headers(s)
    )
    assert r.status_code == 200 and r.json()["status"]["email"] == "c•••@other.example"
    assert setup(client, s, "email", email="nonsense").status_code == 403  # needs a current code (it is on)
    r = setup(client, s, "email", email="nonsense", code=verify_code(client, s))
    assert r.status_code == 422 and "valid e-mail" in r.json()["detail"]
    # Change it to a third address: password + current code (here by text) + code at the new address.
    r = setup(client, s, "email", email="third@example.net", code=verify_code(client, s))
    assert r.status_code == 200 and r.json()["replacing"]
    r = setup(client, s, "email", email="third@example.net", code=verify_code(client, s))  # resend: newest counts
    code = last_code("third@example.net")
    messaging.OUTBOX.clear()
    r = client.post("/api/v1/me/2fa/enable", json={"code": code, "method": "email"}, headers=headers(s))
    assert r.status_code == 200 and r.json()["status"]["email"] == "t•••@example.net"
    told = {m["to"] for m in messaging.OUTBOX.messages(channel="email")}
    assert told == {EMAIL, "codes@other.example"}  # the sign-in address and the previous codes address
    # Back to the account e-mail itself: stored as "the account address", not a copy of it.
    r = setup(client, s, "email", email=EMAIL, code=verify_code(client, s))
    assert r.status_code == 200
    client.post("/api/v1/me/2fa/enable", json={"code": last_code(EMAIL), "method": "email"}, headers=headers(s))
    with database.SessionLocal() as db:
        acc = db.query(models.Account).one()
        assert acc.otp_email is None and acc.email_otp_enabled_at
    # Removing the e-mail method forgets the separate address.
    r = setup(client, s, "email", email="again@example.net", code=verify_code(client, s))
    client.post(
        "/api/v1/me/2fa/enable", json={"code": last_code("again@example.net"), "method": "email"}, headers=headers(s)
    )
    client.post(
        "/api/v1/me/2fa/disable",
        json={"auth_key": auth_key_for(PASSWORD, EMAIL), "code": verify_code(client, s), "method": "email"},
        headers=headers(s),
    )
    with database.SessionLocal() as db:
        acc = db.query(models.Account).one()
        assert acc.otp_email is None and acc.email_otp_enabled_at is None


def test_change_authenticator_app(client, monkeypatch):
    s = register(client)
    r = setup(client, s, "totp")
    old_secret = r.json()["secret"]
    client.post("/api/v1/me/2fa/enable", json={"code": totp.code_at(old_secret), "method": "totp"}, headers=headers(s))
    monkeypatch.setattr(totp, "_now", lambda: totp.time.time() + 30)  # next step: the old code was used
    # A new app: password + a current code from the OLD app (or a recovery code) start the change.
    assert setup(client, s, "totp").status_code == 403
    r = setup(client, s, "totp", code=totp.code_at(old_secret))
    assert r.status_code == 200 and r.json()["replacing"] and r.json()["secret"] != old_secret
    new_secret = r.json()["secret"]
    with database.SessionLocal() as db:
        acc = db.query(models.Account).one()
        assert acc.totp_secret == old_secret and acc.totp_pending_secret == new_secret  # old app still valid
    # Confirmed with a code from the NEW app: the old app stops working, the new one signs in.
    monkeypatch.setattr(totp, "_now", lambda: totp.time.time() + 60)
    messaging.OUTBOX.clear()
    r = client.post(
        "/api/v1/me/2fa/enable", json={"code": totp.code_at(new_secret), "method": "totp"}, headers=headers(s)
    )
    assert r.status_code == 200 and r.json()["status"]["methods"] == ["totp"]
    assert "authenticator app" in messaging.OUTBOX.latest(to=EMAIL)["body"]  # the account e-mail is told
    monkeypatch.setattr(totp, "_now", lambda: totp.time.time() + 90)
    challenge = password_step(client).json()["challenge"]
    assert code_step(client, challenge, totp.code_at(old_secret)).status_code == 401
    assert code_step(client, challenge, totp.code_at(new_secret)).status_code == 200
    with database.SessionLocal() as db:
        assert db.query(models.AuditLog).filter_by(action="2fa.method_changed").count() == 1


def test_recovery_code_may_authorise_a_change_when_the_phone_is_gone(client):
    s = register(client)
    codes = enable_sms(client, s)
    r = setup(client, s, "sms", phone=NEW_PHONE, code=codes[0])
    assert r.status_code == 200
    r = client.post("/api/v1/me/2fa/enable", json={"code": last_code(NEW_E164), "method": "sms"}, headers=headers(s))
    assert r.status_code == 200 and r.json()["status"]["phone"] == NEW_MASK
    assert r.json()["status"]["recovery_codes_left"] == 9
    assert re.fullmatch(r"[a-z0-9]{5}-[a-z0-9]{5}", codes[1])


def test_resend_of_a_pending_confirmation_code(client):
    s = register(client)
    assert client.post("/api/v1/me/2fa/resend", headers=headers(s)).status_code == 409  # nothing pending
    setup(client, s, "sms", phone=NEW_PHONE)
    first = last_code(NEW_E164)
    r = client.post("/api/v1/me/2fa/resend", headers=headers(s))
    assert r.status_code == 200 and r.json()["destination"] == NEW_MASK
    second = last_code(NEW_E164)
    if first != second:  # the newest code counts
        assert (
            client.post("/api/v1/me/2fa/enable", json={"code": first, "method": "sms"}, headers=headers(s)).status_code
            == 403
        )
    assert (
        client.post("/api/v1/me/2fa/enable", json={"code": second, "method": "sms"}, headers=headers(s)).status_code
        == 200
    )
