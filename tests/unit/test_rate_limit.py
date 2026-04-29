"""Unit tests for the in-memory ``RateLimiter``.

The limiter is the only piece of intake-side abuse mitigation in v0.1
(per-IP, sliding window, process-local). These tests pin its observable
behavior — concurrency safety is left to the integration layer.
"""

from __future__ import annotations

import time

import pytest

from bug_fab._rate_limit import RateLimiter

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
