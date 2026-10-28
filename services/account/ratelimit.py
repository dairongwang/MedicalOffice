"""In-memory sliding-window limiters (standard library only, per process).

``RateLimiter`` caps *calls* per key — the anonymous ad endpoints use it per caller address.
``FailureLimiter`` caps *failed attempts* per key — the second factor of a login (a six-digit
code must not be guessable by looping) and password re-checks use it per account.

Both keep nothing but a salted hash of the key and a handful of timestamps, and only for the
length of the window (PLATFORM_DESIGN.md: rate-limit counters are the one place an address is
touched at all, never for longer than the window).
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import deque


class RateLimiter:
    """Sliding-window limiter for the anonymous endpoints, so nobody can inflate the counters
    from a loop. Keys are a per-process salted hash of the caller's address kept **in memory
    only** for the window."""

    def __init__(self, limit: int, window_seconds: int):
        self.limit = limit
        self.window = window_seconds
        self._salt = secrets.token_bytes(16)
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def key(self, address: str | None) -> str:
        return hashlib.sha256(self._salt + (address or "?").encode()).hexdigest()[:32]

    def allow(self, address: str | None, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        k = self.key(address)
        with self._lock:
            q = self._hits.setdefault(k, deque())
            while q and q[0] <= now - self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            if len(self._hits) > 10_000:  # housekeeping: forget idle callers
                for stale in [key for key, hits in self._hits.items() if not hits or hits[-1] <= now - self.window]:
                    del self._hits[stale]
            return True


class FailureLimiter:
    """Counts *failures* per key: after ``limit`` of them within ``window_seconds`` the key is
    blocked until the oldest failure falls out of the window. A success clears the key.

    Used for guessable secrets (TOTP codes, recovery codes) and for password re-checks on
    sensitive account actions — always keyed by the account, so an attacker cannot dodge it by
    changing address, and a legitimate user is never locked out by *somebody else's* failures
    against another account."""

    def __init__(self, limit: int, window_seconds: int):
        self.limit = limit
        self.window = window_seconds
        self._salt = secrets.token_bytes(16)
        self._failures: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _key(self, key: str) -> str:
        return hashlib.sha256(self._salt + key.encode()).hexdigest()[:32]

    def _prune(self, q: deque[float], now: float) -> None:
        while q and q[0] <= now - self.window:
            q.popleft()

    def blocked(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            q = self._failures.get(self._key(key))
            if q is None:
                return False
            self._prune(q, now)
            return len(q) >= self.limit

    def retry_after(self, key: str, now: float | None = None) -> int:
        """Seconds until a blocked key is allowed again (0 when not blocked)."""
        now = time.monotonic() if now is None else now
        with self._lock:
            q = self._failures.get(self._key(key))
            if not q:
                return 0
            self._prune(q, now)
            if len(q) < self.limit:
                return 0
            return max(1, int(q[0] + self.window - now) + 1)

    def failed(self, key: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            q = self._failures.setdefault(self._key(key), deque())
            self._prune(q, now)
            q.append(now)
            if len(self._failures) > 10_000:  # housekeeping: forget keys whose failures all expired
                for stale in [k for k, hits in self._failures.items() if not hits or hits[-1] <= now - self.window]:
                    del self._failures[stale]

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(self._key(key), None)
