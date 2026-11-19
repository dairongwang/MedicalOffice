"""Electronic signatures — a small strategy with a real-provider stub and a built-in fake.

    provider   config.ESIGN_PROVIDER
    fake       (default outside prod) records the signature locally and returns a reference like
               FAKE-ESIGN-3F9A…; nothing leaves the machine. What the onboarding portal uses in
               development and tests.
    docusign / hellosign / …   stubs: raise NotConfigured until credentials and an adapter exist.

What every provider receives is the same *evidence bundle* the portal collected: who signed
(typed full name), the drawn signature (PNG data URL), the document key + version they were
shown, and when. The provider's reference is stored with the submission (OnboardingSubmission).
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

import config

log = logging.getLogger("account.esign")

MAX_SIGNATURE_BYTES = 200_000  # a drawn signature is a few KB; this is a generous cap


class ESignError(Exception):
    """The signature could not be recorded (bad image, provider not configured / refused)."""


class NotConfigured(ESignError):
    pass


@dataclass
class Signature:
    signer_name: str
    image_png: str  # data:image/png;base64,…
    document_key: str
    document_version: str
    signed_at: datetime


@dataclass
class SignResult:
    provider: str
    reference: str


def validate_image(data_url: str) -> bytes:
    """A PNG data URL of sane size → its bytes; anything else is refused."""
    prefix = "data:image/png;base64,"
    if not isinstance(data_url, str) or not data_url.startswith(prefix):
        raise ESignError("The signature must be a PNG image (data URL).")
    try:
        raw = base64.b64decode(data_url[len(prefix) :], validate=True)
    except (ValueError, TypeError) as exc:
        raise ESignError("The signature image is not valid base64.") from exc
    if len(raw) < 100 or len(raw) > MAX_SIGNATURE_BYTES:
        raise ESignError("The signature image is empty or too large.")
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise ESignError("The signature image is not a PNG.")
    return raw


class Provider:
    name = "base"

    def sign(self, signature: Signature) -> SignResult:
        raise NotImplementedError


class FakeESign(Provider):
    """Development/test stand-in: deterministic-looking reference derived from the evidence."""

    name = "fake"

    def sign(self, signature: Signature) -> SignResult:
        raw = validate_image(signature.image_png)
        digest = (
            hashlib.sha256(
                raw
                + signature.signer_name.encode()
                + signature.document_key.encode()
                + signature.document_version.encode()
            )
            .hexdigest()[:12]
            .upper()
        )
        reference = f"FAKE-ESIGN-{digest}-{secrets.token_hex(2).upper()}"
        log.info(
            "[fake e-sign] %s signed %s v%s → %s",
            signature.signer_name,
            signature.document_key,
            signature.document_version,
            reference,
        )
        return SignResult(provider=self.name, reference=reference)


class StubProvider(Provider):
    """Placeholder for a real integration (DocuSign, Dropbox Sign, Adobe Sign …)."""

    def __init__(self, name: str):
        self.name = name

    def sign(self, signature: Signature) -> SignResult:
        raise NotConfigured(
            f"The e-signature provider '{self.name}' is not set up on this server (no credentials/adapter). "
            "Use ESIGN_PROVIDER=fake outside production."
        )


def provider() -> Provider:
    name = (config.ESIGN_PROVIDER or "fake").lower()
    if name == "fake":
        if config.IS_PROD:
            return StubProvider("fake (not allowed in prod)")
        return FakeESign()
    return StubProvider(name)


def sign(signer_name: str, image_png: str, document_key: str, document_version: str) -> tuple[SignResult, datetime]:
    now = datetime.now(timezone.utc)
    result = provider().sign(
        Signature(
            signer_name=signer_name,
            image_png=image_png,
            document_key=document_key,
            document_version=document_version,
            signed_at=now,
        )
    )
    return result, now
