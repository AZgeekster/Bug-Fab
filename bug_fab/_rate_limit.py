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
from collections.abc import Container
from threading import Lock


def resolve_client_ip(
    peer: str | None,
    forwarded_for: str | None,
    trusted_proxies: Container[str],
) -> str:
    """Resolve the rate-limit key, honoring ``X-Forwarded-For`` only when safe.

    ``X-Forwarded-For`` is client-controlled and trivially spoofed: an
    attacker who can rotate the header on every request lands in a fresh
    bucket each time and defeats the limiter entirely. It is therefore
    honored **only** when the direct connection ``peer`` is a configured
    trusted proxy — i.e. ``peer`` is in ``trusted_proxies``, or
    ``trusted_proxies`` contains the wildcard ``"*"`` (opt back into the
    old always-trust behavior for a deployment that terminates every
    request behind a proxy it controls). Otherwise the direct ``peer``
    address is used.

    Only the first hop of ``X-Forwarded-For`` is read; the remaining hops
    are further from the edge and equally spoofable. Returns ``"unknown"``
    when no address is available so the limiter still sees a stable key.
    """
    if forwarded_for and (peer in trusted_proxies or "*" in trusted_proxies):
        first = forwarded_for.split(",", 1)[0].strip()
        if first:
            return first
    return peer or "unknown"


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
    entries outside the window on every ``check`` call. This keeps each
    active bucket bounded by ``max_per_window``. Idle buckets are evicted
    by a sweep that runs at most once per window (amortized O(1) per
    ``check``): without it, an attacker cycling through source keys — the
    trivial outcome of a spoofed ``X-Forwarded-For`` — would grow the map
    without bound, so *enabling* the limiter would itself be a
    memory-exhaustion sink. Pruning and sweeping run under a
    ``threading.Lock`` so concurrent FastAPI worker threads in the same
    process see a consistent view; async callers acquire the lock for the
    brief, non-blocking critical section.
    """

    def __init__(self, max_per_window: int, window_seconds: int) -> None:
        self._max = max_per_window
        self._window = max(int(window_seconds), 1)
        self._events: dict[str, list[float]] = {}
        self._lock = Lock()
        self._last_sweep = time.monotonic()

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
            self._sweep(now, cutoff)
            # ``.get`` (not ``[]``) so a rejected/one-off key is not
            # auto-created here — buckets only ever exist because a request
            # was recorded against them, which keeps the sweep's job small.
            timestamps = self._events.get(ip)
            # Drop expired entries so the window slides forward.
            fresh = [t for t in timestamps if t > cutoff] if timestamps else []
            if len(fresh) >= self._max:
                self._events[ip] = fresh
                return False
            fresh.append(now)
            self._events[ip] = fresh
            return True

    def _sweep(self, now: float, cutoff: float) -> None:
        """Evict buckets whose entire window has expired.

        Called under ``self._lock``. Throttled to at most once per window
        so the full scan is amortized O(1) across ``check`` calls. Because
        timestamps are appended in monotonic order, a bucket whose newest
        entry is at or before ``cutoff`` has no live entries and is removed.
        """
        if now - self._last_sweep < self._window:
            return
        self._last_sweep = now
        stale = [key for key, ts in self._events.items() if not ts or ts[-1] <= cutoff]
        for key in stale:
            del self._events[key]

    def reset(self) -> None:
        """Clear all tracked timestamps. Primarily useful in tests."""
        with self._lock:
            self._events.clear()
            self._last_sweep = time.monotonic()
