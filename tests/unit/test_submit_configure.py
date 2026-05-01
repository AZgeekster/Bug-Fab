"""Unit tests for the submit-router module-level helpers.

These cover the ``configure()`` factory wiring, the dependency-injection
helpers (``get_storage`` / ``get_settings`` / ``get_rate_limiter`` /
``get_github_sync``), and the ``_client_ip`` extraction. They are split
from the integration tests so they can run without spinning up a
``TestClient``.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from bug_fab._rate_limit import RateLimiter
from bug_fab.config import Settings
from bug_fab.routers.submit import (
    _client_ip,
    _is_png,
    configure,
    get_github_sync,
    get_rate_limiter,
    get_settings,
    get_storage,
)
from bug_fab.storage.files import FileStorage


@pytest.fixture(autouse=True)
def _reset_submit_module_state(reset_router_module_state):  # type: ignore[no-untyped-def]
    """Wipe module singletons before AND after every test in this module."""
    reset_router_module_state()
    yield
    reset_router_module_state()


def _make_request(headers: dict[str, str] | None = None, host: str | None = None) -> Request:
    """Build a minimal ASGI Request for the helpers under test."""
    raw_headers = []
    if headers:
        for k, v in headers.items():
            raw_headers.append((k.lower().encode(), v.encode()))
    scope: dict[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": raw_headers,
        "client": ("127.0.0.1", 1234) if host is None else (host, 1234),
    }
    return Request(scope)


def test_get_storage_raises_500_when_unconfigured() -> None:
    """An un-configured router should fail loudly, not silently 200."""
    with pytest.raises(HTTPException) as excinfo:
        get_storage()
    assert excinfo.value.status_code == 500
    assert "not configured" in excinfo.value.detail.lower()


def test_get_settings_falls_back_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing ``configure()`` call returns env-derived defaults."""
    monkeypatch.delenv("BUG_FAB_MAX_UPLOAD_MB", raising=False)
    settings = get_settings()
    assert settings.max_upload_mb == 10  # documented default


def test_get_github_sync_returns_none_when_unconfigured() -> None:
    assert get_github_sync() is None


def test_get_rate_limiter_returns_none_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BUG_FAB_RATE_LIMIT_ENABLED", raising=False)
    assert get_rate_limiter() is None


def test_configure_wires_storage(tmp_path) -> None:  # type: ignore[no-untyped-def]
    storage = FileStorage(tmp_path)
    configure(storage=storage)
    assert get_storage() is storage


def test_configure_uses_provided_settings(tmp_path) -> None:  # type: ignore[no-untyped-def]
    storage = FileStorage(tmp_path)
    settings = Settings(max_upload_mb=42)
    configure(storage=storage, settings=settings)
    assert get_settings() is settings
    assert get_settings().max_upload_mb == 42


def test_configure_creates_rate_limiter() -> None:
    storage = FileStorage("/tmp/bug-fab-conf-test-1")  # type: ignore[arg-type]
    settings = Settings(rate_limit_enabled=True, rate_limit_max=5, rate_limit_window_seconds=10)
    configure(storage=storage, settings=settings)
    limiter = get_rate_limiter()
    assert limiter is not None
    assert isinstance(limiter, RateLimiter)


def test_configure_skips_github_sync_when_pat_missing() -> None:
    storage = FileStorage("/tmp/bug-fab-conf-test-2")  # type: ignore[arg-type]
    settings = Settings(github_enabled=True, github_pat="", github_repo="owner/repo")
    configure(storage=storage, settings=settings)
    assert get_github_sync() is None


def test_configure_creates_github_sync_when_fully_set() -> None:
    storage = FileStorage("/tmp/bug-fab-conf-test-3")  # type: ignore[arg-type]
    settings = Settings(github_enabled=True, github_pat="ghp_x", github_repo="owner/repo")
    configure(storage=storage, settings=settings)
    sync = get_github_sync()
    assert sync is not None


def test_configure_explicit_github_sync_wins() -> None:
    storage = FileStorage("/tmp/bug-fab-conf-test-4")  # type: ignore[arg-type]
    settings = Settings()
    sentinel = object()
    configure(storage=storage, settings=settings, github_sync=sentinel)  # type: ignore[arg-type]
    assert get_github_sync() is sentinel


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def test_client_ip_uses_x_forwarded_for_first_hop() -> None:
    request = _make_request(headers={"x-forwarded-for": "1.2.3.4, 5.6.7.8"})
    assert _client_ip(request) == "1.2.3.4"


def test_client_ip_falls_back_to_request_client_host() -> None:
    request = _make_request(host="9.9.9.9")
    assert _client_ip(request) == "9.9.9.9"


def test_client_ip_returns_unknown_when_no_data() -> None:
    scope: dict[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "client": None,
    }
    request = Request(scope)
    assert _client_ip(request) == "unknown"


@pytest.mark.parametrize(
    "payload,expected",
    [
        (b"\x89PNG\r\n\x1a\nrest", True),
        (b"\xff\xd8\xff\x00rest", False),  # JPEG — rejected per PROTOCOL.md v0.1
        (b"GIF89a", False),
        (b"", False),
        (b"random bytes", False),
    ],
)
def test_is_png(payload: bytes, expected: bool) -> None:
    """Only the PNG magic signature returns True; everything else (including
    JPEG) is rejected. v0.1 locks the wire format to ``image/png``."""
    assert _is_png(payload) is expected
