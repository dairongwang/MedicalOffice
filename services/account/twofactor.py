"""Two-factor authentication — the second step of a sign-in, per account and optional.

Three methods, any combination of which may be on at the same time:

    totp    a time-based code from an authenticator app (totp.py)   → ``accounts.totp_secret``
    sms     a 6-digit code by text message (messaging.SMS_CHANNEL)  → ``sms_otp_enabled_at`` + ``phone_number``
    email   a 6-digit code by e-mail (messaging.EMAIL_CHANNEL)      → ``email_otp_enabled_at`` (+ ``otp_email``)

plus one-time **recovery codes** (shown once when the first method is turned on; stored as
salted hashes) for the day the phone is gone.

Flows (main.py wires them to the /auth and /me/2fa endpoints):

  sign-in     password ok → ``challenge_for``: a short-lived signed challenge (auth.issue_challenge)
              listing the methods; when the preferred one is sms/e-mail a code goes out right away.
              ``send_login_code`` (re)sends a code, ``complete_login`` checks the code and returns
              the account. Wrong codes are counted per account (FailureLimiter) → 429.
  set-up      ``begin_setup`` (password re-check): totp → a pending secret + otpauth URI for the QR
              code; sms/email → a "setup" code to the new number/address. ``enable`` confirms the
              code and activates the method (recovery codes come back with the first one).
  changes     ``disable`` (one method or everything), ``regenerate_recovery_codes`` — password +
              a current code (any method, or a recovery code); ``send_verify_code`` delivers one.
  support     ``clear`` removes every second factor (admin.py, after identity verification).

Codes we send are never stored in clear: ``one_time_codes`` keeps an HMAC (TOKEN_SECRET) of
the code, one outstanding row per account and purpose (login | setup | verify).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

import auth
import config
import messaging
import models
import schemas
import totp
from ratelimit import FailureLimiter, RateLimiter

log = logging.getLogger("account.twofactor")

TOTP, SMS, EMAIL, RECOVERY = "totp", "sms", "email", "recovery"
METHODS = (TOTP, SMS, EMAIL)  # preference order: the sign-in screen offers the first enabled one
CHANNELS = (SMS, EMAIL)
PURPOSE_LOGIN, PURPOSE_SETUP, PURPOSE_VERIFY = "login", "setup", "verify"

CODE_DIGITS = 6

# Wrong codes per account (any purpose): after TWO_FACTOR_MAX_FAILURES within the window → 429.
FAILURES = FailureLimiter(config.TWO_FACTOR_MAX_FAILURES, config.TWO_FACTOR_FAILURE_WINDOW_SECONDS)
# Password re-checks on sensitive changes share the same budget (keyed separately).
PASSWORD_FAILURES = FailureLimiter(config.TWO_FACTOR_MAX_FAILURES, config.TWO_FACTOR_FAILURE_WINDOW_SECONDS)
# Codes sent per account within the window (texts cost money; "SMS pumping" is a real attack).
SENDS = RateLimiter(config.TWO_FACTOR_MAX_SENDS, config.TWO_FACTOR_SEND_WINDOW_SECONDS)

_PHONE_DIGITS = re.compile(r"^\+[1-9]\d{7,14}$")  # E.164: "+" and 8–15 digits


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------- masking & normalising


def mask_email(address: str | None) -> str | None:
    """``alice@example.com`` → ``a•••@example.com``."""
    if not address or "@" not in address:
        return None
    local, domain = address.rsplit("@", 1)
    return f"{local[:1]}•••@{domain}"


def mask_phone(phone: str | None) -> str | None:
    """``+15551234567`` → ``+•••••••4567``."""
    if not phone:
        return None
    digits = phone.lstrip("+")
    return "+" + "•" * max(0, len(digits) - 4) + digits[-4:]


def normalize_phone(raw: str | None) -> str:
    """What the user typed → E.164 (``+15551234567``). Spaces, dashes, dots and parentheses are
    dropped; a leading ``00`` becomes ``+``. Anything else is refused."""
    text = (raw or "").strip()
    cleaned = re.sub(r"[\s().\-\u00a0\u2011\u2013\u2014]", "", text)
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    if not _PHONE_DIGITS.match(cleaned):
        raise HTTPException(
            status_code=422,
            detail="Please enter the phone number in international format, e.g. +1 555 123 4567.",
        )
    return cleaned


def normalize_email(raw: str | None) -> str:
    text = (raw or "").strip().lower()
    if not text or "@" not in text or text.startswith("@") or text.endswith("@") or " " in text or len(text) > 320:
        raise HTTPException(status_code=422, detail="Please enter a valid e-mail address.")
    return text


# ---------------------------------------------------------------------------- status


def enabled_methods(account: models.Account) -> list[str]:
    out = []
    if account.totp_secret:
        out.append(TOTP)
    if account.sms_otp_enabled_at and account.phone_number:
        out.append(SMS)
    if account.email_otp_enabled_at:
        out.append(EMAIL)
    return out


def available_methods() -> list[str]:
    """What this server can set up right now: totp always; sms/email need a provider or the fake."""
    out = [TOTP]
    if messaging.SMS_CHANNEL.available:
        out.append(SMS)
    if messaging.EMAIL_CHANNEL.available:
        out.append(EMAIL)
    return out


def fake_channels() -> list[str]:
    return [c for c in CHANNELS if messaging.channel(c).fake and messaging.channel(c).available]


def otp_email_for(account: models.Account) -> str:
    return account.otp_email or account.email


def _recovery_hashes(account: models.Account) -> list[str]:
    try:
        data = json.loads(account.recovery_codes or "[]")
    except ValueError:
        return []
    return [h for h in data if isinstance(h, str)]


def _enabled_at(account: models.Account) -> datetime | None:
    stamps = [t for t in (account.totp_enabled_at, account.sms_otp_enabled_at, account.email_otp_enabled_at) if t]
    return min(stamps) if stamps else None


def status(account: models.Account) -> schemas.TwoFactorStatus:
    methods = enabled_methods(account)
    return schemas.TwoFactorStatus(
        enabled=bool(methods),
        enabled_at=_enabled_at(account),
        recovery_codes_left=len(_recovery_hashes(account)) if methods else 0,
        pending=bool(account.totp_pending_secret),
        methods=methods,
        phone=mask_phone(account.phone_number) if SMS in methods else None,
        email=mask_email(otp_email_for(account)) if EMAIL in methods else mask_email(account.email),
        available=available_methods(),
        fake_channels=fake_channels(),
    )


# ---------------------------------------------------------------------------- one-time codes (sms / e-mail)


def _code_hash(account_id: str, purpose: str, code: str) -> str:
    msg = f"{account_id}:{purpose}:{totp.normalize_code(code)}".encode()
    return hmac.new(config.TOKEN_SECRET.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def _new_code() -> str:
    return f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"


def _pending(db: Session, account: models.Account, purpose: str) -> models.OneTimeCode | None:
    return db.execute(
        select(models.OneTimeCode).where(
            models.OneTimeCode.account_id == account.id, models.OneTimeCode.purpose == purpose
        )
    ).scalar_one_or_none()


def _drop(db: Session, account: models.Account, purpose: str) -> None:
    db.execute(
        delete(models.OneTimeCode).where(
            models.OneTimeCode.account_id == account.id, models.OneTimeCode.purpose == purpose
        )
    )


def prune_codes(db: Session) -> int:
    """Delete expired codes (start-up housekeeping). Returns how many went."""
    result = db.execute(delete(models.OneTimeCode).where(models.OneTimeCode.expires_at < _now()))
    db.commit()
    return result.rowcount or 0


def _message(purpose: str, code: str) -> tuple[str, str]:
    issuer = config.TWO_FACTOR_ISSUER
    what = {
        PURPOSE_LOGIN: "sign-in",
        PURPOSE_SETUP: "two-factor set-up",
        PURPOSE_VERIFY: "security change",
    }[purpose]
    minutes = max(1, config.TWO_FACTOR_CODE_TTL_SECONDS // 60)
    body = (
        f"{code} is your {issuer} {what} code. It is valid for {minutes} minutes.\n"
        "If you did not request it, you can ignore this message."
    )
    if config.SMS_WEBOTP_HOST:  # WebOTP: "@host #code" as the last line lets Android offer auto-fill
        body += f"\n\n@{config.SMS_WEBOTP_HOST} #{code}"
    return f"{issuer}: your {what} code", body


def _send_code(
    db: Session, account: models.Account, purpose: str, channel_name: str, destination: str
) -> schemas.CodeDelivery:
    """Generate, store (hashed) and deliver a code. Enforces the resend interval and the per-account
    send budget; a provider failure surfaces as 503 with the reason."""
    if channel_name not in CHANNELS:
        raise HTTPException(status_code=422, detail="Codes can be sent by text message or e-mail.")
    ch = messaging.channel(channel_name)
    if not ch.available:
        raise HTTPException(status_code=503, detail=f"{ch.label} codes are not available on this server.")
    existing = _pending(db, account, purpose)
    now = _now()
    if existing and existing.destination == destination and existing.channel == channel_name:
        elapsed = (now - existing.created_at).total_seconds()
        if elapsed < config.TWO_FACTOR_RESEND_SECONDS:
            wait = int(config.TWO_FACTOR_RESEND_SECONDS - elapsed) + 1
            raise HTTPException(
                status_code=429,
                detail=f"A code was just sent. You can ask for another one in {wait} seconds.",
                headers={"Retry-After": str(wait)},
            )
    if not SENDS.allow(account.id):
        raise HTTPException(
            status_code=429,
            detail="Too many codes were requested for this account. Please try again later.",
            headers={"Retry-After": str(config.TWO_FACTOR_SEND_WINDOW_SECONDS)},
        )
    code = _new_code()
    subject, body = _message(purpose, code)
    try:
        ch.send(destination, body, subject=subject)
    except messaging.MessagingError as exc:
        raise HTTPException(status_code=503, detail=f"The code could not be sent: {exc}.") from exc
    _drop(db, account, purpose)
    db.add(
        models.OneTimeCode(
            account_id=account.id,
            purpose=purpose,
            channel=channel_name,
            destination=destination,
            code_hash=_code_hash(account.id, purpose, code),
            created_at=now,
            expires_at=now + timedelta(seconds=config.TWO_FACTOR_CODE_TTL_SECONDS),
        )
    )
    db.flush()
    return schemas.CodeDelivery(
        method=channel_name,
        destination=(mask_phone(destination) if channel_name == SMS else mask_email(destination)) or "",
        expires_in=config.TWO_FACTOR_CODE_TTL_SECONDS,
        resend_after=config.TWO_FACTOR_RESEND_SECONDS,
        fake_outbox_url=messaging.outbox_url() if ch.fake else None,
    )


def _check_sent_code(db: Session, account: models.Account, purpose: str, code: str) -> models.OneTimeCode | None:
    """The pending row if ``code`` is the code we sent for ``purpose`` (consumed), else None."""
    row = _pending(db, account, purpose)
    if not row:
        return None
    if row.expires_at < _now():
        _drop(db, account, purpose)
        return None
    if not totp.looks_like_totp(totp.normalize_code(code)):
        return None
    if not hmac.compare_digest(row.code_hash, _code_hash(account.id, purpose, code)):
        row.attempts += 1
        if row.attempts >= config.TWO_FACTOR_MAX_FAILURES:
            _drop(db, account, purpose)  # burn it: a guessed code must not stay valid
        return None
    _drop(db, account, purpose)
    return row


def _destination_for(account: models.Account, channel_name: str) -> str:
    if channel_name == SMS:
        if not (account.sms_otp_enabled_at and account.phone_number):
            raise HTTPException(status_code=409, detail="Text-message codes are not turned on for this account.")
        return account.phone_number
    if channel_name == EMAIL:
        if not account.email_otp_enabled_at:
            raise HTTPException(status_code=409, detail="E-mail codes are not turned on for this account.")
        return otp_email_for(account)
    raise HTTPException(status_code=422, detail="Codes can be sent by text message or e-mail.")


# ---------------------------------------------------------------------------- verifying any code


def _too_many_failures(account: models.Account) -> None:
    wait = FAILURES.retry_after(account.id)
    if wait:
        raise HTTPException(
            status_code=429,
            detail=f"Too many wrong codes. Please wait {wait} seconds and try again.",
            headers={"Retry-After": str(wait)},
        )


def _use_recovery_code(account: models.Account, code: str) -> bool:
    hashes = _recovery_hashes(account)
    found = totp.match_recovery_code(code, account.auth_salt, hashes)
    if found is None:
        return False
    account.recovery_codes = json.dumps([h for h in hashes if h != found])
    return True


def verify_code(db: Session, account: models.Account, code: str, purposes: tuple[str, ...] = (PURPOSE_VERIFY,)) -> str:
    """Check a code against every enabled method (authenticator, the sent code for ``purposes``,
    recovery codes). Returns the method that matched; raises 401 (counted) or 429."""
    _too_many_failures(account)
    code = totp.normalize_code(code)
    if account.totp_secret and totp.looks_like_totp(code):
        step = totp.verify(account.totp_secret, code, last_step=account.totp_last_step)
        if step is not None:
            account.totp_last_step = step
            FAILURES.reset(account.id)
            return TOTP
    for purpose in purposes:
        row = _check_sent_code(db, account, purpose, code)
        if row is not None:
            FAILURES.reset(account.id)
            return row.channel
    if enabled_methods(account) and _use_recovery_code(account, code):
        FAILURES.reset(account.id)
        return RECOVERY
    FAILURES.failed(account.id)
    raise HTTPException(status_code=401, detail="That code is not right. Please check it and try again.")


def _check_password(account: models.Account, auth_key: str) -> None:
    if not account.has_password:
        raise HTTPException(status_code=409, detail="This account has no password yet. Set a passphrase first.")
    wait = PASSWORD_FAILURES.retry_after(account.id)
    if wait:
        raise HTTPException(
            status_code=429,
            detail=f"Too many attempts. Please wait {wait} seconds and try again.",
            headers={"Retry-After": str(wait)},
        )
    if not auth.verify_auth_key(auth_key, account.auth_salt, account.auth_hash):
        PASSWORD_FAILURES.failed(account.id)
        raise HTTPException(status_code=401, detail="The password is not right.")
    PASSWORD_FAILURES.reset(account.id)


# ---------------------------------------------------------------------------- sign-in


def _destinations(account: models.Account) -> dict[str, str]:
    out: dict[str, str] = {}
    if account.sms_otp_enabled_at and account.phone_number:
        out[SMS] = mask_phone(account.phone_number) or ""
    if account.email_otp_enabled_at:
        out[EMAIL] = mask_email(otp_email_for(account)) or ""
    return out


def challenge_for(db: Session, account: models.Account) -> schemas.TwoFactorChallenge:
    """The password step passed: what the client needs for the code step. When the preferred
    method delivers codes, one goes out right away (a failure is reported, not raised — the user
    may still pick another method or a recovery code)."""
    methods = enabled_methods(account)
    if not methods:
        raise HTTPException(status_code=409, detail="Two-factor authentication is not turned on.")
    listed = list(methods)
    if _recovery_hashes(account):
        listed.append(RECOVERY)
    default = methods[0]
    sent = send_error = None
    if default in CHANNELS:
        try:
            sent = _send_code(db, account, PURPOSE_LOGIN, default, _destination_for(account, default))
        except HTTPException as exc:
            send_error = str(exc.detail)
    return schemas.TwoFactorChallenge(
        challenge=auth.issue_challenge(account.id),
        expires_in=config.TWO_FACTOR_CHALLENGE_TTL_SECONDS,
        methods=listed,
        default_method=default,
        destinations=_destinations(account),
        sent=sent,
        send_error=send_error,
    )


def _account_for_challenge(db: Session, challenge: str) -> models.Account:
    account_id = auth.decode_challenge(challenge)
    account = db.get(models.Account, account_id) if account_id else None
    if not account or account.deleted_at:
        raise HTTPException(status_code=401, detail="This sign-in has expired. Please enter your password again.")
    return account


def send_login_code(db: Session, challenge: str, method: str) -> schemas.CodeDelivery:
    account = _account_for_challenge(db, challenge)
    if method not in CHANNELS:
        raise HTTPException(status_code=422, detail="Codes can be sent by text message or e-mail.")
    return _send_code(db, account, PURPOSE_LOGIN, method, _destination_for(account, method))


def complete_login(db: Session, challenge: str, code: str) -> tuple[models.Account, str]:
    """The code step. Returns (account, method that matched); 401/429 otherwise."""
    account = _account_for_challenge(db, challenge)
    if not account.two_factor_enabled:
        raise HTTPException(status_code=409, detail="Two-factor authentication is not turned on.")
    method = verify_code(db, account, code, purposes=(PURPOSE_LOGIN,))
    _drop(db, account, PURPOSE_LOGIN)
    return account, method


# ---------------------------------------------------------------------------- set-up


def begin_setup(
    db: Session,
    account: models.Account,
    auth_key: str,
    method: str,
    phone: str | None,
    email: str | None,
    code: str | None,
) -> schemas.TwoFactorSetup:
    """Step 1 of turning a method on (or replacing it): password, and — when anything is already
    on — a current code. totp: a fresh pending secret. sms/email: a code to the new destination."""
    _check_password(account, auth_key)
    if method not in METHODS:
        raise HTTPException(status_code=422, detail="Unknown two-factor method.")
    if method not in available_methods():
        raise HTTPException(status_code=503, detail=f"{method} codes are not available on this server.")
    replacing = method in enabled_methods(account)
    if enabled_methods(account):
        if not code:
            raise HTTPException(
                status_code=401, detail="Enter a current code from one of your methods (or a recovery code) too."
            )
        verify_code(db, account, code)
    if method == TOTP:
        secret = totp.new_secret()
        account.totp_pending_secret = secret
        return schemas.TwoFactorSetup(
            method=TOTP,
            secret=secret,
            otpauth_uri=totp.otpauth_uri(secret, account.email, config.TWO_FACTOR_ISSUER),
            issuer=config.TWO_FACTOR_ISSUER,
            account=account.email,
            digits=totp.DIGITS,
            period=totp.STEP_SECONDS,
            replacing=replacing,
        )
    destination = normalize_phone(phone) if method == SMS else normalize_email(email) if email else account.email
    delivery = _send_code(db, account, PURPOSE_SETUP, method, destination)
    return schemas.TwoFactorSetup(
        method=method,
        issuer=config.TWO_FACTOR_ISSUER,
        account=account.email,
        delivery=delivery,
        replacing=replacing,
    )


def resend_setup_code(db: Session, account: models.Account) -> schemas.CodeDelivery:
    row = _pending(db, account, PURPOSE_SETUP)
    if not row:
        raise HTTPException(status_code=409, detail="No set-up is waiting for a code. Start again.")
    return _send_code(db, account, PURPOSE_SETUP, row.channel, row.destination)


def _issue_recovery_codes(account: models.Account) -> list[str]:
    codes = totp.new_recovery_codes()
    account.recovery_codes = json.dumps([totp.hash_recovery_code(c, account.auth_salt) for c in codes])
    return codes


def enable(db: Session, account: models.Account, code: str, method: str = TOTP) -> schemas.TwoFactorRecoveryCodes:
    """Step 2: the right code activates the method. The first method brings the recovery codes."""
    _too_many_failures(account)
    first = not enabled_methods(account)
    now = _now()
    if method == TOTP:
        secret = account.totp_pending_secret
        if not secret:
            raise HTTPException(status_code=409, detail="Authenticator set-up was not started. Start again.")
        step = totp.verify(secret, code)
        if step is None:
            FAILURES.failed(account.id)
            raise HTTPException(status_code=401, detail="That code is not right. Check the app and try again.")
        account.totp_secret = secret
        account.totp_pending_secret = None
        account.totp_last_step = step
        account.totp_enabled_at = now
    elif method in CHANNELS:
        row = _pending(db, account, PURPOSE_SETUP)
        if not row or row.channel != method:
            raise HTTPException(status_code=409, detail="Set-up was not started for this method. Start again.")
        if _check_sent_code(db, account, PURPOSE_SETUP, code) is None:
            FAILURES.failed(account.id)
            raise HTTPException(status_code=401, detail="That code is not right. Please check it and try again.")
        if method == SMS:
            account.phone_number = row.destination
            account.sms_otp_enabled_at = now
        else:
            account.otp_email = None if row.destination == account.email else row.destination
            account.email_otp_enabled_at = now
    else:
        raise HTTPException(status_code=422, detail="Unknown two-factor method.")
    FAILURES.reset(account.id)
    codes = _issue_recovery_codes(account) if first else []
    db.flush()
    return schemas.TwoFactorRecoveryCodes(recovery_codes=codes, status=status(account))


# ---------------------------------------------------------------------------- changes


def send_verify_code(db: Session, account: models.Account, method: str) -> schemas.CodeDelivery:
    """A code to confirm a sensitive change, by one of the enabled channels."""
    return _send_code(db, account, PURPOSE_VERIFY, method, _destination_for(account, method))


def _remove(account: models.Account, method: str) -> None:
    if method == TOTP:
        account.totp_secret = None
        account.totp_pending_secret = None
        account.totp_enabled_at = None
        account.totp_last_step = 0
    elif method == SMS:
        account.sms_otp_enabled_at = None
        account.phone_number = None
    elif method == EMAIL:
        account.email_otp_enabled_at = None
        account.otp_email = None


def clear(db: Session, account: models.Account) -> None:
    """Remove every second factor and the recovery codes (support reset, account purge)."""
    for m in METHODS:
        _remove(account, m)
    account.recovery_codes = "[]"
    db.execute(delete(models.OneTimeCode).where(models.OneTimeCode.account_id == account.id))


def disable(
    db: Session, account: models.Account, auth_key: str, code: str, method: str | None
) -> schemas.TwoFactorStatus:
    """Remove one method, or (``method`` None) turn two-factor authentication off entirely."""
    _check_password(account, auth_key)
    if not enabled_methods(account):
        raise HTTPException(status_code=409, detail="Two-factor authentication is not turned on.")
    if method is not None and method not in enabled_methods(account):
        raise HTTPException(status_code=409, detail="This method is not turned on.")
    verify_code(db, account, code)
    if method is None:
        clear(db, account)
    else:
        _remove(account, method)
        if not enabled_methods(account):  # the last one went: nothing left to recover
            account.recovery_codes = "[]"
            db.execute(delete(models.OneTimeCode).where(models.OneTimeCode.account_id == account.id))
    db.flush()
    return status(account)


def regenerate_recovery_codes(
    db: Session, account: models.Account, auth_key: str, code: str
) -> schemas.TwoFactorRecoveryCodes:
    _check_password(account, auth_key)
    if not enabled_methods(account):
        raise HTTPException(status_code=409, detail="Two-factor authentication is not turned on.")
    verify_code(db, account, code)
    codes = _issue_recovery_codes(account)
    db.flush()
    return schemas.TwoFactorRecoveryCodes(recovery_codes=codes, status=status(account))
