"""Minimal HTTPS client for provider REST APIs (standard library, identical on every OS).

Returns ``(status, json_body)`` for every answer — 4xx/5xx included — and raises
``TransportError`` only when the other side cannot be reached at all. It is the single seam the
messaging (Twilio) and OAuth (Google, Facebook) adapters have; tests and the built-in fakes
replace it with an in-process object of the same shape.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any


class TransportError(Exception):
    pass


def json_or_empty(raw: bytes) -> dict[str, Any]:
    try:
        data = json.loads(raw or b"{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {"data": data}


class HttpTransport:
    timeout_seconds = 20

    def request(
        self, method: str, url: str, *, headers: Mapping[str, str] | None = None, data: bytes | None = None
    ) -> tuple[int, dict[str, Any]]:
        req = urllib.request.Request(url, data=data, method=method, headers=dict(headers or {}))
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                return resp.status, json_or_empty(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json_or_empty(exc.read())
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise TransportError(str(exc)) from exc
