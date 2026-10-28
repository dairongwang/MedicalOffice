"""Two-factor authentication (optional per account), with three methods the user can combine:

    totp   an authenticator app (Google Authenticator, Authy, 1Password, …) — RFC 6238, totp.py
    sms    a 6-digit code by text message to a phone number the user confirmed
    email  a 6-digit code by e-mail to the account's address (confirmed the same way)

plus ten one-time **recovery codes**, issued when the first method is turned on.

Why it exists in a zero-knowledge design: the password alone unlocks *two* things at login —
the session and the ``wrapped_data_key``. Somebody who learns the password (phishing, reuse,
a leak elsewhere) could fetch the wrapped key and unwrap it offline. With a second factor the
login never reaches that point without the user's phone, inbox or authenticator
(PLATFORM_DESIGN.md §3.2).

Flow
  1. ``POST /auth/login`` with the right password → when the account has a second factor the
     answer is ``202`` with a short-lived *challenge* (auth.issue_challenge) instead of a session.
     No device is registered, nothing is revealed (not even the wrapped key). If the preferred
     method is sms/email, the code is sent right away (``sent``).
  2. ``POST /auth/login/2fa/send`` — (re)send a code by the other channel, if the user has it.
  3. ``POST /auth/login/2fa`` with the challenge + a code (any enabled method) or a recovery code
     → the session, exactly as a password-only login would have returned it.

Turning a method on is two steps — ``setup`` (password re-check; a QR code, or a code sent to
the address/number being added) and ``enable`` (the right code proves the app/phone/inbox is
really the user's) — so nobody is locked out by a typo in a phone number. Removing a method,
turning everything off and new recovery codes need the password *and* a current code: a
stolen session token alone cannot weaken the account. Administrators can reset the second
factor for a user who lost every method and the recovery codes (audited, admin.py).

Sent codes (``one_time_codes``): one outstanding per account and purpose (login · setup ·
verify), a keyed hash only, valid TWO_FACTOR_CODE_TTL_SECONDS, at most TWO_FACTOR_MAX_ATTEMPTS
tries, deleted when used. Sending is limited per account (TWO_FACTOR_MAX_SENDS per window, one
per TWO_FACTOR_RESEND_SECONDS) — texts cost money and must not be a spam cannon.

Status codes: a wrong code at sign-in is 401 like a wrong password; on the signed-in endpoints
a wrong code or password is 403 — the clients drop their session on any 401, and a typo must
not sign anybody out.

Abuse: wrong codes are counted per account (ratelimit.FailureLimiter); after
TWO_FACTOR_MAX_FAILURES within the window the account's second factor answers 429 until the
window passes. A TOTP code signs in once (totp.verify's replay guard); a recovery code or a sent
code is deleted when used.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

import accounts
import auth
import config
import messaging
import models
import schemas
import totp
from ratelimit import FailureLimiter, RateLimiter

log = logging.getLogger("account.twofactor")

# Wrong codes / wrong password re-checks, per account.
LIMITER = FailureLimiter(config.TWO_FACTOR_MAX_FAILURES, config.TWO_FACTOR_FAILURE_WINDOW_SECONDS)
# Codes sent by e-mail / text, per account.
SEND_LIMITER = RateLimiter(config.TWO_FACTOR_MAX_SENDS, config.TWO_FACTOR_SEND_WINDOW_SECONDS)
MAX_ATTEMPTS = 5  # wrong tries per sent code before it is void

METHOD_TOTP = "totp"
METHOD_SMS = "sms"
METHOD_EMAIL = "email"
METHOD_RECOVERY = "recovery"
METHODS = (METHOD_TOTP, METHOD_SMS, METHOD_EMAIL)  # also the preference order at sign-in
LABELS = {METHOD_TOTP: "authenticator app", METHOD_SMS: "text message", METHOD_EMAIL: "e-mail"}

PURPOSE_LOGIN = "login"
PURPOSE_SETUP = "setup"
PURPOSE_VERIFY = "verify"

MSG_BAD_CODE = (
    "That code is not valid. Enter the current code from your authenticator app, text message or e-mail, "
    "or one of your recovery codes."
)
MSG_BAD_PASSWORD = "Your password is incorrect."
MSG_STALE_CHALLENGE = "This sign-in is no longer valid. Enter your e-mail and password again."


# ---------------------------------------------------------------------------- helpers


def normalize_phone(text: str | None) -> str | None:
    """What the user typed → E.164 (``+4915112345678``), or None. International format is
    required (a leading ``+`` or ``00``); spaces, dashes, dots and brackets are ignored.
    Deliberately dependency-free: the provider validates the number again when sending."""
    if not text:
        return None
    raw = "".join(ch for ch in text.strip() if ch not in " -.()/\u00a0\u2011\u2013")
    if raw.startswith("00"):
        raw = "+" + raw[2:]
    if not raw.startswith("+"):
        return None
    digits = raw[1:]
    if not digits.isdigit() or not 8 <= len(digits) <= 15 or digits[0] == "0":
        return None
    return "+" + digits


def mask_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    return "+" + "•" * max(len(phone) - 5, 2) + phone[-4:]


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:1]}•••@{domain}" if domain else email


def _codes(account: models.Account) -> list[str]:
    try:
        codes = json.loads(account.recovery_codes or "[]")
    except ValueError:
        codes = []
    return [c for c in codes if isinstance(c, str)]


def enabled_methods(account: models.Account) -> list[str]:
    on = {
        METHOD_TOTP: bool(account.totp_secret),
        METHOD_SMS: bool(account.sms_otp_enabled_at and account.phone_number),
        METHOD_EMAIL: bool(account.email_otp_enabled_at),
    }
    return [m for m in METHODS if on[m]]


def _destination(account: models.Account, method: str) -> str:
    return (account.phone_number or "") if method == METHOD_SMS else (account.otp_email or account.email)


_EMAIL_SHAPE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email(text: str | None) -> str | None:
    value = (text or "").strip().lower()
    return value if value and len(value) <= 320 and _EMAIL_SHAPE.match(value) else None


def _masked_destinations(account: models.Account, methods: list[str]) -> dict[str, str]:
    out = {}
    if METHOD_SMS in methods:
        out[METHOD_SMS] = mask_phone(account.phone_number) or ""
    if METHOD_EMAIL in methods:
        out[METHOD_EMAIL] = mask_email(account.email)
    return out


def available_methods() -> list[str]:
    return [METHOD_TOTP] + [m for m in (METHOD_SMS, METHOD_EMAIL) if messaging.channel(m).available]


def status(account: models.Account) -> schemas.TwoFactorStatus:
    methods = enabled_methods(account)
    dates = [d for d in (account.totp_enabled_at, account.sms_otp_enabled_at, account.email_otp_enabled_at) if d]
    return schemas.TwoFactorStatus(
        enabled=bool(methods),
        enabled_at=min(dates) if methods and dates else None,
        recovery_codes_left=len(_codes(account)) if methods else 0,
        pending=bool(account.totp_pending_secret) and METHOD_TOTP not in methods,
        methods=methods,
        phone=mask_phone(account.phone_number) if METHOD_SMS in methods else None,
        email=mask_email(_destination(account, METHOD_EMAIL)),
        available=available_methods(),
        fake_channels=[c for c in messaging.CHANNELS if messaging.channel(c).fake],
    )


def _too_many(key: str) -> HTTPException:
    wait = LIMITER.retry_after(key)
    return HTTPException(
        status_code=429,
        detail=f"Too many wrong attempts. Try again in {wait} second{'s' if wait != 1 else ''}.",
        headers={"Retry-After": str(wait)},
    )


# ---------------------------------------------------------------------------- sent codes


def _code_hash(account: models.Account, purpose: str, code: str) -> str:
    """Keyed (TOKEN_SECRET): a six-digit code hashed without a key is reversed in a millisecond."""
    msg = f"{account.id}:{purpose}:{totp.normalize_code(code)}".encode()
    return hmac.new(config.TOKEN_SECRET.encode(), msg, hashlib.sha256).hexdigest()


def _message(purpose: str, channel: str, code: str) -> tuple[str, str]:
    """(subject, body) of the message carrying ``code``."""
    minutes = max(1, config.TWO_FACTOR_CODE_TTL_SECONDS // 60)
    issuer = config.TWO_FACTOR_ISSUER
    what = {
        PURPOSE_LOGIN: "sign in",
        PURPOSE_SETUP: f"turn on two-factor codes by {LABELS[channel]}",
        PURPOSE_VERIFY: "confirm a change to your sign-in security",
    }[purpose]
    if channel == METHOD_SMS:
        body = f"{code} is your {issuer} code to {what}. Valid {minutes} min. Never share it."
        if config.SMS_WEBOTP_HOST:  # WebOTP (Android/Chrome): lets the browser offer the code — only on that site
            body += f"\n\n@{config.SMS_WEBOTP_HOST} #{code}"
        return "", body
    subject = f"{code} is your {issuer} code"
    body = (
        f"Your code to {what}:\n\n    {code}\n\nIt is valid for {minutes} minutes and works once.\n\n"
        f"If this was not you, somebody knows your password: change it and review your devices "
        f"in the app (Account & sync). Never share this code — {issuer} will never ask you for it.\n"
    )
    return subject, body


def send_code(
    db: Session, account: models.Account, purpose: str, channel: str, destination: str
) -> schemas.CodeDelivery:
    """Create (replace) the outstanding code for ``purpose`` and send it. Caller commits —
    except that the code row is committed before sending, so a slow provider cannot leave a
    message out there whose code the database does not know."""
    key = account.id
    now = models.utcnow()
    row = db.execute(
        select(models.OneTimeCode).where(
            models.OneTimeCode.account_id == account.id, models.OneTimeCode.purpose == purpose
        )
    ).scalar_one_or_none()
    if row is not None and row.channel == channel and row.destination == destination:
        wait = int((row.created_at + timedelta(seconds=config.TWO_FACTOR_RESEND_SECONDS) - now).total_seconds())
        if wait > 0:
            raise HTTPException(
                status_code=429,
                detail=f"A code was just sent. You can ask for another one in {wait} s.",
                headers={"Retry-After": str(wait)},
            )
    if not SEND_LIMITER.allow(key):
        raise HTTPException(
            status_code=429,
            detail="Too many codes requested. Wait a few minutes, or use another method or a recovery code.",
            headers={"Retry-After": str(config.TWO_FACTOR_RESEND_SECONDS)},
        )
    via = messaging.channel(channel)
    if not via.available:
        raise HTTPException(status_code=503, detail=f"{via.label} codes are not available on this server.")
    code = f"{secrets.randbelow(10**6):06d}"
    if row is None:
        row = models.OneTimeCode(account_id=account.id, purpose=purpose)
        db.add(row)
    row.channel = channel
    row.destination = destination
    row.code_hash = _code_hash(account, purpose, code)
    row.attempts = 0
    row.created_at = now
    row.expires_at = now + timedelta(seconds=config.TWO_FACTOR_CODE_TTL_SECONDS)
    db.commit()
    subject, body = _message(purpose, channel, code)
    try:
        via.send(destination, body, subject)
    except messaging.MessagingError as exc:
        db.delete(row)
        db.commit()
        raise HTTPException(
            status_code=503, detail=f"The code could not be sent right now ({exc}). Try again or use another method."
        ) from exc
    return schemas.CodeDelivery(
        method=channel,
        destination=mask_phone(destination) if channel == METHOD_SMS else mask_email(destination),
        expires_in=config.TWO_FACTOR_CODE_TTL_SECONDS,
        resend_after=config.TWO_FACTOR_RESEND_SECONDS,
        fake_outbox_url=messaging.outbox_url() if via.fake else None,
    )


def _check_sent_code(
    db: Session, account: models.Account, purpose: str, code: str, *, channels: tuple[str, ...]
) -> models.OneTimeCode | None:
    """The outstanding code row if ``code`` matches (the row is *not* deleted here), else None.
    A wrong guess counts against the row (committed at once — it must survive the error)."""
    row = db.execute(
        select(models.OneTimeCode).where(
            models.OneTimeCode.account_id == account.id, models.OneTimeCode.purpose == purpose
        )
    ).scalar_one_or_none()
    if row is None or row.channel not in channels:
        return None
    if row.expires_at < models.utcnow() or row.attempts >= MAX_ATTEMPTS:
        db.delete(row)
        db.commit()
        return None
    if hmac.compare_digest(row.code_hash, _code_hash(account, purpose, code)):
        return row
    row.attempts += 1
    db.commit()
    return None


def prune_codes(db: Session) -> int:
    """Delete expired sent codes (startup housekeeping)."""
    n = db.execute(delete(models.OneTimeCode).where(models.OneTimeCode.expires_at < models.utcnow())).rowcount
    db.commit()
    return n or 0


# ---------------------------------------------------------------------------- checks


def check_password(account: models.Account, auth_key: str) -> None:
    """Sensitive changes re-prove the password (the client-derived auth key), even with a valid
    session: a leaked token must not be enough to weaken the account."""
    key = f"pw:{account.id}"
    if LIMITER.blocked(key):
        raise _too_many(key)
    if not auth.verify_auth_key(auth_key, account.auth_salt, account.auth_hash):
        LIMITER.failed(key)
        raise HTTPException(status_code=403, detail=MSG_BAD_PASSWORD)
    LIMITER.reset(key)


def consume_code(
    db: Session,
    account: models.Account,
    code: str,
    *,
    purpose: str = PURPOSE_LOGIN,
    now: float | None = None,
    wrong_status: int = 401,
) -> str:
    """Accept a current authenticator code, the outstanding code sent by text/e-mail for
    ``purpose``, or an unused recovery code. Returns the method used. Raises ``wrong_status``
    for a wrong code — 401 at sign-in, 403 on the signed-in endpoints (clients treat a 401 as
    "session expired") — and 429 after too many wrong ones. Caller commits."""
    methods = enabled_methods(account)
    if not methods:
        raise HTTPException(status_code=409, detail="Two-factor authentication is not turned on for this account.")
    key = account.id
    if LIMITER.blocked(key):
        raise _too_many(key)
    clean = totp.normalize_code(code)
    if totp.looks_like_totp(clean):
        if METHOD_TOTP in methods:
            step = totp.verify(account.totp_secret or "", clean, now=now, last_step=account.totp_last_step)
            if step is not None:
                account.totp_last_step = step
                LIMITER.reset(key)
                return METHOD_TOTP
        channels = tuple(m for m in methods if m in (METHOD_SMS, METHOD_EMAIL))
        if channels:
            row = _check_sent_code(db, account, purpose, clean, channels=channels)
            if row is not None:
                method = row.channel
                db.delete(row)
                LIMITER.reset(key)
                return method
    else:
        hashes = _codes(account)
        matched = totp.match_recovery_code(clean, account.auth_salt, hashes)
        if matched is not None:
            hashes.remove(matched)
            account.recovery_codes = json.dumps(hashes)
            LIMITER.reset(key)
            accounts.audit(db, account, "2fa.recovery_used", account, codes_left=len(hashes))
            return METHOD_RECOVERY
    LIMITER.failed(key)
    raise HTTPException(status_code=wrong_status, detail=MSG_BAD_CODE)


# ---------------------------------------------------------------------------- login


def _challenge_account(db: Session, challenge: str) -> models.Account:
    account_id = auth.decode_challenge(challenge)
    account = db.get(models.Account, account_id) if account_id else None
    if not account or account.deleted_at or not account.two_factor_enabled:
        # Expired/forged challenge — or the second factor was turned off/reset meanwhile: the
        # password alone signs in now, so the client simply starts over.
        raise HTTPException(status_code=401, detail=MSG_STALE_CHALLENGE)
    return account


def challenge_for(db: Session, account: models.Account) -> schemas.TwoFactorChallenge:
    """The 202 answer of the password step. When the preferred method is a text/e-mail code it
    is sent right away; a provider failure is reported (``send_error``) rather than failing the
    sign-in — the user can pick another method or a recovery code."""
    methods = enabled_methods(account)
    default = methods[0]
    out = schemas.TwoFactorChallenge(
        challenge=auth.issue_challenge(account.id),
        expires_in=config.TWO_FACTOR_CHALLENGE_TTL_SECONDS,
        methods=methods + ([METHOD_RECOVERY] if _codes(account) else []),
        default_method=default,
        destinations=_masked_destinations(account, methods),
    )
    if default in (METHOD_SMS, METHOD_EMAIL):
        try:
            out.sent = send_code(db, account, PURPOSE_LOGIN, default, _destination(account, default))
        except HTTPException as exc:
            out.send_error = str(exc.detail)
    return out


def send_login_code(db: Session, challenge: str, method: str) -> schemas.CodeDelivery:
    account = _challenge_account(db, challenge)
    if method not in enabled_methods(account):
        raise HTTPException(status_code=409, detail=f"Codes by {LABELS[method]} are not turned on for this account.")
    return send_code(db, account, PURPOSE_LOGIN, method, _destination(account, method))


def complete_login(db: Session, challenge: str, code: str) -> tuple[models.Account, str]:
    """Second login step: the challenge from step one plus a code → the account (the caller
    registers the device and issues the session). Caller commits."""
    account = _challenge_account(db, challenge)
    method = consume_code(db, account, code, purpose=PURPOSE_LOGIN)
    return account, method


# ---------------------------------------------------------------------------- enrolment


def begin_setup(
    db: Session,
    account: models.Account,
    auth_key: str,
    method: str = METHOD_TOTP,
    phone: str | None = None,
    email: str | None = None,
    code: str | None = None,
) -> schemas.TwoFactorSetup:
    """Step 1 of turning a method on — or of **changing** one that is on (a new phone number,
    another e-mail address, a new authenticator app): a fresh secret for the app, or a code sent
    to the address / number being added. Nothing changes until ``enable`` confirms it.

    Who may do this: the signed-in owner, re-proving the password — and, when a method is being
    replaced, a *current* code from one of the enabled methods (or a recovery code), so a stolen
    session plus a guessed password is still not enough to redirect the codes. Caller commits."""
    replacing = method in enabled_methods(account)
    if replacing and not code:
        raise HTTPException(
            status_code=403,
            detail=f"To change your {LABELS[method]} settings, enter a current code from one of your "
            "two-factor methods (or a recovery code) along with your password.",
        )
    check_password(account, auth_key)
    if replacing:
        consume_code(db, account, code or "", purpose=PURPOSE_VERIFY, wrong_status=403)
    out = schemas.TwoFactorSetup(
        method=method,
        issuer=config.TWO_FACTOR_ISSUER,
        account=account.email,
        digits=totp.DIGITS,
        period=totp.STEP_SECONDS,
        replacing=replacing,
    )
    if method == METHOD_TOTP:
        secret = totp.new_secret()
        account.totp_pending_secret = secret
        out.secret = secret
        out.otpauth_uri = totp.otpauth_uri(secret, account.email, config.TWO_FACTOR_ISSUER)
        return out
    if method == METHOD_SMS:
        destination = normalize_phone(phone)
        if destination is None:
            raise HTTPException(
                status_code=422,
                detail="Enter the mobile number in international format, starting with + and the country code "
                "(e.g. +1 415 555 0100 or +49 151 2345678).",
            )
        if replacing and destination == account.phone_number:
            raise HTTPException(status_code=422, detail="That is already the number your codes go to.")
    else:
        destination = normalize_email(email) if email else account.email
        if destination is None:
            raise HTTPException(status_code=422, detail="Enter a valid e-mail address for the codes.")
        if replacing and destination == _destination(account, METHOD_EMAIL):
            raise HTTPException(status_code=422, detail="That is already the address your codes go to.")
    out.delivery = send_code(db, account, PURPOSE_SETUP, method, destination)
    return out


def _issue_recovery_codes(account: models.Account) -> list[str]:
    codes = totp.new_recovery_codes()
    account.recovery_codes = json.dumps([totp.hash_recovery_code(c, account.auth_salt) for c in codes])
    return codes


def enable(
    db: Session, account: models.Account, code: str, *, method: str = METHOD_TOTP, now: float | None = None
) -> schemas.TwoFactorRecoveryCodes:
    """Step 2: the right code activates the method — or, when the method was already on and
    ``begin_setup`` started a change, swaps the old app / number / address for the new one (the
    old destination is told). The first method also hands out the recovery codes (shown once).
    Caller commits."""
    replacing = method in enabled_methods(account)
    first = not account.two_factor_enabled
    key = account.id
    if LIMITER.blocked(key):
        raise _too_many(key)
    old_destination = _destination(account, method) if replacing and method != METHOD_TOTP else None
    if method == METHOD_TOTP:
        if not account.totp_pending_secret:
            raise HTTPException(
                status_code=409,
                detail="This method is already on."
                if replacing
                else "Start the set-up first (scan the QR code), then enter a code.",
            )
        step = totp.verify(account.totp_pending_secret, code, now=now)
        if step is None:
            LIMITER.failed(key)
            raise HTTPException(
                status_code=403,  # not 401: to the signed-in client that would mean "session expired"
                detail="That code does not match. Check that the authenticator app holds the secret shown here and "
                "that its clock is right, then enter the current code.",
            )
        account.totp_secret = account.totp_pending_secret
        account.totp_pending_secret = None
        account.totp_last_step = step
        account.totp_enabled_at = models.utcnow()
    else:
        pending = db.execute(
            select(models.OneTimeCode).where(
                models.OneTimeCode.account_id == account.id,
                models.OneTimeCode.purpose == PURPOSE_SETUP,
                models.OneTimeCode.channel == method,
            )
        ).scalar_one_or_none()
        if pending is None:
            raise HTTPException(
                status_code=409,
                detail="This method is already on." if replacing else "Ask for a code first, then enter it here.",
            )
        row = _check_sent_code(db, account, PURPOSE_SETUP, code, channels=(method,))
        if row is None:
            LIMITER.failed(key)
            raise HTTPException(
                status_code=403,
                detail=f"That code does not match the one we sent by {LABELS[method]}. Check the latest message, "
                "or ask for a new code.",
            )
        if method == METHOD_SMS:
            account.phone_number = row.destination
            account.sms_otp_enabled_at = models.utcnow()
        else:
            account.otp_email = row.destination if row.destination != account.email else None
            account.email_otp_enabled_at = models.utcnow()
        db.delete(row)
    LIMITER.reset(key)
    codes = _issue_recovery_codes(account) if first or not _codes(account) else []
    if replacing:
        accounts.audit(db, account, "2fa.method_changed", account, method=method)
        _notify_change(account, method, old_destination)
    else:
        accounts.audit(db, account, "2fa.enable", account, method=method)
    return schemas.TwoFactorRecoveryCodes(recovery_codes=codes, status=status(account))


def _notify_change(account: models.Account, method: str, old_destination: str | None) -> None:
    """Tell the *previous* destination(s) that the second factor changed — the one thing an
    attacker who somehow passed every check cannot suppress. Best effort: a provider outage
    never blocks the change itself."""
    what = {
        METHOD_TOTP: "authenticator app",
        METHOD_SMS: f"phone number for text-message codes (now {mask_phone(account.phone_number)})",
        METHOD_EMAIL: f"e-mail address for codes (now {mask_email(_destination(account, METHOD_EMAIL))})",
    }[method]
    issuer = config.TWO_FACTOR_ISSUER
    text = (
        f"The {what} for two-factor sign-in to your {issuer} account was just changed. If that was you, "
        f"nothing else to do. If not, sign in now, change your password and check Account & sync — "
        f"or contact support."
    )
    targets = [(METHOD_EMAIL, account.email)]
    if method == METHOD_EMAIL and old_destination and old_destination != account.email:
        targets.append((METHOD_EMAIL, old_destination))
    if method == METHOD_SMS and old_destination:
        targets.append((METHOD_SMS, old_destination))
    for channel, to in targets:
        via = messaging.channel(channel)
        if not via.available:
            continue
        try:
            via.send(to, text if channel == METHOD_SMS else text + "\n", f"{issuer}: two-factor sign-in changed")
        except messaging.MessagingError as exc:  # pragma: no cover - provider outage
            log.warning("could not notify %s about a two-factor change: %s", to, exc)


def resend_setup_code(db: Session, account: models.Account) -> schemas.CodeDelivery:
    """ "Send a new code" on the confirmation step: the pending set-up / change was already
    authorised, so the code simply goes to the same new address or number again (limits apply).
    Caller commits."""
    row = db.execute(
        select(models.OneTimeCode).where(
            models.OneTimeCode.account_id == account.id, models.OneTimeCode.purpose == PURPOSE_SETUP
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=409, detail="There is no set-up in progress. Start again from the account screen."
        )
    return send_code(db, account, PURPOSE_SETUP, row.channel, row.destination)


def send_verify_code(db: Session, account: models.Account, method: str) -> schemas.CodeDelivery:
    """Signed in: a code by text/e-mail to confirm a sensitive change. Caller commits."""
    if method not in enabled_methods(account):
        raise HTTPException(status_code=409, detail=f"Codes by {LABELS[method]} are not turned on for this account.")
    return send_code(db, account, PURPOSE_VERIFY, method, _destination(account, method))


def disable(
    db: Session, account: models.Account, auth_key: str, code: str, method: str | None = None
) -> schemas.TwoFactorStatus:
    """Password + a current code (any method, or a recovery code). ``method`` removes just that
    one; without it — or when it was the last one — the second factor is turned off entirely
    (recovery codes included). Caller commits."""
    methods = enabled_methods(account)
    if not methods:
        raise HTTPException(status_code=409, detail="Two-factor authentication is not turned on.")
    if method is not None and method not in methods:
        raise HTTPException(status_code=409, detail=f"Two-factor authentication by {LABELS[method]} is not on.")
    check_password(account, auth_key)
    consume_code(db, account, code, purpose=PURPOSE_VERIFY, wrong_status=403)
    if method is None or methods == [method]:
        clear(db, account)
        accounts.audit(db, account, "2fa.disable", account)
    else:
        _remove(account, method)
        accounts.audit(db, account, "2fa.method_removed", account, method=method)
    return status(account)


def regenerate_recovery_codes(
    db: Session, account: models.Account, auth_key: str, code: str
) -> schemas.TwoFactorRecoveryCodes:
    """New set of ten; the old ones stop working at once. Caller commits."""
    if not account.two_factor_enabled:
        raise HTTPException(status_code=409, detail="Two-factor authentication is not turned on.")
    check_password(account, auth_key)
    consume_code(db, account, code, purpose=PURPOSE_VERIFY, wrong_status=403)
    codes = _issue_recovery_codes(account)
    accounts.audit(db, account, "2fa.recovery_codes", account)
    return schemas.TwoFactorRecoveryCodes(recovery_codes=codes, status=status(account))


def _remove(account: models.Account, method: str) -> None:
    if method == METHOD_TOTP:
        account.totp_secret = None
        account.totp_pending_secret = None
        account.totp_enabled_at = None
        account.totp_last_step = 0
    elif method == METHOD_SMS:
        account.sms_otp_enabled_at = None
        account.phone_number = None  # the number is kept only while it is used
    elif method == METHOD_EMAIL:
        account.email_otp_enabled_at = None
        account.otp_email = None


def clear(db: Session | None, account: models.Account) -> None:
    """Forget every second-factor secret (turn off, administrator reset, account purge)."""
    for method in METHODS:
        _remove(account, method)
    account.recovery_codes = "[]"
    if db is not None:
        db.execute(delete(models.OneTimeCode).where(models.OneTimeCode.account_id == account.id))
    LIMITER.reset(account.id)
