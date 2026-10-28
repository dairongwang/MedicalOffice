"""Time-based one-time passwords (RFC 6238 over HOTP, RFC 4226) and recovery codes.

Standard library only. The parameters are the ones every authenticator app (Google
Authenticator, Authy, 1Password, Bitwarden, Microsoft Authenticator, …) uses by default:
SHA-1, 6 digits, 30-second steps, a 160-bit secret — so the ``otpauth://`` URI works everywhere.

    secret (base32) ──HMAC-SHA1(step counter)──▶ dynamic truncation ──▶ 6 digits

``verify`` accepts the current step and one step on either side (clock drift of up to 30 s),
and returns the *step* that matched so the caller can remember it: a code is good for one login
only — replaying a captured code inside its 90-second window is refused.

Recovery codes are the way back in when the authenticator is gone: ten random codes shown once
at activation, stored as salted SHA-256 hashes (they carry ~50 bits of entropy, so a fast hash
is fine), each usable once.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

SECRET_BYTES = 20  # RFC 4226 §4: at least 128 bits; 160 bits is what the apps expect
STEP_SECONDS = 30
DIGITS = 6
DRIFT_STEPS = 1  # accept one step before/after "now"

RECOVERY_CODE_COUNT = 10
RECOVERY_CODE_LENGTH = 10
# Without the look-alikes (0/o, 1/l/i) — the codes are typed by hand from a printout.
RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"

# ---------------------------------------------------------------------------- TOTP


def new_secret() -> str:
    """A fresh base32 secret without padding, as the authenticator apps want it."""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode("ascii").rstrip("=")


def _secret_bytes(secret: str) -> bytes:
    clean = secret.strip().replace(" ", "").upper()
    return base64.b32decode(clean + "=" * (-len(clean) % 8), casefold=True)


def _now() -> float:
    """The clock (a function so tests can step through 30-second windows)."""
    return time.time()


def current_step(now: float | None = None) -> int:
    return int((_now() if now is None else now) // STEP_SECONDS)


def hotp(secret: str, counter: int, digits: int = DIGITS) -> str:
    """RFC 4226: HMAC-SHA1 of the big-endian counter, dynamically truncated to ``digits``."""
    digest = hmac.new(_secret_bytes(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % (10**digits)
    return str(code).zfill(digits)


def code_at(secret: str, now: float | None = None) -> str:
    """The code an authenticator shows right now (tests, the seeded fixture)."""
    return hotp(secret, current_step(now))


def normalize_code(text: str) -> str:
    """What the user typed → the bare code: spaces and dashes dropped, lower-cased."""
    return "".join(ch for ch in text.strip().lower() if ch not in " -\u00a0\u2011\u2013\u2014")


def looks_like_totp(code: str) -> bool:
    return len(code) == DIGITS and code.isdigit()


def verify(secret: str, code: str, *, now: float | None = None, last_step: int = 0) -> int | None:
    """The step the code belongs to (current ±DRIFT_STEPS), or None. Steps at or before
    ``last_step`` — a code that already signed somebody in — never match again."""
    code = normalize_code(code)
    if not looks_like_totp(code):
        return None
    step = current_step(now)
    for candidate in range(step - DRIFT_STEPS, step + DRIFT_STEPS + 1):
        if candidate <= last_step:
            continue
        if hmac.compare_digest(hotp(secret, candidate), code):
            return candidate
    return None


def otpauth_uri(secret: str, account: str, issuer: str) -> str:
    """The ``otpauth://totp/…`` URI the QR code encodes (Key Uri Format)."""
    label = quote(f"{issuer}:{account}", safe="")
    params = f"secret={secret}&issuer={quote(issuer, safe='')}&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}"
    return f"otpauth://totp/{label}?{params}"


# ---------------------------------------------------------------------------- recovery codes


def new_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
    """Ten codes like ``k7pmx-3wqen``: shown once, stored hashed."""
    out = []
    for _ in range(count):
        raw = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(RECOVERY_CODE_LENGTH))
        out.append(f"{raw[:5]}-{raw[5:]}")
    return out


def hash_recovery_code(code: str, salt: str) -> str:
    return hashlib.sha256((salt + ":" + normalize_code(code)).encode("utf-8")).hexdigest()


def match_recovery_code(code: str, salt: str, hashes: list[str]) -> str | None:
    """The stored hash the typed code corresponds to, or None."""
    candidate = hash_recovery_code(code, salt)
    found = None
    for stored in hashes:  # constant-time per entry, no early exit
        if hmac.compare_digest(candidate, stored):
            found = stored
    return found
