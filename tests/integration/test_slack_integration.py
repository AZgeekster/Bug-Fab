"""Integration tests for the optional Slack incoming-webhook adapter.

External HTTP is captured via :class:`httpx.MockTransport` — same
harness shape ``test_webhook_integration`` uses. The tests verify
that:

* ``SlackSync.send`` POSTs a Slack Block Kit payload (attachments +
  blocks) to the configured URL with ``Content-Type: application/json``.
* The severity field on the report maps to the documented sidebar
  colors; unknown severities fall back to a neutral default rather
  than crashing.
* The viewer-base-url, when set, renders as a Slack-style ``<url|View>``
  link in the context block.
* Long descriptions are truncated with an ellipsis to keep messages
  scannable in busy channels.
* Non-2xx responses, transport errors, and timeouts ALL return
  ``False`` rather than raising — preserving the failure-tolerance
  contract that Slack outages must NOT block intake.
* ``SlackSync.from_env`` reads ``BUG_FAB_SLACK_*`` env vars and
  returns ``None`` when disabled or unconfigured, so it can be passed
  straight into ``submit.configure(webhook_sync=...)``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from bug_fab.integrations.slack import (
    DEFAULT_COLOR,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_DESCRIPTION_CHARS,
    SEVERITY_COLORS,
    SlackSync,
)


def _run(coro: Any) -> Any:
    """Drive an async coroutine on a fresh event loop, matching webhook tests."""
    return asyncio.new_event_loop().run_until_complete(coro)


def _make_sync_with_transport(
    handler: Any,
    *,
    url: str = "https://hooks.slack.com/services/T00000000/B00000000/XXXXX",
    viewer_base_url: str = "",
    timeout_seconds: float = 5.0,
) -> tuple[SlackSync, list[httpx.Request]]:
    """Build a SlackSync whose internal httpx client uses a MockTransport.

    Returns ``(sync, captured_requests)``; the list is appended to in
    order so post-hoc assertions can inspect headers and decoded body.
    """
    captured: list[httpx.Request] = []

    def _wrapped(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    transport = httpx.MockTransport(_wrapped)

    import bug_fab.integrations.slack as slack_module

    real_client = httpx.AsyncClient

    class _MockClient(real_client):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    slack_module.httpx.AsyncClient = _MockClient  # type: ignore[attr-defined]
    sync = SlackSync(url, viewer_base_url=viewer_base_url, timeout_seconds=timeout_seconds)
    return sync, captured


@pytest.fixture(autouse=True)
def _restore_httpx_async_client() -> Any:
    """Restore ``httpx.AsyncClient`` after every test to avoid bleed-through."""
    import bug_fab.integrations.slack as slack_module

    original = slack_module.httpx.AsyncClient
    yield
    slack_module.httpx.AsyncClient = original


def _make_report(**overrides: Any) -> dict[str, Any]:
    """Synthetic ``BugReportDetail``-shaped dict used as test input.

    Defaults to a fully populated payload so individual tests can
    override one field at a time without rebuilding the whole shape.
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


def _decode_body(req: httpx.Request) -> dict[str, Any]:
    """Read + JSON-decode a captured request's body."""
    return json.loads(req.content.decode("utf-8"))


def _find_block(blocks: list[dict[str, Any]], block_type: str) -> dict[str, Any]:
    """Return the first block matching ``block_type``; fail loudly if missing."""
    for block in blocks:
        if block.get("type") == block_type:
            return block
    raise AssertionError(f"no {block_type!r} block in {[b.get('type') for b in blocks]}")


def test_default_timeout_is_five_seconds() -> None:
    """Pin the default — anything longer would stretch intake under Slack issues."""
    assert DEFAULT_TIMEOUT_SECONDS == 5.0


def test_send_posts_attachments_payload_to_webhook_url() -> None:
    captured_payload: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured_payload.update(_decode_body(req))
        assert req.headers["content-type"] == "application/json"
        return httpx.Response(200, json={"ok": True})

    sync, captured = _make_sync_with_transport(handler)
    assert _run(sync.send(_make_report())) is True
    assert len(captured) == 1

    assert "attachments" in captured_payload
    attachments = captured_payload["attachments"]
    assert isinstance(attachments, list) and len(attachments) == 1
    att = attachments[0]
    assert att["color"] == SEVERITY_COLORS["critical"]
    assert att["fallback"]  # non-empty fallback for plain-text clients
    assert isinstance(att["blocks"], list)


def test_header_block_has_severity_and_title() -> None:
    captured_payload: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured_payload.update(_decode_body(req))
        return httpx.Response(200, json={"ok": True})

    sync, _ = _make_sync_with_transport(handler)
    _run(sync.send(_make_report(title="Login button does not respond", severity="critical")))

    blocks = captured_payload["attachments"][0]["blocks"]
    header = _find_block(blocks, "header")
    text = header["text"]["text"]
    assert "CRITICAL" in text
    assert "Login button" in text


@pytest.mark.parametrize("severity", ["critical", "high", "medium", "low"])
def test_each_severity_maps_to_documented_color(severity: str) -> None:
    """Critical / high / medium / low all use their pinned color.

    Parametrized rather than looped because ``_make_sync_with_transport``
    monkey-patches ``httpx.AsyncClient`` and chained patches inside a
    single test compound through the inheritance chain — only the first
    iteration's transport actually fires.
    """
    captured: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(_decode_body(req))
        return httpx.Response(200, json={"ok": True})

    sync, _ = _make_sync_with_transport(handler)
    _run(sync.send(_make_report(severity=severity)))
    assert captured[0]["attachments"][0]["color"] == SEVERITY_COLORS[severity]


def test_unknown_severity_falls_back_to_default_color() -> None:
    """A weirdly-named severity must not crash — graceful fallback to gray."""
    captured: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(_decode_body(req))
        return httpx.Response(200, json={"ok": True})

    sync, _ = _make_sync_with_transport(handler)
    assert _run(sync.send(_make_report(severity="catastrophic"))) is True
    assert captured[0]["attachments"][0]["color"] == DEFAULT_COLOR


def test_viewer_base_url_renders_view_link_in_context() -> None:
    captured: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(_decode_body(req))
        return httpx.Response(200, json={"ok": True})

    sync, _ = _make_sync_with_transport(
        handler, viewer_base_url="https://bugs.example.com/admin/bug-reports/"
    )
    _run(sync.send(_make_report(id="bug-042")))

    blocks = captured[0]["attachments"][0]["blocks"]
    context = _find_block(blocks, "context")
    text = context["elements"][0]["text"]
    # Trailing slash on viewer_base_url is stripped by the constructor.
    assert "<https://bugs.example.com/admin/bug-reports/bug-042|View>" in text


def test_viewer_link_omitted_when_no_base_url_configured() -> None:
    captured: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(_decode_body(req))
        return httpx.Response(200, json={"ok": True})

    sync, _ = _make_sync_with_transport(handler)  # default: no viewer_base_url
    _run(sync.send(_make_report()))

    blocks = captured[0]["attachments"][0]["blocks"]
    context = _find_block(blocks, "context")
    text = context["elements"][0]["text"]
    assert "|View>" not in text


def test_github_issue_url_renders_link_in_context() -> None:
    captured: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(_decode_body(req))
        return httpx.Response(200, json={"ok": True})

    sync, _ = _make_sync_with_transport(handler)
    _run(sync.send(_make_report(github_issue_url="https://github.com/org/repo/issues/7")))

    text = _find_block(captured[0]["attachments"][0]["blocks"], "context")["elements"][0]["text"]
    assert "<https://github.com/org/repo/issues/7|GitHub issue>" in text


def test_long_description_truncated_with_ellipsis() -> None:
    captured: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(_decode_body(req))
        return httpx.Response(200, json={"ok": True})

    sync, _ = _make_sync_with_transport(handler)
    long = "x" * (MAX_DESCRIPTION_CHARS + 500)
    _run(sync.send(_make_report(description=long)))

    blocks = captured[0]["attachments"][0]["blocks"]
    # First section block is the description body (the second is the fields block).
    description_section = next(
        b for b in blocks if b.get("type") == "section" and isinstance(b.get("text"), dict)
    )
    text = description_section["text"]["text"]
    assert len(text) <= MAX_DESCRIPTION_CHARS
    assert text.endswith("…")


def test_empty_description_renders_placeholder() -> None:
    captured: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(_decode_body(req))
        return httpx.Response(200, json={"ok": True})

    sync, _ = _make_sync_with_transport(handler)
    _run(sync.send(_make_report(description="")))

    blocks = captured[0]["attachments"][0]["blocks"]
    description_section = next(
        b for b in blocks if b.get("type") == "section" and isinstance(b.get("text"), dict)
    )
    assert "no description" in description_section["text"]["text"]


def test_anonymous_reporter_when_name_missing() -> None:
    captured: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(_decode_body(req))
        return httpx.Response(200, json={"ok": True})

    sync, _ = _make_sync_with_transport(handler)
    _run(sync.send(_make_report(reporter={"name": "", "email": "", "user_id": ""})))

    fields_block = next(
        b
        for b in captured[0]["attachments"][0]["blocks"]
        if b.get("type") == "section" and "fields" in b
    )
    reporter_field = next(f for f in fields_block["fields"] if "Reporter" in f["text"])
    assert "anonymous" in reporter_field["text"]


def test_non_2xx_response_returns_false() -> None:
    """404 (e.g., invalid webhook token) doesn't crash intake."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="invalid_token")

    sync, _ = _make_sync_with_transport(handler)
    assert _run(sync.send(_make_report())) is False


def test_timeout_returns_false() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("slow Slack")

    sync, _ = _make_sync_with_transport(handler)
    assert _run(sync.send(_make_report())) is False


def test_connect_error_returns_false() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    sync, _ = _make_sync_with_transport(handler)
    assert _run(sync.send(_make_report())) is False


def test_build_payload_is_pure_and_idempotent() -> None:
    """No network: callers can inspect the rendered shape directly."""
    sync = SlackSync("https://hooks.slack.com/services/x/y/z")
    report = _make_report()
    a = sync.build_payload(report)
    b = sync.build_payload(report)
    assert a == b
    assert a["attachments"][0]["color"] == SEVERITY_COLORS["critical"]


def test_from_env_disabled_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BUG_FAB_SLACK_ENABLED", raising=False)
    monkeypatch.delenv("BUG_FAB_SLACK_WEBHOOK_URL", raising=False)
    assert SlackSync.from_env() is None


def test_from_env_enabled_without_url_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUG_FAB_SLACK_ENABLED", "true")
    monkeypatch.delenv("BUG_FAB_SLACK_WEBHOOK_URL", raising=False)
    assert SlackSync.from_env() is None


def test_from_env_builds_sync_with_optional_viewer_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BUG_FAB_SLACK_ENABLED", "true")
    monkeypatch.setenv("BUG_FAB_SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/A/B/C")
    monkeypatch.setenv(
        "BUG_FAB_SLACK_VIEWER_BASE_URL", "https://bugs.example.com/admin/bug-reports"
    )
    monkeypatch.setenv("BUG_FAB_SLACK_TIMEOUT_SECONDS", "2.5")

    sync = SlackSync.from_env()
    assert sync is not None
    assert sync.url == "https://hooks.slack.com/services/A/B/C"
    assert sync.viewer_base_url == "https://bugs.example.com/admin/bug-reports"


def test_from_env_invalid_timeout_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo in BUG_FAB_SLACK_TIMEOUT_SECONDS must not break startup."""
    monkeypatch.setenv("BUG_FAB_SLACK_ENABLED", "1")
    monkeypatch.setenv("BUG_FAB_SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/A/B/C")
    monkeypatch.setenv("BUG_FAB_SLACK_TIMEOUT_SECONDS", "not-a-number")
    sync = SlackSync.from_env()
    assert sync is not None  # built despite the typo
