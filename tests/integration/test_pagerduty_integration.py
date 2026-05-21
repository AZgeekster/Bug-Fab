"""Integration tests for the optional PagerDuty Events API v2 adapter.

External HTTP is captured via :class:`httpx.MockTransport` — same
harness shape ``test_slack_integration`` uses. The tests verify that:

* ``PagerDutySync.send`` POSTs an Events API v2 trigger payload to the
  configured endpoint (``/v2/enqueue``) with ``Content-Type:
  application/json`` for severities in ``escalate_severities``, and
  returns ``False`` *without* making a network call for severities
  outside it (the documented suppression behavior — not a failure).
* Each Bug-Fab severity maps to the right PagerDuty Events API v2
  severity vocabulary (``critical/error/warning/info``).
* ``dedup_key`` is built from ``<prefix>-<report_id>`` so retried
  webhook deliveries collapse into a single incident, and the prefix
  can be overridden via the constructor.
* When a viewer base URL is configured, the rendered body carries the
  deep link in BOTH ``payload.custom_details.viewer_url`` AND the
  top-level ``links[]`` array.
* Non-2xx responses, transport errors, and timeouts ALL return
  ``False`` rather than raising — preserving the failure-tolerance
  contract that PagerDuty outages must NOT block intake.
* PagerDuty's typical success status (``202 Accepted``) is treated as
  success, not just a vanilla ``200``.
* ``PagerDutySync.from_env`` reads ``BUG_FAB_PAGERDUTY_*`` env vars
  and returns ``None`` when disabled or unconfigured, so it can be
  passed straight into ``submit.configure(webhook_sync=...)``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from bug_fab.integrations.pagerduty import (
    DEFAULT_API_URL,
    DEFAULT_DEDUP_PREFIX,
    DEFAULT_ESCALATE_SEVERITIES,
    DEFAULT_PAGERDUTY_SEVERITY,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_DESCRIPTION_CHARS,
    SEVERITY_MAP,
    PagerDutySync,
)


def _run(coro: Any) -> Any:
    """Drive an async coroutine on a fresh event loop, matching slack tests."""
    return asyncio.new_event_loop().run_until_complete(coro)


def _make_sync_with_transport(
    handler: Any,
    *,
    integration_key: str = "test-integration-key",
    escalate_severities: tuple[str, ...] = DEFAULT_ESCALATE_SEVERITIES,
    api_url: str = DEFAULT_API_URL,
    viewer_base_url: str = "",
    timeout_seconds: float = 10.0,
    dedup_prefix: str = DEFAULT_DEDUP_PREFIX,
) -> tuple[PagerDutySync, list[httpx.Request]]:
    """Build a PagerDutySync whose internal httpx client uses a MockTransport.

    Returns ``(sync, captured_requests)``; the list is appended to in
    order so post-hoc assertions can inspect headers and decoded body.
    """
    captured: list[httpx.Request] = []

    def _wrapped(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    transport = httpx.MockTransport(_wrapped)

    import bug_fab.integrations.pagerduty as pd_module

    real_client = httpx.AsyncClient

    class _MockClient(real_client):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    pd_module.httpx.AsyncClient = _MockClient  # type: ignore[attr-defined]
    sync = PagerDutySync(
        integration_key=integration_key,
        escalate_severities=escalate_severities,
        api_url=api_url,
        viewer_base_url=viewer_base_url,
        timeout_seconds=timeout_seconds,
        dedup_prefix=dedup_prefix,
    )
    return sync, captured


@pytest.fixture(autouse=True)
def _restore_httpx_async_client() -> Any:
    """Restore ``httpx.AsyncClient`` after every test to avoid bleed-through."""
    import bug_fab.integrations.pagerduty as pd_module

    original = pd_module.httpx.AsyncClient
    yield
    pd_module.httpx.AsyncClient = original


def _make_report(**overrides: Any) -> dict[str, Any]:
    """Synthetic ``BugReportDetail``-shaped dict used as test input."""
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


def test_default_timeout_is_ten_seconds() -> None:
    """Pin the default — PagerDuty enqueue is fast but global blips happen."""
    assert DEFAULT_TIMEOUT_SECONDS == 10.0


def test_default_endpoint_is_events_v2() -> None:
    """The adapter targets Events API v2 exclusively (v1 is deprecated)."""
    assert DEFAULT_API_URL == "https://events.pagerduty.com/v2/enqueue"


def test_default_escalate_severities_is_critical_only() -> None:
    """Critical-only is the documented opinionated default."""
    assert DEFAULT_ESCALATE_SEVERITIES == ("critical",)


def test_critical_report_triggers_post_with_full_payload() -> None:
    captured_body: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured_body.update(_decode_body(req))
        assert req.headers["content-type"] == "application/json"
        # PagerDuty replies 202 Accepted on success, not 200.
        return httpx.Response(202, json={"status": "success", "dedup_key": "bug-fab-bug-001"})

    sync, captured = _make_sync_with_transport(handler)
    assert _run(sync.send(_make_report())) is True
    assert len(captured) == 1

    # Top-level shape per Events API v2 contract.
    assert captured_body["routing_key"] == "test-integration-key"
    assert captured_body["event_action"] == "trigger"
    assert captured_body["dedup_key"] == "bug-fab-bug-001"

    payload = captured_body["payload"]
    assert payload["summary"] == "CRITICAL: Login button does not respond"
    assert payload["source"] == "bug-fab"
    assert payload["severity"] == "critical"
    assert payload["component"] == "auth"
    assert payload["group"] == "production"

    details = payload["custom_details"]
    assert details["report_id"] == "bug-001"
    assert details["reporter"] == "Alice"
    assert details["created_at"] == "2026-05-20T12:00:00+00:00"
    assert "login button" in details["description"].lower()


@pytest.mark.parametrize("severity", ["low", "medium", "high"])
def test_non_critical_severities_suppressed_by_default(severity: str) -> None:
    """Low / medium / high MUST NOT page when the default config is in force."""
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(202, json={"status": "success"})

    sync, captured = _make_sync_with_transport(handler)
    assert _run(sync.send(_make_report(severity=severity))) is False
    assert captured == []  # ZERO outbound calls — pure suppression
    assert calls == []


def test_critical_and_high_escalation_includes_high() -> None:
    """Override via ``escalate_severities`` opens the gate for additional levels."""
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(202, json={"status": "success"})

    sync, captured = _make_sync_with_transport(handler, escalate_severities=("critical", "high"))
    assert _run(sync.send(_make_report(severity="high"))) is True
    assert _run(sync.send(_make_report(severity="medium"))) is False  # still suppressed
    assert len(captured) == 1  # only the "high" call landed


@pytest.mark.parametrize("severity", ["critical", "high", "medium", "low"])
def test_escalate_all_severities_lets_everything_through(severity: str) -> None:
    """``escalate_severities=("low","medium","high","critical")`` = page on everything."""
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(202, json={"status": "success"})

    sync, _ = _make_sync_with_transport(
        handler, escalate_severities=("low", "medium", "high", "critical")
    )
    assert _run(sync.send(_make_report(severity=severity))) is True
    assert len(calls) == 1


@pytest.mark.parametrize(
    "bug_fab_severity,pagerduty_severity",
    [
        ("critical", "critical"),
        ("high", "error"),
        ("medium", "warning"),
        ("low", "info"),
    ],
)
def test_severity_maps_to_pagerduty_vocabulary(
    bug_fab_severity: str, pagerduty_severity: str
) -> None:
    """Bug-Fab severity → Events API v2 severity vocab mapping is pinned.

    Parametrized rather than looped because the transport monkey-patch
    is per-test and chained patches inside a single test compound
    through the inheritance chain — only the first iteration would
    actually fire.
    """
    captured_body: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured_body.update(_decode_body(req))
        return httpx.Response(202, json={"status": "success"})

    sync, _ = _make_sync_with_transport(
        handler, escalate_severities=("low", "medium", "high", "critical")
    )
    _run(sync.send(_make_report(severity=bug_fab_severity)))
    assert captured_body["payload"]["severity"] == pagerduty_severity
    # Sanity check the module constant agrees with the test expectation
    assert SEVERITY_MAP[bug_fab_severity] == pagerduty_severity


def test_unknown_severity_maps_to_info_default() -> None:
    """A weirdly-named severity must not 400 PagerDuty — fall back to ``info``."""
    captured_body: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured_body.update(_decode_body(req))
        return httpx.Response(202, json={"status": "success"})

    sync, _ = _make_sync_with_transport(
        handler,
        escalate_severities=("critical", "catastrophic"),  # let it through to check mapping
    )
    _run(sync.send(_make_report(severity="catastrophic")))
    assert captured_body["payload"]["severity"] == DEFAULT_PAGERDUTY_SEVERITY


def test_dedup_key_uses_report_id_with_default_prefix() -> None:
    captured_body: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured_body.update(_decode_body(req))
        return httpx.Response(202, json={"status": "success"})

    sync, _ = _make_sync_with_transport(handler)
    _run(sync.send(_make_report(id="bug-042")))
    assert captured_body["dedup_key"] == "bug-fab-bug-042"


def test_custom_dedup_prefix_is_honored() -> None:
    captured_body: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured_body.update(_decode_body(req))
        return httpx.Response(202, json={"status": "success"})

    sync, _ = _make_sync_with_transport(handler, dedup_prefix="acme-prod")
    _run(sync.send(_make_report(id="bug-042")))
    assert captured_body["dedup_key"] == "acme-prod-bug-042"


def test_viewer_base_url_renders_in_custom_details_and_links() -> None:
    captured_body: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured_body.update(_decode_body(req))
        return httpx.Response(202, json={"status": "success"})

    sync, _ = _make_sync_with_transport(
        handler, viewer_base_url="https://bugs.example.com/admin/bug-reports/"
    )
    _run(sync.send(_make_report(id="bug-042")))

    expected = "https://bugs.example.com/admin/bug-reports/bug-042"
    # Trailing slash on viewer_base_url is stripped by the constructor.
    assert captured_body["payload"]["custom_details"]["viewer_url"] == expected
    links = captured_body["links"]
    assert isinstance(links, list) and len(links) == 1
    assert links[0]["href"] == expected
    assert links[0]["text"] == "View in Bug-Fab viewer"


def test_no_viewer_base_url_omits_link_and_viewer_url() -> None:
    captured_body: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured_body.update(_decode_body(req))
        return httpx.Response(202, json={"status": "success"})

    sync, _ = _make_sync_with_transport(handler)  # default: no viewer
    _run(sync.send(_make_report()))

    assert "viewer_url" not in captured_body["payload"]["custom_details"]
    assert "links" not in captured_body


def test_long_description_truncated_with_ellipsis() -> None:
    captured_body: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured_body.update(_decode_body(req))
        return httpx.Response(202, json={"status": "success"})

    sync, _ = _make_sync_with_transport(handler)
    long = "x" * (MAX_DESCRIPTION_CHARS + 500)
    _run(sync.send(_make_report(description=long)))

    description = captured_body["payload"]["custom_details"]["description"]
    assert len(description) <= MAX_DESCRIPTION_CHARS
    assert description.endswith("…")


def test_anonymous_reporter_when_name_missing() -> None:
    captured_body: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured_body.update(_decode_body(req))
        return httpx.Response(202, json={"status": "success"})

    sync, _ = _make_sync_with_transport(handler)
    _run(sync.send(_make_report(reporter={"name": "", "email": "", "user_id": ""})))
    assert captured_body["payload"]["custom_details"]["reporter"] == "anonymous"


def test_two_hundred_two_accepted_is_success() -> None:
    """PagerDuty's typical success code is 202, not 200 — must be treated as ok."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(202, json={"status": "success", "dedup_key": "x"})

    sync, _ = _make_sync_with_transport(handler)
    assert _run(sync.send(_make_report())) is True


def test_four_hundred_returns_false() -> None:
    """400 from Events API (e.g., bad routing key) doesn't crash intake."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"status": "invalid event", "errors": ["bad key"]})

    sync, _ = _make_sync_with_transport(handler)
    assert _run(sync.send(_make_report())) is False


def test_five_hundred_returns_false() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream busy")

    sync, _ = _make_sync_with_transport(handler)
    assert _run(sync.send(_make_report())) is False


def test_timeout_returns_false() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("slow PagerDuty")

    sync, _ = _make_sync_with_transport(handler)
    assert _run(sync.send(_make_report())) is False


def test_connect_error_returns_false() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    sync, _ = _make_sync_with_transport(handler)
    assert _run(sync.send(_make_report())) is False


def test_build_payload_is_pure_and_idempotent() -> None:
    """No network: callers can inspect the rendered shape directly."""
    sync = PagerDutySync(integration_key="key-abc")
    report = _make_report()
    a = sync.build_payload(report)
    b = sync.build_payload(report)
    assert a == b
    assert a["routing_key"] == "key-abc"
    assert a["event_action"] == "trigger"
    assert a["payload"]["severity"] == "critical"


def test_from_env_disabled_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BUG_FAB_PAGERDUTY_ENABLED", raising=False)
    monkeypatch.delenv("BUG_FAB_PAGERDUTY_INTEGRATION_KEY", raising=False)
    assert PagerDutySync.from_env() is None


def test_from_env_enabled_without_integration_key_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BUG_FAB_PAGERDUTY_ENABLED", "true")
    monkeypatch.delenv("BUG_FAB_PAGERDUTY_INTEGRATION_KEY", raising=False)
    assert PagerDutySync.from_env() is None


def test_from_env_builds_sync_with_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUG_FAB_PAGERDUTY_ENABLED", "true")
    monkeypatch.setenv("BUG_FAB_PAGERDUTY_INTEGRATION_KEY", "key-from-env")
    monkeypatch.delenv("BUG_FAB_PAGERDUTY_ESCALATE_SEVERITIES", raising=False)
    monkeypatch.delenv("BUG_FAB_PAGERDUTY_API_URL", raising=False)
    monkeypatch.delenv("BUG_FAB_PAGERDUTY_VIEWER_BASE_URL", raising=False)
    monkeypatch.delenv("BUG_FAB_PAGERDUTY_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("BUG_FAB_PAGERDUTY_DEDUP_PREFIX", raising=False)

    sync = PagerDutySync.from_env()
    assert sync is not None
    assert sync.integration_key == "key-from-env"
    assert sync.escalate_severities == DEFAULT_ESCALATE_SEVERITIES
    assert sync.api_url == DEFAULT_API_URL
    assert sync.dedup_prefix == DEFAULT_DEDUP_PREFIX


def test_from_env_parses_comma_separated_severities(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUG_FAB_PAGERDUTY_ENABLED", "1")
    monkeypatch.setenv("BUG_FAB_PAGERDUTY_INTEGRATION_KEY", "k")
    monkeypatch.setenv("BUG_FAB_PAGERDUTY_ESCALATE_SEVERITIES", "critical, high ,medium")
    sync = PagerDutySync.from_env()
    assert sync is not None
    # Whitespace trimmed, case normalized, empty tokens dropped.
    assert sync.escalate_severities == ("critical", "high", "medium")


def test_from_env_full_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUG_FAB_PAGERDUTY_ENABLED", "true")
    monkeypatch.setenv("BUG_FAB_PAGERDUTY_INTEGRATION_KEY", "k")
    monkeypatch.setenv("BUG_FAB_PAGERDUTY_ESCALATE_SEVERITIES", "critical")
    monkeypatch.setenv("BUG_FAB_PAGERDUTY_API_URL", "https://events.eu.pagerduty.com/v2/enqueue")
    monkeypatch.setenv(
        "BUG_FAB_PAGERDUTY_VIEWER_BASE_URL", "https://bugs.example.com/admin/bug-reports"
    )
    monkeypatch.setenv("BUG_FAB_PAGERDUTY_TIMEOUT_SECONDS", "3.5")
    monkeypatch.setenv("BUG_FAB_PAGERDUTY_DEDUP_PREFIX", "acme-prod")
    sync = PagerDutySync.from_env()
    assert sync is not None
    assert sync.api_url == "https://events.eu.pagerduty.com/v2/enqueue"
    assert sync.viewer_base_url == "https://bugs.example.com/admin/bug-reports"
    assert sync.dedup_prefix == "acme-prod"


def test_from_env_invalid_timeout_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo in BUG_FAB_PAGERDUTY_TIMEOUT_SECONDS must not break startup."""
    monkeypatch.setenv("BUG_FAB_PAGERDUTY_ENABLED", "1")
    monkeypatch.setenv("BUG_FAB_PAGERDUTY_INTEGRATION_KEY", "k")
    monkeypatch.setenv("BUG_FAB_PAGERDUTY_TIMEOUT_SECONDS", "not-a-number")
    sync = PagerDutySync.from_env()
    assert sync is not None  # built despite the typo


def test_suppression_logged_at_debug(caplog: pytest.LogCaptureFixture) -> None:
    """Suppressed reports must surface at DEBUG for audit, not be silent."""
    import logging

    sync = PagerDutySync(integration_key="k")  # default escalate = critical only
    caplog.set_level(logging.DEBUG, logger="bug_fab.integrations.pagerduty")
    result = _run(sync.send(_make_report(severity="low", id="bug-suppressed")))
    assert result is False
    assert any("suppressed" in rec.message.lower() for rec in caplog.records)
