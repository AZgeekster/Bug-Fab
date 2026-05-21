"""Integration tests for the optional Microsoft Teams incoming-webhook adapter.

External HTTP is captured via :class:`httpx.MockTransport` — same
harness shape ``test_slack_integration`` and ``test_webhook_integration``
use. The tests verify that:

* ``TeamsSync.send`` POSTs an Adaptive Card payload (message envelope
  + attachments + AdaptiveCard content) to the configured URL with
  ``Content-Type: application/json``.
* The severity field on the report maps to the Adaptive Card TextBlock
  color vocabulary (``attention | warning | accent | good``); unknown
  severities fall back to ``default`` rather than crashing.
* The viewer-base-url, when set, renders as an ``Action.OpenUrl``
  button on the card. When unset, the ``actions`` array is OMITTED
  entirely — some Teams clients reject an empty list.
* Empty environment / module fields are skipped from the FactSet so
  the card doesn't show ugly blank rows.
* Long descriptions are truncated with an ellipsis.
* Non-2xx responses, transport errors, and timeouts ALL return
  ``False`` rather than raising — preserving the failure-tolerance
  contract that Teams outages must NOT block intake.
* ``TeamsSync.from_env`` reads ``BUG_FAB_TEAMS_*`` env vars and
  returns ``None`` when disabled or unconfigured, so it can be passed
  straight into ``submit.configure(webhook_sync=...)``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from bug_fab.integrations.teams import (
    ADAPTIVE_CARD_VERSION,
    DEFAULT_COLOR,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_DESCRIPTION_CHARS,
    SEVERITY_COLORS,
    TeamsSync,
)


def _run(coro: Any) -> Any:
    """Drive an async coroutine on a fresh event loop, matching webhook tests."""
    return asyncio.new_event_loop().run_until_complete(coro)


def _make_sync_with_transport(
    handler: Any,
    *,
    url: str = "https://outlook.office.com/webhook/00000000-0000-0000-0000-000000000000@tenant/IncomingWebhook/abc/def",
    viewer_base_url: str = "",
    timeout_seconds: float = 5.0,
) -> tuple[TeamsSync, list[httpx.Request]]:
    """Build a TeamsSync whose internal httpx client uses a MockTransport.

    Returns ``(sync, captured_requests)``; the list is appended to in
    order so post-hoc assertions can inspect headers and decoded body.
    """
    captured: list[httpx.Request] = []

    def _wrapped(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    transport = httpx.MockTransport(_wrapped)

    import bug_fab.integrations.teams as teams_module

    real_client = httpx.AsyncClient

    class _MockClient(real_client):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    teams_module.httpx.AsyncClient = _MockClient  # type: ignore[attr-defined]
    sync = TeamsSync(url, viewer_base_url=viewer_base_url, timeout_seconds=timeout_seconds)
    return sync, captured


@pytest.fixture(autouse=True)
def _restore_httpx_async_client() -> Any:
    """Restore ``httpx.AsyncClient`` after every test to avoid bleed-through."""
    import bug_fab.integrations.teams as teams_module

    original = teams_module.httpx.AsyncClient
    yield
    teams_module.httpx.AsyncClient = original


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


def _card_content(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the inner ``AdaptiveCard`` content from a Teams message payload."""
    return payload["attachments"][0]["content"]


def _find_block(body: list[dict[str, Any]], block_type: str) -> dict[str, Any]:
    """Return the first body block matching ``block_type``; fail loudly if missing."""
    for block in body:
        if block.get("type") == block_type:
            return block
    raise AssertionError(f"no {block_type!r} block in {[b.get('type') for b in body]}")


def test_default_timeout_is_five_seconds() -> None:
    """Pin the default — anything longer would stretch intake under Teams issues."""
    assert DEFAULT_TIMEOUT_SECONDS == 5.0


def test_adaptive_card_version_pinned_to_1_4() -> None:
    """1.4 is the broad-support floor; bumping is a deliberate decision."""
    assert ADAPTIVE_CARD_VERSION == "1.4"


def test_send_posts_adaptive_card_payload_to_webhook_url() -> None:
    captured_payload: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured_payload.update(_decode_body(req))
        assert req.headers["content-type"] == "application/json"
        return httpx.Response(200, text="1")

    sync, captured = _make_sync_with_transport(handler)
    assert _run(sync.send(_make_report())) is True
    assert len(captured) == 1

    assert captured_payload["type"] == "message"
    attachments = captured_payload["attachments"]
    assert isinstance(attachments, list) and len(attachments) == 1
    att = attachments[0]
    assert att["contentType"] == "application/vnd.microsoft.card.adaptive"
    card = att["content"]
    assert card["type"] == "AdaptiveCard"
    assert card["version"] == ADAPTIVE_CARD_VERSION
    assert card["$schema"] == "http://adaptivecards.io/schemas/adaptive-card.json"
    assert isinstance(card["body"], list) and card["body"]


def test_title_textblock_has_severity_and_title() -> None:
    captured_payload: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured_payload.update(_decode_body(req))
        return httpx.Response(200, text="1")

    sync, _ = _make_sync_with_transport(handler)
    _run(sync.send(_make_report(title="Login button does not respond", severity="critical")))

    body = _card_content(captured_payload)["body"]
    # The first TextBlock is the bold title with severity prefix.
    title_block = body[0]
    assert title_block["type"] == "TextBlock"
    assert title_block["weight"] == "Bolder"
    assert title_block["size"] == "Large"
    assert "CRITICAL" in title_block["text"]
    assert "Login button" in title_block["text"]


@pytest.mark.parametrize(
    "severity,expected_color",
    [
        ("critical", "attention"),
        ("high", "warning"),
        ("medium", "accent"),
        ("low", "good"),
    ],
)
def test_each_severity_maps_to_documented_adaptive_card_color(
    severity: str, expected_color: str
) -> None:
    """Each documented severity uses its pinned Adaptive Card color token.

    Parametrized rather than looped because ``_make_sync_with_transport``
    monkey-patches ``httpx.AsyncClient`` and chained patches inside a
    single test compound through the inheritance chain — only the first
    iteration's transport actually fires.
    """
    captured: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(_decode_body(req))
        return httpx.Response(200, text="1")

    sync, _ = _make_sync_with_transport(handler)
    _run(sync.send(_make_report(severity=severity)))
    body = _card_content(captured[0])["body"]
    assert body[0]["color"] == expected_color
    assert SEVERITY_COLORS[severity] == expected_color


def test_unknown_severity_falls_back_to_default_color() -> None:
    """A weirdly-named severity must not crash — graceful fallback to default."""
    captured: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(_decode_body(req))
        return httpx.Response(200, text="1")

    sync, _ = _make_sync_with_transport(handler)
    assert _run(sync.send(_make_report(severity="catastrophic"))) is True
    body = _card_content(captured[0])["body"]
    assert body[0]["color"] == DEFAULT_COLOR


def test_viewer_base_url_renders_openurl_action() -> None:
    captured: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(_decode_body(req))
        return httpx.Response(200, text="1")

    sync, _ = _make_sync_with_transport(
        handler, viewer_base_url="https://bugs.example.com/admin/bug-reports/"
    )
    _run(sync.send(_make_report(id="bug-042")))

    card = _card_content(captured[0])
    actions = card.get("actions")
    assert isinstance(actions, list) and len(actions) == 1
    action = actions[0]
    assert action["type"] == "Action.OpenUrl"
    assert action["title"] == "View report"
    # Trailing slash on viewer_base_url is stripped by the constructor.
    assert action["url"] == "https://bugs.example.com/admin/bug-reports/bug-042"


def test_no_viewer_url_omits_actions_array_entirely() -> None:
    """Some Teams clients reject ``actions: []`` — omit the key when empty."""
    captured: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(_decode_body(req))
        return httpx.Response(200, text="1")

    sync, _ = _make_sync_with_transport(handler)  # default: no viewer_base_url
    _run(sync.send(_make_report()))

    card = _card_content(captured[0])
    assert "actions" not in card


def test_factset_includes_reporter_and_status_always() -> None:
    captured: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(_decode_body(req))
        return httpx.Response(200, text="1")

    sync, _ = _make_sync_with_transport(handler)
    _run(sync.send(_make_report(environment="", module="")))

    facts = _find_block(_card_content(captured[0])["body"], "FactSet")["facts"]
    titles = [f["title"] for f in facts]
    assert "Reporter" in titles
    assert "Status" in titles


def test_factset_skips_empty_environment_and_module() -> None:
    captured: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(_decode_body(req))
        return httpx.Response(200, text="1")

    sync, _ = _make_sync_with_transport(handler)
    _run(sync.send(_make_report(environment="", module="")))

    facts = _find_block(_card_content(captured[0])["body"], "FactSet")["facts"]
    titles = [f["title"] for f in facts]
    assert "Environment" not in titles
    assert "Module" not in titles


def test_factset_includes_environment_and_module_when_set() -> None:
    captured: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(_decode_body(req))
        return httpx.Response(200, text="1")

    sync, _ = _make_sync_with_transport(handler)
    _run(sync.send(_make_report(environment="production", module="auth")))

    facts = _find_block(_card_content(captured[0])["body"], "FactSet")["facts"]
    by_title = {f["title"]: f["value"] for f in facts}
    assert by_title["Environment"] == "production"
    assert by_title["Module"] == "auth"


def test_long_description_truncated_with_ellipsis() -> None:
    captured: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(_decode_body(req))
        return httpx.Response(200, text="1")

    sync, _ = _make_sync_with_transport(handler)
    long = "x" * (MAX_DESCRIPTION_CHARS + 500)
    _run(sync.send(_make_report(description=long)))

    body = _card_content(captured[0])["body"]
    # Second TextBlock is the description body (first is the title).
    description_block = body[1]
    assert description_block["type"] == "TextBlock"
    text = description_block["text"]
    assert len(text) <= MAX_DESCRIPTION_CHARS
    assert text.endswith("…")


def test_empty_description_renders_placeholder() -> None:
    captured: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(_decode_body(req))
        return httpx.Response(200, text="1")

    sync, _ = _make_sync_with_transport(handler)
    _run(sync.send(_make_report(description="")))

    body = _card_content(captured[0])["body"]
    description_block = body[1]
    assert "no description" in description_block["text"]


def test_anonymous_reporter_when_name_missing() -> None:
    captured: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(_decode_body(req))
        return httpx.Response(200, text="1")

    sync, _ = _make_sync_with_transport(handler)
    _run(sync.send(_make_report(reporter={"name": "", "email": "", "user_id": ""})))

    facts = _find_block(_card_content(captured[0])["body"], "FactSet")["facts"]
    reporter = next(f for f in facts if f["title"] == "Reporter")
    assert reporter["value"] == "anonymous"


def test_context_textblock_has_id_and_timestamp() -> None:
    captured: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(_decode_body(req))
        return httpx.Response(200, text="1")

    sync, _ = _make_sync_with_transport(handler)
    _run(sync.send(_make_report(id="bug-123", created_at="2026-05-20T12:00:00+00:00")))

    body = _card_content(captured[0])["body"]
    # Last body block is the subtle context line.
    context = body[-1]
    assert context["type"] == "TextBlock"
    assert context.get("isSubtle") is True
    assert context.get("size") == "Small"
    assert "bug-123" in context["text"]
    assert "2026-05-20T12:00:00+00:00" in context["text"]


def test_non_2xx_response_returns_false() -> None:
    """404 (e.g., invalid webhook token) doesn't crash intake."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="invalid_token")

    sync, _ = _make_sync_with_transport(handler)
    assert _run(sync.send(_make_report())) is False


def test_timeout_returns_false() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("slow Teams")

    sync, _ = _make_sync_with_transport(handler)
    assert _run(sync.send(_make_report())) is False


def test_connect_error_returns_false() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    sync, _ = _make_sync_with_transport(handler)
    assert _run(sync.send(_make_report())) is False


def test_build_payload_is_pure_and_idempotent() -> None:
    """No network: callers can inspect the rendered shape directly."""
    sync = TeamsSync("https://outlook.office.com/webhook/x")
    report = _make_report()
    a = sync.build_payload(report)
    b = sync.build_payload(report)
    assert a == b
    body = a["attachments"][0]["content"]["body"]
    assert body[0]["color"] == SEVERITY_COLORS["critical"]


def test_from_env_disabled_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BUG_FAB_TEAMS_ENABLED", raising=False)
    monkeypatch.delenv("BUG_FAB_TEAMS_WEBHOOK_URL", raising=False)
    assert TeamsSync.from_env() is None


def test_from_env_enabled_without_url_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUG_FAB_TEAMS_ENABLED", "true")
    monkeypatch.delenv("BUG_FAB_TEAMS_WEBHOOK_URL", raising=False)
    assert TeamsSync.from_env() is None


def test_from_env_builds_sync_with_optional_viewer_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BUG_FAB_TEAMS_ENABLED", "true")
    monkeypatch.setenv("BUG_FAB_TEAMS_WEBHOOK_URL", "https://outlook.office.com/webhook/abc")
    monkeypatch.setenv(
        "BUG_FAB_TEAMS_VIEWER_BASE_URL", "https://bugs.example.com/admin/bug-reports"
    )
    monkeypatch.setenv("BUG_FAB_TEAMS_TIMEOUT_SECONDS", "2.5")

    sync = TeamsSync.from_env()
    assert sync is not None
    assert sync.url == "https://outlook.office.com/webhook/abc"
    assert sync.viewer_base_url == "https://bugs.example.com/admin/bug-reports"


def test_from_env_invalid_timeout_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo in BUG_FAB_TEAMS_TIMEOUT_SECONDS must not break startup."""
    monkeypatch.setenv("BUG_FAB_TEAMS_ENABLED", "1")
    monkeypatch.setenv("BUG_FAB_TEAMS_WEBHOOK_URL", "https://outlook.office.com/webhook/abc")
    monkeypatch.setenv("BUG_FAB_TEAMS_TIMEOUT_SECONDS", "not-a-number")
    sync = TeamsSync.from_env()
    assert sync is not None  # built despite the typo
