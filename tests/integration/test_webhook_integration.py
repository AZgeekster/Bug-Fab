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


# -----------------------------------------------------------------------------
# Retry-with-backoff + dead-letter queue (added 2026-05-20)
# -----------------------------------------------------------------------------


def _install_transport(handler) -> None:  # type: ignore[no-untyped-def]
    """Patch httpx.AsyncClient to use the given handler. Restored by fixture."""
    transport = httpx.MockTransport(handler)
    import bug_fab.integrations.webhook as webhook_module

    real_client = webhook_module.httpx.AsyncClient

    class _MockClient(real_client):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    webhook_module.httpx.AsyncClient = _MockClient  # type: ignore[attr-defined]


def test_max_attempts_default_is_one_no_retry() -> None:
    """Historical fire-and-forget shape preserved: max_attempts=1 = no retry."""
    calls: list[int] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503)

    _install_transport(handler)
    sync = WebhookSync("https://hooks.example.test/post")
    assert _run(sync.send(_sample_report())) is False
    assert len(calls) == 1


def test_5xx_triggers_retry_until_success() -> None:
    """A 5xx becomes 2xx on the third attempt; final result is True."""
    attempts: list[int] = []

    def handler(req: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": True})

    _install_transport(handler)
    sync = WebhookSync(
        "https://hooks.example.test/post",
        max_attempts=5,
        retry_backoff_seconds=0.0,  # zero backoff in tests for speed
    )
    assert _run(sync.send(_sample_report())) is True
    assert len(attempts) == 3


def test_4xx_does_not_retry_even_with_high_max_attempts() -> None:
    """403/422/etc. mean the receiver rejected the body; retrying never helps."""
    attempts: list[int] = []

    def handler(req: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(403, text="forbidden")

    _install_transport(handler)
    sync = WebhookSync("https://hooks.example.test/post", max_attempts=5, retry_backoff_seconds=0.0)
    assert _run(sync.send(_sample_report())) is False
    assert len(attempts) == 1


def test_timeout_classified_as_transient_and_retried() -> None:
    """A timeout on attempt 1 + a 200 on attempt 2 still succeeds overall."""
    attempts: list[int] = []

    def handler(req: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            raise httpx.TimeoutException("slow")
        return httpx.Response(200, json={"ok": True})

    _install_transport(handler)
    sync = WebhookSync("https://hooks.example.test/post", max_attempts=3, retry_backoff_seconds=0.0)
    assert _run(sync.send(_sample_report())) is True
    assert len(attempts) == 2


def test_negative_max_attempts_clamps_to_one() -> None:
    """A bad config value must not silently skip the request entirely."""
    attempts: list[int] = []

    def handler(req: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(200, json={"ok": True})

    _install_transport(handler)
    sync = WebhookSync("https://hooks.example.test/post", max_attempts=-5)
    assert sync.max_attempts == 1
    assert _run(sync.send(_sample_report())) is True
    assert len(attempts) == 1


def test_dlq_writes_envelope_on_terminal_failure(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """All retries exhausted → one JSON envelope appears in dlq_dir."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    _install_transport(handler)
    dlq = tmp_path / "dlq"
    sync = WebhookSync(
        "https://hooks.example.test/post",
        max_attempts=2,
        retry_backoff_seconds=0.0,
        dlq_dir=dlq,
    )
    assert _run(sync.send(_sample_report())) is False
    files = sorted(dlq.glob("*.json"))
    assert len(files) == 1
    envelope = json.loads(files[0].read_text(encoding="utf-8"))
    assert envelope["source_url"] == "https://hooks.example.test/post"
    assert envelope["report"]["id"] == "bug-001"
    assert "HTTP 503" in envelope["last_error"]


def test_dlq_not_written_on_success(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A 2xx response leaves the DLQ dir empty (no false positives)."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    _install_transport(handler)
    dlq = tmp_path / "dlq"
    sync = WebhookSync("https://hooks.example.test/post", max_attempts=3, dlq_dir=dlq)
    assert _run(sync.send(_sample_report())) is True
    if dlq.exists():
        # dir may exist if any caller created it; what matters is no .json files
        assert list(dlq.glob("*.json")) == []


def test_dlq_disabled_when_dir_unset(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """No dlq_dir means failures are logged only — and don't crash."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    _install_transport(handler)
    sync = WebhookSync("https://hooks.example.test/post", max_attempts=2, retry_backoff_seconds=0.0)
    assert _run(sync.send(_sample_report())) is False
    # tmp_path itself is unrelated and remains empty.
    assert list(tmp_path.glob("*.json")) == []


def test_replay_dead_letters_succeeds_and_unlinks(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A previously-failed envelope replays successfully and is removed."""
    from bug_fab.integrations.webhook import replay_dead_letters

    dlq = tmp_path / "dlq"
    dlq.mkdir()

    # Seed a malformed-but-readable envelope.
    envelope = {
        "persisted_at": "2026-05-20T00:00:00Z",
        "source_url": "https://hooks.example.test/post",
        "last_error": "HTTP 503",
        "report": _sample_report(),
    }
    (dlq / "20260520T000000Z_bug-001.json").write_text(json.dumps(envelope), encoding="utf-8")

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    _install_transport(handler)
    sync = WebhookSync("https://hooks.example.test/post", max_attempts=1, dlq_dir=dlq)
    stats = _run(replay_dead_letters(sync, dlq))
    assert stats == {"attempted": 1, "succeeded": 1, "failed": 0, "malformed": 0}
    # File deleted on success.
    assert list(dlq.glob("*.json")) == []


def test_replay_dead_letters_skips_malformed(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Broken JSON in the DLQ is counted as malformed and skipped (not crashed)."""
    from bug_fab.integrations.webhook import replay_dead_letters

    dlq = tmp_path / "dlq"
    dlq.mkdir()
    (dlq / "garbage.json").write_text("{not-json", encoding="utf-8")

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    _install_transport(handler)
    sync = WebhookSync("https://hooks.example.test/post", dlq_dir=dlq)
    stats = _run(replay_dead_letters(sync))
    assert stats["malformed"] == 1
    assert stats["succeeded"] == 0
    # Malformed file is NOT auto-deleted (operator decides what to do).
    assert (dlq / "garbage.json").exists()


def test_replay_dead_letters_keeps_failed_envelopes(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Replay where the receiver is still broken keeps the envelope on disk."""
    from bug_fab.integrations.webhook import replay_dead_letters

    dlq = tmp_path / "dlq"
    dlq.mkdir()
    envelope = {
        "persisted_at": "2026-05-20T00:00:00Z",
        "source_url": "https://hooks.example.test/post",
        "last_error": "HTTP 503",
        "report": _sample_report(),
    }
    fname = "20260520T000000Z_bug-001.json"
    (dlq / fname).write_text(json.dumps(envelope), encoding="utf-8")

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    _install_transport(handler)
    sync = WebhookSync(
        "https://hooks.example.test/post",
        max_attempts=1,
        retry_backoff_seconds=0.0,
        dlq_dir=dlq,
    )
    stats = _run(replay_dead_letters(sync))
    assert stats == {"attempted": 1, "succeeded": 0, "failed": 1, "malformed": 0}
    # Envelope stays — operator must decide whether to retry later or purge.
    assert (dlq / fname).exists()
    # C6: a failed replay must NOT write a *second* dead letter. Asserting the
    # original survives is not enough — the bug left it in place and added a
    # new file. Two more replays against the still-down receiver must keep the
    # count at exactly one, or the DLQ grows N → 2N → 4N.
    for _ in range(2):
        _run(replay_dead_letters(sync))
    assert sorted(p.name for p in dlq.glob("*.json")) == [fname]


def test_replay_dead_letters_returns_zero_stats_when_dir_missing() -> None:
    """A missing/absent DLQ dir is a no-op, not a crash."""
    from bug_fab.integrations.webhook import replay_dead_letters

    sync = WebhookSync("https://hooks.example.test/post")
    stats = _run(replay_dead_letters(sync))
    assert stats == {"attempted": 0, "succeeded": 0, "failed": 0, "malformed": 0}
