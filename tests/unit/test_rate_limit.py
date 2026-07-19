"""Unit tests for the in-memory ``RateLimiter``.

The limiter is the only piece of intake-side abuse mitigation in v0.1
(per-IP, sliding window, process-local). These tests pin its observable
behavior — concurrency safety is left to the integration layer.
"""

from __future__ import annotations

import time

import pytest

from bug_fab._rate_limit import RateLimiter, resolve_client_ip

# -----------------------------------------------------------------------------
# Basic enforcement
# -----------------------------------------------------------------------------


def test_under_limit_allows_request() -> None:
    limiter = RateLimiter(max_per_window=3, window_seconds=60)
    assert limiter.check("1.2.3.4") is True


def test_at_limit_rejects_next_request() -> None:
    limiter = RateLimiter(max_per_window=2, window_seconds=60)
    assert limiter.check("1.2.3.4") is True
    assert limiter.check("1.2.3.4") is True
    assert limiter.check("1.2.3.4") is False


def test_repeated_rejections_remain_rejected() -> None:
    """Once over the limit, subsequent checks within the window stay rejected."""
    limiter = RateLimiter(max_per_window=1, window_seconds=60)
    assert limiter.check("1.2.3.4") is True
    for _ in range(5):
        assert limiter.check("1.2.3.4") is False


# -----------------------------------------------------------------------------
# Window expiry / sliding behavior
# -----------------------------------------------------------------------------


def test_window_expiry_releases_old_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the window slides past old timestamps, fresh requests are allowed."""
    fake_now = [1000.0]

    def fake_monotonic() -> float:
        return fake_now[0]

    monkeypatch.setattr("bug_fab._rate_limit.time.monotonic", fake_monotonic)

    limiter = RateLimiter(max_per_window=2, window_seconds=10)
    assert limiter.check("1.2.3.4") is True  # t=1000, count=1
    assert limiter.check("1.2.3.4") is True  # t=1000, count=2
    assert limiter.check("1.2.3.4") is False  # at limit

    # Slide past the 10-second window
    fake_now[0] = 1015.0
    assert limiter.check("1.2.3.4") is True  # window cleared


def test_partial_window_slide_keeps_partial_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the entries past the cutoff drop — the rest still count."""
    fake_now = [1000.0]

    def fake_monotonic() -> float:
        return fake_now[0]

    monkeypatch.setattr("bug_fab._rate_limit.time.monotonic", fake_monotonic)

    limiter = RateLimiter(max_per_window=3, window_seconds=10)
    assert limiter.check("1.2.3.4") is True  # t=1000
    fake_now[0] = 1005.0
    assert limiter.check("1.2.3.4") is True  # t=1005
    assert limiter.check("1.2.3.4") is True  # t=1005, at limit (count=3)
    assert limiter.check("1.2.3.4") is False
    # Slide so only the first entry expires
    fake_now[0] = 1011.0
    assert limiter.check("1.2.3.4") is True  # one slot freed


# -----------------------------------------------------------------------------
# Multi-IP isolation
# -----------------------------------------------------------------------------


def test_multiple_ips_have_independent_buckets() -> None:
    limiter = RateLimiter(max_per_window=1, window_seconds=60)
    assert limiter.check("1.1.1.1") is True
    assert limiter.check("1.1.1.1") is False
    # A different IP starts fresh
    assert limiter.check("2.2.2.2") is True


# -----------------------------------------------------------------------------
# Edge configurations
# -----------------------------------------------------------------------------


@pytest.mark.parametrize("max_per", [0, -1, -100])
def test_max_zero_or_negative_disables_limiter(max_per: int) -> None:
    """Non-positive max disables the limiter — every check returns True."""
    limiter = RateLimiter(max_per_window=max_per, window_seconds=60)
    for _ in range(20):
        assert limiter.check("any") is True


@pytest.mark.parametrize("window", [0, -10])
def test_non_positive_window_clamps_to_one_second(window: int) -> None:
    """Defensive defaults: zero or negative window becomes a 1-second window."""
    limiter = RateLimiter(max_per_window=2, window_seconds=window)
    # The window is clamped, so we should be able to enforce limits within 1s
    assert limiter.check("a") is True
    assert limiter.check("a") is True
    assert limiter.check("a") is False


def test_reset_clears_all_tracked_state() -> None:
    limiter = RateLimiter(max_per_window=1, window_seconds=60)
    assert limiter.check("1.1.1.1") is True
    assert limiter.check("1.1.1.1") is False
    limiter.reset()
    assert limiter.check("1.1.1.1") is True


def test_real_clock_window_expires() -> None:
    """Real-clock smoke check — short window, real ``time.sleep``.

    Kept tight (0.1s window) to avoid slowing the suite. Skipped only if
    machine timing is too jittery to honor it.
    """
    limiter = RateLimiter(max_per_window=1, window_seconds=1)
    assert limiter.check("ip") is True
    assert limiter.check("ip") is False
    # The sliding window is 1 second (clamped from anything <=0); real sleep
    # is acceptable here because it is bounded and the test value is small.
    time.sleep(1.05)
    assert limiter.check("ip") is True


# -----------------------------------------------------------------------------
# Idle-bucket eviction (C7 — unbounded-memory guard)
# -----------------------------------------------------------------------------


def test_idle_buckets_are_evicted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Buckets whose window fully expired are removed, not retained forever.

    Without eviction, an attacker cycling through source keys (the trivial
    outcome of a spoofed ``X-Forwarded-For``) would grow the map without
    bound — the very DoS that enabling the limiter is meant to prevent.
    """
    fake_now = [1000.0]
    monkeypatch.setattr("bug_fab._rate_limit.time.monotonic", lambda: fake_now[0])

    limiter = RateLimiter(max_per_window=5, window_seconds=10)
    assert limiter.check("1.1.1.1") is True
    assert limiter.check("2.2.2.2") is True
    assert set(limiter._events) == {"1.1.1.1", "2.2.2.2"}

    # Slide past the window so both buckets are stale; a request from a third
    # IP triggers the once-per-window sweep.
    fake_now[0] = 1020.0
    assert limiter.check("3.3.3.3") is True
    assert set(limiter._events) == {"3.3.3.3"}


def test_sweep_keeps_still_active_buckets(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bucket with a live entry survives a sweep; only fully-expired ones go."""
    fake_now = [1000.0]
    monkeypatch.setattr("bug_fab._rate_limit.time.monotonic", lambda: fake_now[0])

    limiter = RateLimiter(max_per_window=5, window_seconds=10)
    assert limiter.check("keep") is True  # t=1000
    fake_now[0] = 1008.0
    assert limiter.check("keep") is True  # t=1008 — refreshes the bucket
    # Sweep fires (1012 - 1000 >= 10); cutoff=1002, so the t=1008 entry is live.
    fake_now[0] = 1012.0
    assert limiter.check("other") is True
    assert "keep" in limiter._events


# -----------------------------------------------------------------------------
# Trusted-proxy IP resolution (S3f — spoofable X-Forwarded-For)
# -----------------------------------------------------------------------------


def test_resolve_ignores_forwarded_for_from_untrusted_peer() -> None:
    """Secure default: an empty trust set meters by peer and ignores the header."""
    assert resolve_client_ip("203.0.113.5", "9.9.9.9", frozenset()) == "203.0.113.5"


def test_resolve_honors_forwarded_for_from_trusted_peer() -> None:
    """When the peer is a trusted proxy, the first forwarded hop is the key."""
    key = resolve_client_ip("10.0.0.1", "9.9.9.9, 7.7.7.7", frozenset({"10.0.0.1"}))
    assert key == "9.9.9.9"


def test_resolve_wildcard_trusts_every_peer() -> None:
    """``"*"`` restores the old always-trust behavior as an explicit opt-in."""
    assert resolve_client_ip("203.0.113.5", "9.9.9.9", frozenset({"*"})) == "9.9.9.9"


def test_resolve_trusted_peer_without_forwarded_for_uses_peer() -> None:
    assert resolve_client_ip("10.0.0.1", None, frozenset({"10.0.0.1"})) == "10.0.0.1"


def test_resolve_trusted_peer_blank_forwarded_for_uses_peer() -> None:
    assert resolve_client_ip("10.0.0.1", "   ", frozenset({"10.0.0.1"})) == "10.0.0.1"


def test_resolve_unknown_when_no_peer() -> None:
    assert resolve_client_ip(None, None, frozenset()) == "unknown"


def test_spoofed_forwarded_for_cannot_defeat_the_limiter() -> None:
    """End-to-end intent: rotating a spoofed header from an untrusted peer
    keeps landing in the same peer-keyed bucket, so the limit still bites."""
    limiter = RateLimiter(max_per_window=1, window_seconds=60)
    trusted: frozenset[str] = frozenset()
    peer = "203.0.113.5"
    assert limiter.check(resolve_client_ip(peer, "1.1.1.1", trusted)) is True
    assert limiter.check(resolve_client_ip(peer, "2.2.2.2", trusted)) is False
    assert limiter.check(resolve_client_ip(peer, "3.3.3.3", trusted)) is False
