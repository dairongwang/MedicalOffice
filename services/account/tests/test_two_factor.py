"""Two-factor authentication: TOTP (RFC 6238) as the second login step, recovery codes,
password re-checks for sensitive changes, brute-force limits, administrator reset."""

from __future__ import annotations

import base64
import json
import time

import pytest
from conftest import auth_key_for, headers, login, register

import auth
import config
import database
import models
import totp
import twofactor
from ratelimit import FailureLimiter

EMAIL, PASSWORD = "ann@example.com", "correct horse"


@pytest.fixture(autouse=True)
def fresh_limiter(monkeypatch):
    monkeypatch.setattr(twofactor, "LIMITER", FailureLimiter(config.TWO_FACTOR_MAX_FAILURES, 300))


class Clock:
    """A TOTP code signs in once per 30-second step: tests move the clock on between steps."""

    def __init__(self):
        self.offset = 0.0

    def now(self) -> float:
        return time.time() + self.offset

    def next_step(self) -> None:
        self.offset += totp.STEP_SECONDS


@pytest.fixture(autouse=True)
def clock(monkeypatch) -> Clock:
    c = Clock()
    monkeypatch.setattr(totp, "_now", c.now)
    return c


def subset(d: dict, **expected) -> bool:
    return {k: d.get(k) for k in expected} == expected


def _secret_of(email: str = EMAIL, *, pending: bool = False) -> str:
    with database.SessionLocal() as db:
        acc = db.query(models.Account).filter_by(email=email).one()
        return (acc.totp_pending_secret if pending else acc.totp_secret) or ""


def _code(email: str = EMAIL, **kw) -> str:
    return totp.code_at(_secret_of(email, **kw))


def enable_2fa(client, session, email=EMAIL, password=PASSWORD) -> list[str]:
    """The happy path of turning it on: password → secret → first code → recovery codes."""
    r = client.post("/api/v1/me/2fa/setup", json={"auth_key": auth_key_for(password, email)}, headers=headers(session))
    assert r.status_code == 200, r.text
    setup = r.json()
    assert setup["secret"] == _secret_of(email, pending=True)
    assert (
        setup["otpauth_uri"].startswith("otpauth://totp/Personal%20Assistant%3A")
        and setup["secret"] in setup["otpauth_uri"]
    )
    assert setup["issuer"] == "Personal Assistant" and setup["account"] == email
    assert setup["digits"] == 6 and setup["period"] == 30 and setup["algorithm"] == "SHA1"
    r = client.post("/api/v1/me/2fa/enable", json={"code": totp.code_at(setup["secret"])}, headers=headers(session))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"]["enabled"] is True and body["status"]["recovery_codes_left"] == 10
    assert len(body["recovery_codes"]) == 10 and all(len(c) == 11 and c[5] == "-" for c in body["recovery_codes"])
    return body["recovery_codes"]


def password_step(client, email=EMAIL, password=PASSWORD):
    return client.post(
        "/api/v1/auth/login",
        json={"email": email, "auth_key": auth_key_for(password, email), "device": {"platform": "test"}},
    )


def code_step(client, challenge: str, code: str):
    return client.post(
        "/api/v1/auth/login/2fa", json={"challenge": challenge, "code": code, "device": {"platform": "web"}}
    )


# ---------------------------------------------------------------------------- the algorithm


def test_totp_matches_the_rfc_6238_reference_vectors():
    secret = base64.b32encode(b"12345678901234567890").decode()
    for at, expected in {
        59: "94287082",
        1111111109: "07081804",
        1234567890: "89005924",
        2000000000: "69279037",
    }.items():
        assert totp.hotp(secret, totp.current_step(at), digits=8) == expected
    assert [totp.hotp(secret, n) for n in range(3)] == ["755224", "287082", "359152"]  # RFC 4226


def test_totp_window_replay_and_normalisation():
    secret = totp.new_secret()
    now = 1_700_000_010  # start of a step
    code = totp.code_at(secret, now)
    step = totp.current_step(now)
    assert totp.verify(secret, code, now=now) == step
    assert totp.verify(secret, code, now=now + 59) == step and totp.verify(secret, code, now=now - 1) == step
    assert totp.verify(secret, code, now=now + 60) is None  # two steps off
    assert totp.verify(secret, code, now=now, last_step=step) is None  # already used
    assert totp.verify(secret, f"{code[:3]} {code[3:]}", now=now) == step  # "123 456"
    assert totp.verify(secret, code[:5], now=now) is None and totp.verify(secret, "abcdef", now=now) is None
    assert len(totp._secret_bytes(totp.new_secret())) == 20 and len(totp.new_secret()) == 32
    uri = totp.otpauth_uri("JBSWY3DPEHPK3PXP", "ann@example.com", "Personal Assistant")
    assert uri == (
        "otpauth://totp/Personal%20Assistant%3Aann%40example.com"
        "?secret=JBSWY3DPEHPK3PXP&issuer=Personal%20Assistant&algorithm=SHA1&digits=6&period=30"
    )


def test_recovery_codes_are_random_hashed_and_tolerant_to_formatting():
    codes = totp.new_recovery_codes()
    assert len(set(codes)) == 10 and all(set(c.replace("-", "")) <= set(totp.RECOVERY_ALPHABET) for c in codes)
    hashes = [totp.hash_recovery_code(c, "salt") for c in codes]
    assert totp.match_recovery_code(codes[2].upper().replace("-", " "), "salt", hashes) == hashes[2]
    assert totp.match_recovery_code(codes[2], "other-salt", hashes) is None
    assert totp.match_recovery_code("nope", "salt", hashes) is None


def test_failure_limiter_blocks_after_limit_and_recovers():
    lim = FailureLimiter(limit=3, window_seconds=100)
    for _ in range(3):
        assert not lim.blocked("a", now=10.0)
        lim.failed("a", now=10.0)
    assert lim.blocked("a", now=10.0) and not lim.blocked("b", now=10.0)
    assert lim.retry_after("a", now=50.0) == 61
    assert not lim.blocked("a", now=111.0)  # the window passed
    lim.failed("a", now=111.0)
    lim.reset("a")
    assert not lim.blocked("a", now=111.0) and lim.retry_after("a", now=111.0) == 0


# ---------------------------------------------------------------------------- enrolment


def test_off_by_default_and_status_in_me(client):
    s = register(client)
    me = client.get("/api/v1/me", headers=headers(s)).json()
    assert subset(me["two_factor"], enabled=False, enabled_at=None, recovery_codes_left=0, pending=False, methods=[])
    assert me["two_factor"]["available"] == ["totp", "sms", "email"]  # the built-in fakes stand in (test env)
    assert client.get("/api/v1/me/2fa", headers=headers(s)).json()["enabled"] is False
    # Without a second factor the login is exactly what it was.
    assert password_step(client).status_code == 200


def test_enable_disable_and_password_checks(client):
    s = register(client)
    # Setup needs the password (a leaked session token alone cannot lock the owner out).
    r = client.post("/api/v1/me/2fa/setup", json={"auth_key": "0" * 64}, headers=headers(s))
    assert r.status_code == 403 and "password" in r.json()["detail"].lower()
    # Enable without setup is refused; a wrong first code does not activate anything.
    assert client.post("/api/v1/me/2fa/enable", json={"code": "123456"}, headers=headers(s)).status_code == 409
    client.post("/api/v1/me/2fa/setup", json={"auth_key": auth_key_for(PASSWORD, EMAIL)}, headers=headers(s))
    assert subset(
        client.get("/api/v1/me/2fa", headers=headers(s)).json(),
        enabled=False,
        enabled_at=None,
        recovery_codes_left=0,
        pending=True,
    )
    r = client.post("/api/v1/me/2fa/enable", json={"code": "000000"}, headers=headers(s))
    assert r.status_code == 403 and "does not match" in r.json()["detail"]  # 403, never 401: the session stays
    assert password_step(client).status_code == 200  # still password-only
    # A second setup replaces the pending secret (mis-scanned QR code: start over).
    first = _secret_of(pending=True)
    client.post("/api/v1/me/2fa/setup", json={"auth_key": auth_key_for(PASSWORD, EMAIL)}, headers=headers(s))
    assert _secret_of(pending=True) != first

    codes = enable_2fa(client, s)
    status = client.get("/api/v1/me/2fa", headers=headers(s)).json()
    assert status["enabled"] and status["enabled_at"] and status["recovery_codes_left"] == 10 and not status["pending"]
    assert client.get("/api/v1/me", headers=headers(s)).json()["two_factor"]["enabled"] is True
    # Already on: a second set-up means "change the app" and needs a current code (test_two_factor_change).
    r = client.post("/api/v1/me/2fa/setup", json={"auth_key": auth_key_for(PASSWORD, EMAIL)}, headers=headers(s))
    assert r.status_code == 403 and "current code" in r.json()["detail"]
    assert client.post("/api/v1/me/2fa/enable", json={"code": _code()}, headers=headers(s)).status_code == 409
    # Existing sessions keep working.
    assert client.get("/api/v1/me", headers=headers(s)).status_code == 200

    # Turning off needs password AND a code.
    r = client.post("/api/v1/me/2fa/disable", json={"auth_key": "0" * 64, "code": _code()}, headers=headers(s))
    assert r.status_code == 403
    r = client.post(
        "/api/v1/me/2fa/disable", json={"auth_key": auth_key_for(PASSWORD, EMAIL), "code": "000000"}, headers=headers(s)
    )
    assert r.status_code == 403 and "not valid" in r.json()["detail"]
    assert client.get("/api/v1/me/2fa", headers=headers(s)).json()["enabled"] is True
    r = client.post(
        "/api/v1/me/2fa/disable", json={"auth_key": auth_key_for(PASSWORD, EMAIL), "code": codes[0]}, headers=headers(s)
    )
    assert r.status_code == 200 and subset(
        r.json(), enabled=False, enabled_at=None, recovery_codes_left=0, pending=False, methods=[]
    )
    assert password_step(client).status_code == 200  # password-only again
    with database.SessionLocal() as db:
        acc = db.query(models.Account).filter_by(email=EMAIL).one()
        assert acc.totp_secret is None and acc.recovery_codes == "[]" and acc.totp_last_step == 0
    r = client.post(
        "/api/v1/me/2fa/disable", json={"auth_key": auth_key_for(PASSWORD, EMAIL), "code": "123456"}, headers=headers(s)
    )
    assert r.status_code == 409  # not on


# ---------------------------------------------------------------------------- login


def test_login_needs_the_second_factor(client, clock):
    s = register(client)
    codes = enable_2fa(client, s)
    clock.next_step()  # the code that activated 2FA cannot also sign in (replay guard)
    # Step 1: the password alone yields a challenge, not a session — and nothing secret.
    r = password_step(client)
    assert r.status_code == 202, r.text
    challenge = r.json()
    assert challenge["two_factor_required"] is True and challenge["expires_in"] == 300
    assert challenge["methods"] == ["totp", "recovery"]
    assert "token" not in challenge and "wrapped_data_key" not in challenge
    me = client.get("/api/v1/me", headers=headers(s)).json()
    assert len(me["devices"]) == 1  # no device registered by step 1
    # The wrong password still fails at step 1, same message as before.
    assert password_step(client, password="wrong").status_code == 401
    # The challenge is not a session token.
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {challenge['challenge']}"}).status_code == 401
    # Step 2 with a wrong code.
    r = code_step(client, challenge["challenge"], "000000")
    assert r.status_code == 401 and "not valid" in r.json()["detail"]
    # Step 2 with the authenticator's code: a full session, like a password-only login.
    r = code_step(client, challenge["challenge"], _code())
    assert r.status_code == 200, r.text
    s2 = r.json()
    assert s2["wrapped_data_key"] == s["wrapped_data_key"] and s2["token"] and s2["device_id"] != s["device_id"]
    me = client.get("/api/v1/me", headers=headers(s2)).json()
    assert len(me["devices"]) == 2 and me["two_factor"]["recovery_codes_left"] == 10
    # The same code cannot sign in twice (replay inside its 90-second window) — the next one can.
    r2 = password_step(client).json()
    assert code_step(client, r2["challenge"], _code()).status_code == 401
    clock.next_step()
    assert code_step(client, r2["challenge"], _code()).status_code == 200
    clock.next_step()
    # A recovery code works once — and is reported as used.
    r = code_step(client, r2["challenge"], codes[3].upper())
    assert r.status_code == 200, r.text
    assert client.get("/api/v1/me/2fa", headers=headers(r.json())).json()["recovery_codes_left"] == 9
    r3 = password_step(client).json()
    assert code_step(client, r3["challenge"], codes[3]).status_code == 401
    assert code_step(client, r3["challenge"], codes[3].replace("-", " ").upper()).status_code == 401
    # Garbage / expired / foreign challenges.
    assert code_step(client, "not-a-challenge-at-all", _code()).status_code == 401
    assert code_step(client, s["token"], _code()).status_code == 401  # a session token is not a challenge
    stale = auth.sign({"typ": "2fa", "sub": s["account_id"], "exp": 1})
    r = code_step(client, stale, _code())
    assert r.status_code == 401 and "no longer valid" in r.json()["detail"]


def test_wrong_codes_are_rate_limited_per_account(client, clock):
    s = register(client)
    enable_2fa(client, s)
    other = register(client, email="bob@example.com")
    enable_2fa(client, other, email="bob@example.com")
    clock.next_step()
    challenge = password_step(client).json()["challenge"]
    for _ in range(config.TWO_FACTOR_MAX_FAILURES):
        assert code_step(client, challenge, "000000").status_code == 401
    r = code_step(client, challenge, _code())  # even the right code: locked for the window
    assert r.status_code == 429 and "Retry-After" in r.headers and "Too many" in r.json()["detail"]
    # Another account is not affected; the block lifts when the window passes.
    bob = password_step(client, email="bob@example.com").json()["challenge"]
    assert code_step(client, bob, _code("bob@example.com")).status_code == 200
    twofactor.LIMITER.reset(s["account_id"])
    assert code_step(client, challenge, _code()).status_code == 200
    clock.next_step()
    # Password re-checks on sensitive actions are limited the same way.
    for _ in range(config.TWO_FACTOR_MAX_FAILURES):
        r = client.post("/api/v1/me/2fa/disable", json={"auth_key": "0" * 64, "code": "000000"}, headers=headers(s))
        assert r.status_code == 403
    r = client.post(
        "/api/v1/me/2fa/disable", json={"auth_key": auth_key_for(PASSWORD, EMAIL), "code": _code()}, headers=headers(s)
    )
    assert r.status_code == 429


def test_new_recovery_codes_replace_the_old_ones(client):
    s = register(client)
    old = enable_2fa(client, s)
    r = client.post(
        "/api/v1/me/2fa/recovery-codes",
        json={"auth_key": auth_key_for(PASSWORD, EMAIL), "code": old[0]},
        headers=headers(s),
    )
    assert r.status_code == 200, r.text
    new = r.json()["recovery_codes"]
    assert len(new) == 10 and not set(new) & set(old) and r.json()["status"]["recovery_codes_left"] == 10
    challenge = password_step(client).json()["challenge"]
    assert code_step(client, challenge, old[1]).status_code == 401  # old set is dead
    assert code_step(client, challenge, new[0]).status_code == 200
    r = client.post(
        "/api/v1/me/2fa/recovery-codes",
        json={"auth_key": auth_key_for(PASSWORD, EMAIL), "code": old[2]},
        headers=headers(s),
    )
    assert r.status_code == 403  # wrong code on a signed-in endpoint: 403, the session stays
    assert (
        client.post(
            "/api/v1/me/2fa/recovery-codes", json={"auth_key": "0" * 64, "code": new[1]}, headers=headers(s)
        ).status_code
        == 403
    )
    # Off → no codes to regenerate.
    client.post(
        "/api/v1/me/2fa/disable", json={"auth_key": auth_key_for(PASSWORD, EMAIL), "code": new[2]}, headers=headers(s)
    )
    r = client.post(
        "/api/v1/me/2fa/recovery-codes",
        json={"auth_key": auth_key_for(PASSWORD, EMAIL), "code": new[3]},
        headers=headers(s),
    )
    assert r.status_code == 409


def test_server_keeps_hashes_only_and_audits(client, clock):
    s = register(client)
    codes = enable_2fa(client, s)
    clock.next_step()
    with database.SessionLocal() as db:
        acc = db.query(models.Account).filter_by(email=EMAIL).one()
        stored = json.loads(acc.recovery_codes)
        assert len(stored) == 10 and not set(stored) & set(codes) and all(len(h) == 64 for h in stored)
        assert acc.totp_secret and acc.totp_pending_secret is None and acc.totp_enabled_at
    challenge = password_step(client).json()["challenge"]
    code_step(client, challenge, codes[0])
    client.post(
        "/api/v1/me/2fa/disable", json={"auth_key": auth_key_for(PASSWORD, EMAIL), "code": _code()}, headers=headers(s)
    )
    with database.SessionLocal() as db:
        actions = [row.action for row in db.query(models.AuditLog).order_by(models.AuditLog.id)]
    assert actions == ["2fa.enable", "2fa.recovery_used", "2fa.disable"]


def test_purge_forgets_the_second_factor(client):
    s = register(client)
    enable_2fa(client, s)
    assert client.delete("/api/v1/me", headers=headers(s)).status_code == 204
    with database.SessionLocal() as db:
        acc = db.query(models.Account).one()
        assert acc.totp_secret is None and acc.recovery_codes == "[]" and acc.totp_enabled_at is None


# ---------------------------------------------------------------------------- administrators


def test_admin_sees_state_and_can_reset(client, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_EMAILS", {"admin@example.com"})
    admin = register(client, email="admin@example.com", password="Admin-pass-123")
    s = register(client)
    enable_2fa(client, s)
    listed = {a["email"]: a for a in client.get("/api/v1/admin/accounts", headers=headers(admin)).json()["items"]}
    assert listed[EMAIL]["two_factor_enabled"] is True and listed["admin@example.com"]["two_factor_enabled"] is False
    # Users cannot reach the reset; administrators can (the account is signed out everywhere).
    r = client.post(f"/api/v1/admin/accounts/{s['account_id']}/2fa/reset", headers=headers(s))
    assert r.status_code == 403
    r = client.post(f"/api/v1/admin/accounts/{s['account_id']}/2fa/reset", headers=headers(admin))
    assert r.status_code == 200, r.text
    assert r.json()["two_factor_enabled"] is False and r.json()["devices_active"] == 0
    assert r.json()["audit"][0]["action"] == "2fa.reset" and r.json()["audit"][0]["actor_email"] == "admin@example.com"
    assert client.get("/api/v1/me", headers=headers(s)).status_code == 401
    assert password_step(client).status_code == 200  # the password alone signs in again
    r = client.post(f"/api/v1/admin/accounts/{s['account_id']}/2fa/reset", headers=headers(admin))
    assert r.status_code == 409  # nothing to reset


def test_admin_with_two_factor_signs_in_through_both_steps(client, monkeypatch, clock):
    monkeypatch.setattr(config, "ADMIN_EMAILS", {"admin@example.com"})
    admin = register(client, email="admin@example.com", password="Admin-pass-123")
    enable_2fa(client, admin, email="admin@example.com", password="Admin-pass-123")
    clock.next_step()
    r = password_step(client, email="admin@example.com", password="Admin-pass-123")
    assert r.status_code == 202
    session = code_step(client, r.json()["challenge"], _code("admin@example.com")).json()
    assert client.get("/api/v1/admin/stats", headers=headers(session)).status_code == 200
    # The docs gate (prod): its sign-in page knows the second step, and accepts the session
    # obtained through it like any other.
    monkeypatch.setattr(config, "DOCS_REQUIRE_ADMIN", True)
    gate = client.get("/api/docs")
    assert gate.status_code == 401 and "/api/v1/auth/login/2fa" in gate.text and 'id="code"' in gate.text
    assert client.post("/api/docs/login", json={"token": session["token"]}).status_code == 200
    assert client.get("/api/docs").status_code == 200


def test_openapi_documents_the_challenge(client):
    spec = client.get("/api/openapi.json").json()
    login_op = spec["paths"]["/api/v1/auth/login"]["post"]
    assert "202" in login_op["responses"] and "200" in login_op["responses"]
    assert "/api/v1/auth/login/2fa" in spec["paths"]
    for path in ("/api/v1/me/2fa", "/api/v1/me/2fa/setup", "/api/v1/me/2fa/enable", "/api/v1/me/2fa/disable"):
        assert path in spec["paths"]
    s = register(client)
    assert login(client)["token"]  # the conftest helper is untouched for accounts without 2FA
    assert s["token"]
