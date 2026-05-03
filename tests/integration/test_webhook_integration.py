"""Integration tests for the optional generic webhook delivery.

External HTTP is captured via :class:`httpx.MockTransport` (no
``respx`` dep) — the same harness shape ``test_github_integration``
uses. The tests verify that:

* ``WebhookSync.send`` POSTs the report payload as JSON to the
  configured URL with the documented ``Content-Type`` plus any
  consumer-supplied headers.
* Non-2xx responses, transport errors, and timeouts ALL return
  ``False`` rather than raising — preserving the failure-tolerance
  contract that webhook outages must NOT block intake.
* Wired into the FastAPI submit router via the ``webhook_sync``
  dependency, intake still returns 201 even when the webhook errors.
* When the integration is disabled (``webhook_sync=None``), no
  outbound calls are made.
* Headers configured through ``Settings.webhook_headers`` /
  ``BUG_FAB_WEBHOOK_HEADERS`` reach the wire.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from bug_fab.config import Settings
from bug_fab.integrations.webhook import (
    DEFAULT_TIMEOUT_SECONDS,
    WebhookSync,
    parse_headers_env,
)


def _run(coro):  # type: ignore[no-untyped-def]
    return asyncio.new_event_loop().run_until_complete(coro)


def _make_sync_with_transport(
    handler,  # type: ignore[no-untyped-def]
    *,
    url: str = "https://hooks.example.test/post",
    headers: dict[str, str] | None = None,
    timeout_seconds: float = 5.0,
) -> tuple[WebhookSync, list[httpx.Request]]:
    """Build a WebhookSync whose internal client uses a MockTransport.

    Mirrors the helper in ``test_github_integration.py``. Returns
    ``(sync, captured_requests)``; the captured list is appended to in
    order so post-hoc assertions can inspect headers and body.
    """
    captured: list[httpx.Request] = []

    def _wrapped(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    transport = httpx.MockTransport(_wrapped)

    import bug_fab.integrations.webhook as webhook_module

    real_client = httpx.AsyncClient

    class _MockClient(real_client):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    webhook_module.httpx.AsyncClient = _MockClient  # type: ignore[attr-defined]
    sync = WebhookSync(url, headers=headers, timeout_seconds=timeout_seconds)
    return sync, captured


@pytest.fixture(autouse=True)
def _restore_httpx_async_client():  # type: ignore[no-untyped-def]
    """Restore ``httpx.AsyncClient`` after every test (defensive)."""
    import bug_fab.integrations.webhook as webhook_module

    original = webhook_module.httpx.AsyncClient
    yield
    webhook_module.httpx.AsyncClient = original


def _sample_report() -> dict[str, Any]:
    return {
        "id": "bug-001",
        "title": "Submit form does not clear",
        "report_type": "bug",
        "severity": "high",
        "status": "open",
        "module": "ui",
        "environment": "dev",
        "created_at": "2026-05-01T15:00:00Z",
        "github_issue_url": None,
        "context": {"url": "/sample"},
    }


# -----------------------------------------------------------------------------
# WebhookSync.send happy path + headers
# -----------------------------------------------------------------------------


def test_send_posts_full_report_json_to_configured_url() -> None:
    """A 2xx response yields ``True`` and the body matches the report dict."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    sync, captured = _make_sync_with_transport(handler)
    ok = _run(sync.send(_sample_report()))
    assert ok is True
    assert len(captured) == 1
    posted = captured[0]
    assert posted.method == "POST"
    assert str(posted.url) == "https://hooks.example.test/post"
    body = json.loads(posted.content)
    assert body["id"] == "bug-001"
    assert body["severity"] == "high"
    # The webhook receives the full BugReportDetail-shaped payload, not
    # just the intake envelope.
    assert "context" in body


def test_send_includes_default_content_type_header() -> None:
    """``Content-Type: application/json`` is always sent."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    sync, captured = _make_sync_with_transport(handler)
    _run(sync.send(_sample_report()))
    assert captured[0].headers["content-type"].startswith("application/json")


def test_send_honors_consumer_supplied_headers() -> None:
    """Custom headers (auth, source markers) reach the wire."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    sync, captured = _make_sync_with_transport(
        handler,
        headers={"Authorization": "Bearer abc123", "X-Bug-Fab-Source": "test"},
    )
    _run(sync.send(_sample_report()))
    headers = captured[0].headers
    assert headers["authorization"] == "Bearer abc123"
    assert headers["x-bug-fab-source"] == "test"


def test_send_returns_false_on_non_2xx_response() -> None:
    """Non-2xx responses log + return ``False`` (no exception)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream broken")

    sync, _ = _make_sync_with_transport(handler)
    assert _run(sync.send(_sample_report())) is False


def test_send_returns_false_on_4xx_response() -> None:
    """4xx is also a soft failure — no raise, just log + return False."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="webhook url unknown")

    sync, _ = _make_sync_with_transport(handler)
    assert _run(sync.send(_sample_report())) is False


def test_send_returns_false_on_transport_error() -> None:
    """Connection-level errors are caught and surfaced as ``False``."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    sync, _ = _make_sync_with_transport(handler)
    assert _run(sync.send(_sample_report())) is False


def test_send_returns_false_on_timeout() -> None:
    """Per-request timeout is also a soft failure."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    sync, _ = _make_sync_with_transport(handler)
    assert _run(sync.send(_sample_report())) is False


# -----------------------------------------------------------------------------
# parse_headers_env
# -----------------------------------------------------------------------------


def test_parse_headers_env_accepts_json() -> None:
    """A JSON object string decodes into the canonical header dict."""
    result = parse_headers_env('{"Authorization": "Bearer xyz", "X-Source": "bf"}')
    assert result == {"Authorization": "Bearer xyz", "X-Source": "bf"}


def test_parse_headers_env_accepts_semicolon_pairs() -> None:
    """``key=value;key2=value2`` is the shell-friendly fallback format."""
    result = parse_headers_env("Authorization=Bearer xyz;X-Source=bf")
    assert result == {"Authorization": "Bearer xyz", "X-Source": "bf"}


def test_parse_headers_env_returns_empty_on_invalid_json() -> None:
    """A malformed JSON string falls back to an empty dict (no raise)."""
    assert parse_headers_env("{not json at all") == {}


def test_parse_headers_env_handles_missing_or_empty_input() -> None:
    """Unset / empty / whitespace-only input yields an empty dict."""
    assert parse_headers_env(None) == {}
    assert parse_headers_env("") == {}
    assert parse_headers_env("   ") == {}


def test_parse_headers_env_skips_malformed_pairs() -> None:
    """Pairs without ``=`` and pairs with empty keys are dropped silently."""
    result = parse_headers_env("Authorization=Bearer xyz;junk;=novalue;X-Ok=1")
    assert result == {"Authorization": "Bearer xyz", "X-Ok": "1"}


# -----------------------------------------------------------------------------
# Settings.from_env wiring
# -----------------------------------------------------------------------------


def test_settings_from_env_picks_up_webhook_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """All four webhook env vars round-trip onto the Settings instance."""
    monkeypatch.setenv("BUG_FAB_WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("BUG_FAB_WEBHOOK_URL", "https://hook.test/in")
    monkeypatch.setenv("BUG_FAB_WEBHOOK_HEADERS", '{"X-Token": "secret"}')
    monkeypatch.setenv("BUG_FAB_WEBHOOK_TIMEOUT_SECONDS", "12.5")
    settings = Settings.from_env()
    assert settings.webhook_enabled is True
    assert settings.webhook_url == "https://hook.test/in"
    assert settings.webhook_headers == {"X-Token": "secret"}
    assert settings.webhook_timeout_seconds == 12.5


def test_settings_defaults_disable_webhook() -> None:
    """With nothing set, the webhook is off and fields hold their defaults."""
    # Construct ``Settings()`` directly to bypass any ambient env vars.
    settings = Settings()
    assert settings.webhook_enabled is False
    assert settings.webhook_url == ""
    assert settings.webhook_headers == {}
    assert settings.webhook_timeout_seconds == DEFAULT_TIMEOUT_SECONDS


def test_settings_from_env_invalid_timeout_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-numeric timeout env var falls back to the documented default."""
    monkeypatch.setenv("BUG_FAB_WEBHOOK_TIMEOUT_SECONDS", "fast-please")
    settings = Settings.from_env()
    assert settings.webhook_timeout_seconds == 5.0


# -----------------------------------------------------------------------------
# Submit-router integration: webhook fires after intake (and after GitHub)
# -----------------------------------------------------------------------------


def _baseline_metadata() -> dict[str, Any]:
    return {
        "protocol_version": "0.1",
        "title": "webhook integration probe",
        "client_ts": "2026-05-01T12:00:00+00:00",
        "context": {},
    }


def test_submit_does_not_call_webhook_when_disabled(app_factory, tiny_png: bytes) -> None:
    """``webhook_sync=None`` means no outbound webhook traffic."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={})

    # Even if the transport were monkey-patched, the router shouldn't
    # construct a client because webhook_sync=None.
    import bug_fab.integrations.webhook as webhook_module

    original = webhook_module.httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    class _MockClient(original):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    webhook_module.httpx.AsyncClient = _MockClient  # type: ignore[attr-defined]
    try:
        client = app_factory(webhook_sync=None)
        response = client.post(
            "/bug-reports",
            data={"metadata": json.dumps(_baseline_metadata())},
            files={"screenshot": ("shot.png", tiny_png, "image/png")},
        )
        assert response.status_code == 201
        assert captured == []
    finally:
        webhook_module.httpx.AsyncClient = original


def test_submit_calls_webhook_with_full_report_payload(app_factory, tiny_png: bytes) -> None:
    """Configured webhook receives the full BugReportDetail JSON."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"ok": True})

    sync, _ = _make_sync_with_transport(handler)
    client = app_factory(webhook_sync=sync)
    response = client.post(
        "/bug-reports",
        data={"metadata": json.dumps(_baseline_metadata())},
        files={"screenshot": ("shot.png", tiny_png, "image/png")},
    )
    assert response.status_code == 201
    assert len(captured) == 1
    posted = json.loads(captured[0].content)
    # Full BugReportDetail shape — not just the intake envelope.
    assert posted["id"].startswith("bug-")
    assert posted["title"] == "webhook integration probe"
    assert "context" in posted
    assert "lifecycle" in posted


def test_submit_succeeds_even_when_webhook_returns_500(app_factory, tiny_png: bytes) -> None:
    """A 500 from the webhook MUST NOT block local persistence."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="downstream broken")

    sync, _ = _make_sync_with_transport(handler)
    client = app_factory(webhook_sync=sync)
    response = client.post(
        "/bug-reports",
        data={"metadata": json.dumps(_baseline_metadata())},
        files={"screenshot": ("shot.png", tiny_png, "image/png")},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["id"].startswith("bug-")


def test_submit_succeeds_even_when_webhook_connection_refused(app_factory, tiny_png: bytes) -> None:
    """Transport-level webhook failures also leave intake at 201."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    sync, _ = _make_sync_with_transport(handler)
    client = app_factory(webhook_sync=sync)
    response = client.post(
        "/bug-reports",
        data={"metadata": json.dumps(_baseline_metadata())},
        files={"screenshot": ("shot.png", tiny_png, "image/png")},
    )
    assert response.status_code == 201


def test_submit_webhook_carries_consumer_headers(app_factory, tiny_png: bytes) -> None:
    """``Settings.webhook_headers`` round-trips onto the outbound request."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200)

    sync, _ = _make_sync_with_transport(
        handler,
        headers={"Authorization": "Bearer abc", "X-Bug-Fab-Hook": "1"},
    )
    client = app_factory(webhook_sync=sync)
    response = client.post(
        "/bug-reports",
        data={"metadata": json.dumps(_baseline_metadata())},
        files={"screenshot": ("shot.png", tiny_png, "image/png")},
    )
    assert response.status_code == 201
    assert len(captured) == 1
    assert captured[0].headers["authorization"] == "Bearer abc"
    assert captured[0].headers["x-bug-fab-hook"] == "1"


# -----------------------------------------------------------------------------
# Logging assertion — failures emit a structured log line
# -----------------------------------------------------------------------------


def test_send_logs_warning_on_non_2xx(caplog: pytest.LogCaptureFixture) -> None:
    """A failed POST emits ``bug_fab_webhook_send_failed`` at WARN."""
    import logging

    caplog.set_level(logging.WARNING, logger="bug_fab.integrations.webhook")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    sync, _ = _make_sync_with_transport(handler)
    _run(sync.send(_sample_report()))
    assert any("bug_fab_webhook_send_failed" in rec.message for rec in caplog.records)
