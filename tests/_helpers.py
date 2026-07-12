"""Shared test helpers, deduplicated from per-module copies.

The coroutine driver, the httpx mock harness, the JSON body decoder, and
the synthetic ``BugReportDetail`` dict used to exist as 7–11 near-identical
copies across the integration and unit modules — and the copies had already
drifted (one module grew a second, divergent transport installer; every
``_run`` copy leaked the event loop it created). Fixing a harness bug used
to require seven synchronized edits; now it requires one.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx

#: The true ``httpx.AsyncClient``, captured at import time. Installers
#: subclass THIS instead of whatever ``httpx.AsyncClient`` currently points
#: at, so installing a second transport inside one test replaces the first
#: instead of stacking on top of it — the old per-module copies documented
#: a "chained patches compound; only the first transport fires" limitation
#: that this removes.
_REAL_ASYNC_CLIENT = httpx.AsyncClient


def run_coro(coro: Any) -> Any:
    """Drive a coroutine to completion and return its result.

    ``asyncio.run`` creates AND closes its loop. The old per-module
    ``_run`` copies each ran ``asyncio.new_event_loop().run_until_complete``
    and leaked every loop they created — one warning per call under
    warnings-as-errors.
    """
    return asyncio.run(coro)


def install_capturing_async_client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> list[httpx.Request]:
    """Patch the global ``httpx.AsyncClient`` to route through ``handler``.

    Every request is appended to the returned list (in order) before the
    handler produces its response, so tests can assert on headers and
    decoded bodies post-hoc. The patch is global — all integration modules
    reach httpx through the same module object — and is restored after
    every test by the autouse ``_restore_httpx_async_client`` fixture in
    ``tests/conftest.py``.
    """
    captured: list[httpx.Request] = []

    def _wrapped(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    transport = httpx.MockTransport(_wrapped)

    class _MockClient(_REAL_ASYNC_CLIENT):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    httpx.AsyncClient = _MockClient  # type: ignore[misc]
    return captured


def restore_async_client() -> None:
    """Put the real ``httpx.AsyncClient`` back (used by the autouse fixture)."""
    httpx.AsyncClient = _REAL_ASYNC_CLIENT  # type: ignore[misc]


def decode_json_body(req: httpx.Request) -> dict[str, Any]:
    """Read + JSON-decode a captured request's body."""
    return json.loads(req.content.decode("utf-8"))


def make_report_detail(**overrides: Any) -> dict[str, Any]:
    """Canonical synthetic ``BugReportDetail``-shaped dict for outbound tests.

    Fully populated so individual tests override one field at a time
    without rebuilding the whole shape. This is the single source of truth
    for the wire shape in integration tests — a field added to the
    protocol gets added here once, not in four module-level copies.
    """
    base: dict[str, Any] = {
        "id": "bug-001",
        "title": "Login button does not respond",
        "report_type": "bug",
        "severity": "critical",
        "status": "open",
        "module": "auth",
        "created_at": "2026-05-20T12:00:00+00:00",
        "description": "Clicking the login button does nothing on Firefox 130.",
        "environment": "production",
        "reporter": {"name": "Alice", "email": "", "user_id": ""},
        "github_issue_url": None,
    }
    base.update(overrides)
    return base


def clear_env_prefix(monkeypatch: Any, prefix: str) -> None:
    """Strip every env var starting with ``prefix`` for this test.

    ``from_env`` tests that ``delenv`` only the two variables they name
    fail spuriously on any machine exporting other ``BUG_FAB_*`` vars —
    generalized from the Linear module's hermetic fixture.
    """
    import os

    for key in list(os.environ):
        if key.startswith(prefix):
            monkeypatch.delenv(key, raising=False)


def baseline_metadata(**overrides: Any) -> dict[str, Any]:
    """Canonical, all-fields-populated intake metadata dict.

    The single source of truth for the submission wire shape in tests —
    the root conftest's ``valid_metadata_dict`` fixture and the router
    modules' ``_baseline_metadata`` helpers all build on this, so a new
    protocol field lands everywhere with one edit. A ``context`` override
    deep-merges into the default context instead of replacing it, so
    modules can tweak one sub-key without restating the whole block.
    """
    context: dict[str, Any] = {
        "url": "http://localhost/sample/path",
        "module": "sample",
        "user_agent": "bug-fab-tests/0.1",
        "viewport_width": 1280,
        "viewport_height": 720,
        "console_errors": [],
        "network_log": [],
        "source_mapping": {},
        "app_version": "0.1.0",
        "environment": "dev",
    }
    context.update(overrides.pop("context", {}))
    payload: dict[str, Any] = {
        "protocol_version": "0.1",
        "title": "Submit form does not clear after success",
        "client_ts": "2026-04-29T12:00:00+00:00",
        "report_type": "bug",
        "description": "Steps: open page; submit; observe stale form fields.",
        "expected_behavior": "Form clears on successful submission.",
        "severity": "medium",
        "tags": ["regression", "ui"],
        "reporter": {"name": "", "email": "", "user_id": ""},
        "context": context,
    }
    payload.update(overrides)
    return payload


def make_test_png(width: int = 4, height: int = 4) -> bytes:
    """Hand-rolled minimal valid PNG (solid red).

    The single source of truth for test screenshots — the root conftest's
    fixtures delegate here, and the Django conftest / e2e smoke module used
    to carry their own hardcoded 1x1 literals. (The conformance fixture
    helper is deliberately NOT imported: pytest plugin loading would pick
    it up as a conformance test on collection.)
    """
    import struct
    import zlib

    if width < 1 or height < 1:
        raise ValueError("width and height must be >= 1")
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    ihdr_crc = zlib.crc32(b"IHDR" + ihdr_data)
    ihdr = struct.pack(">I", len(ihdr_data)) + b"IHDR" + ihdr_data + struct.pack(">I", ihdr_crc)
    raw = b""
    for _ in range(height):
        raw += b"\x00" + (b"\xff\x00\x00" * width)
    idat_data = zlib.compress(raw)
    idat_crc = zlib.crc32(b"IDAT" + idat_data)
    idat = struct.pack(">I", len(idat_data)) + b"IDAT" + idat_data + struct.pack(">I", idat_crc)
    iend_crc = zlib.crc32(b"IEND")
    iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", iend_crc)
    return signature + ihdr + idat + iend
