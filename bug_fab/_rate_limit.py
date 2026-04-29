"""Per-IP in-memory rate limiter for the submission router.

This module ships a simple sliding-window limiter intended for the v0.1
intake endpoint. It is deliberately minimal — Bug-Fab v0.1 has no
``AuthAdapter`` (per-user identity is unavailable to the package), so per-IP
is the closest available proxy. The default for ``rate_limit_enabled`` is
``False`` to avoid surprising consumers; opt-in is one config flag.

Multi-worker caveat
-------------------
The internal state is a process-local ``dict``. When a consumer runs
``uvicorn --workers N`` (or any other multi-process server), each worker
keeps an independent counter. The effective per-IP limit therefore scales
with the worker count and the limiter MUST NOT be relied upon as a hard
abuse boundary in those deployments. Front-door rate limiting (nginx,
Cloudflare, etc.) remains the right answer for production hardening; this
limiter exists to stop accidental floods from a single misbehaving client.

A future v0.2 release will swap this for an ``AuthAdapter``-keyed limiter
that can share state via a pluggable backend (Redis, etc.).
"""

from __future__ import annotations

import time
from collections import defaultdict
from threading import Lock


class RateLimiter:
    """Sliding-window per-IP rate limiter.

    Parameters
    ----------
    max_per_window:
        Maximum number of allowed events per source IP within
        ``window_seconds``. Values <= 0 disable the limiter (every check
        returns True).
    window_seconds:
        Width of the sliding window in seconds. Values <= 0 are treated as
        a 1-second window (defensive default; the config layer enforces a
        sensible minimum at the edge).

    Notes
    -----
    The implementation stores a list of timestamps per IP and prunes
    entries outside the window on every ``check`` call. This keeps the
    memory profile bounded by ``max_per_window`` per active IP. Pruning
    runs under a ``threading.Lock`` so concurrent FastAPI worker threads
    in the same process see a consistent view; async callers acquire the
    lock for the brief, non-blocking critical section.
    """

    def __init__(self, max_per_window: int, window_seconds: int) -> None:
        self._max = max_per_window
        self._window = max(int(window_seconds), 1)
        self._events: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

    def check(self, ip: str) -> bool:
        """Return True if the caller is under the limit, False otherwise.

        ``ip`` is treated as an opaque key — the caller is responsible for
        deciding whether to use the direct peer address, the
        ``X-Forwarded-For`` header, or some other identifier. The limiter
        does not parse or validate it.
        """
        if self._max <= 0:
            return True
        now = time.monotonic()
        cutoff = now - self._window
        with self._lock:
            timestamps = self._events[ip]
            # Drop expired entries so the window slides forward.
            fresh = [t for t in timestamps if t > cutoff]
            if len(fresh) >= self._max:
                self._events[ip] = fresh
                return False
            fresh.append(now)
            self._events[ip] = fresh
            return True

    def reset(self) -> None:
        """Clear all tracked timestamps. Primarily useful in tests."""
        with self._lock:
            self._events.clear()
