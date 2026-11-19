"""Insurance card photos — decoding and checking what the intake page sends.

The registration form (apps/web, features/patients/patient-intake) lets the patient upload the
front and back of their insurance card or take the photos with the camera. The photos travel in
the same JSON request as the rest of the form, as data URLs (``data:image/jpeg;base64,…``), so
the record and its cards are created together or not at all — the same arrangement as the
drawn signatures of the onboarding portal (esign.py).

What is accepted:

    type    JPEG, PNG or WebP — declared in the data URL *and* confirmed by the file's magic bytes
            (the declaration alone proves nothing). HEIC/HEIF from iPhones is converted on the
            device before upload (the page re-encodes every photo as JPEG).
    size    at most MAX_CARD_BYTES decoded. The page downsizes photos to ~1600 px on the long
            edge first, which lands well under that; the cap is for clients that do not.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass

MAX_CARD_BYTES = 5 * 1024 * 1024  # 5 MB decoded
MIN_CARD_BYTES = 256  # anything smaller is not a photo of a card

CONTENT_TYPES = {
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/webp": (b"RIFF",),  # + "WEBP" at offset 8, checked below
}

_DATA_URL = re.compile(r"^data:(image/(?:jpeg|jpg|png|webp));base64,(.+)$", re.DOTALL)


class InsuranceCardError(ValueError):
    """The card photo could not be used (wrong type, too big, not an image)."""


@dataclass(frozen=True)
class CardImage:
    content_type: str
    data: bytes

    @property
    def size_bytes(self) -> int:
        return len(self.data)


def decode_card_image(data_url: str, *, side: str = "card") -> CardImage:
    """A data URL → the image bytes and their (verified) content type; anything else raises
    InsuranceCardError with a message fit for the patient."""
    if not isinstance(data_url, str):
        raise InsuranceCardError(f"The {side} of the insurance card must be a JPEG, PNG or WebP photo.")
    m = _DATA_URL.match(data_url.strip())
    if not m:
        raise InsuranceCardError(f"The {side} of the insurance card must be a JPEG, PNG or WebP photo.")
    declared = "image/jpeg" if m.group(1) == "image/jpg" else m.group(1)
    encoded = m.group(2)
    # Cheap pre-check before decoding: base64 is 4/3 of the payload.
    if len(encoded) > MAX_CARD_BYTES * 4 // 3 + 4:
        raise InsuranceCardError(
            f"The {side} of the insurance card is too large (more than {MAX_CARD_BYTES // (1024 * 1024)} MB)."
        )
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise InsuranceCardError(f"The {side} of the insurance card is not a valid image file.") from exc
    if len(raw) < MIN_CARD_BYTES:
        raise InsuranceCardError(f"The {side} of the insurance card looks empty. Please try another photo.")
    if len(raw) > MAX_CARD_BYTES:
        raise InsuranceCardError(
            f"The {side} of the insurance card is too large (more than {MAX_CARD_BYTES // (1024 * 1024)} MB)."
        )
    actual = sniff_content_type(raw)
    if actual is None or actual != declared:
        raise InsuranceCardError(f"The {side} of the insurance card is not a JPEG, PNG or WebP image.")
    return CardImage(content_type=actual, data=raw)


def sniff_content_type(raw: bytes) -> str | None:
    """The image type the bytes really are (magic numbers), or None."""
    for content_type, magics in CONTENT_TYPES.items():
        if any(raw.startswith(magic) for magic in magics):
            if content_type == "image/webp" and raw[8:12] != b"WEBP":
                continue
            return content_type
    return None
