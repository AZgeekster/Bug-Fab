"""Integration tests for the optional GitHub Issues sync.

External HTTP is captured via ``httpx.MockTransport`` (no ``respx``
dependency required). The tests verify that:

* ``create_issue`` POSTs to the right URL with the documented body shape.
* ``sync_issue_state`` PATCHes the right URL and body.
* ``ensure_labels`` runs once per instance and tolerates the GitHub
  "label already exists" 422.
* GitHub failures never block local persistence — the submit flow still
  returns 201 even when the GitHub API errors out.
* When the integration is disabled (no PAT / repo), no outbound calls are
  made at all.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from bug_fab.integrations.github import (
    DEFAULT_LABEL_COLORS,
    DEFAULT_STATE_MAP,
    GITHUB_API_VERSION,
    GitHubSync,
    _build_issue_body,
    _build_issue_labels,
    _build_issue_title,
)


def _run(coro):  # type: ignore[no-untyped-def]
    return asyncio.new_event_loop().run_until_complete(coro)


def _sample_report() -> dict[str, Any]:
    return {
        "id": "bug-001",
        "title": "Submit form does not clear",
        "report_type": "bug",
        "severity": "high",
        "status": "open",
        "module": "ui",
        "environment": "dev",
        "created_at": "2026-04-27T15:00:00Z",
        "description": "Steps to reproduce: ...",
        "expected_behavior": "Form clears.",
        "context": {
            "url": "/sample",
            "user_agent": "Mozilla/5.0",
            "viewport_width": 1920,
            "viewport_height": 1080,
            "console_errors": [{"level": "error", "message": "boom"}],
            "network_log": [{"method": "GET", "url": "/api", "status": 500}],
            "source_mapping": {"route": "routes/sample.py"},
        },
    }


def _make_sync_with_transport(
    handler,  # type: ignore[no-untyped-def]
    *,
    pat: str = "ghp_test",
    repo: str = "owner/repo",
    api_base: str = "https://api.github.com",
) -> tuple[GitHubSync, list[httpx.Request]]:
    """Build a GitHubSync whose internal AsyncClient uses a MockTransport.

    Returns (sync, captured_requests). The handler's per-call return value
    decides the response shape; the captured list is appended to in-order
    for post-hoc assertions.
    """
    captured: list[httpx.Request] = []

    def _wrapped(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    transport = httpx.MockTransport(_wrapped)
    sync = GitHubSync(pat=pat, repo=repo, api_base=api_base)

    # Monkey-patch ``httpx.AsyncClient`` *inside* the github module so the
    # synchronously-constructed clients pick up the mock transport.
    import bug_fab.integrations.github as github_module

    real_client = httpx.AsyncClient

    class _MockClient(real_client):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    github_module.httpx.AsyncClient = _MockClient  # type: ignore[attr-defined]
    return sync, captured


@pytest.fixture(autouse=True)
def _restore_httpx_async_client():  # type: ignore[no-untyped-def]
    """Restore ``httpx.AsyncClient`` after every test (defensive)."""
    import bug_fab.integrations.github as github_module

    original = github_module.httpx.AsyncClient
    yield
    github_module.httpx.AsyncClient = original


# -----------------------------------------------------------------------------
# create_issue
# -----------------------------------------------------------------------------


def test_create_issue_posts_to_correct_url_and_returns_number_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/labels"):
            return httpx.Response(201, json={"name": "ok"})
        if request.url.path.endswith("/issues"):
            return httpx.Response(
                201,
                json={"number": 42, "html_url": "https://github.com/owner/repo/issues/42"},
            )
        return httpx.Response(404)

    sync, captured = _make_sync_with_transport(handler)
    number, url = _run(sync.create_issue(_sample_report()))
    assert number == 42
    assert url == "https://github.com/owner/repo/issues/42"

    # Find the issue-creation POST among the captured calls
    issue_posts = [r for r in captured if r.method == "POST" and r.url.path.endswith("/issues")]
    assert len(issue_posts) == 1
    body = json.loads(issue_posts[0].content)
    assert body["title"].startswith("[Bug]")
    assert "ui" in body["body"] or "Submit form does not clear" in body["body"]
    assert "bug" in body["labels"]
    assert "severity:high" in body["labels"]


def test_create_issue_uses_pinned_api_version_header() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/issues"):
            return httpx.Response(201, json={"number": 1, "html_url": "u"})
        return httpx.Response(201, json={})

    sync, captured = _make_sync_with_transport(handler)
    _run(sync.create_issue(_sample_report()))
    issue_posts = [r for r in captured if r.url.path.endswith("/issues")]
    assert issue_posts[0].headers["X-GitHub-Api-Version"] == GITHUB_API_VERSION
    assert issue_posts[0].headers["Authorization"] == "Bearer ghp_test"


def test_create_issue_returns_none_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/issues"):
            return httpx.Response(500, text="server error")
        return httpx.Response(201, json={})

    sync, _ = _make_sync_with_transport(handler)
    number, url = _run(sync.create_issue(_sample_report()))
    assert (number, url) == (None, None)


def test_create_issue_returns_none_on_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    sync, _ = _make_sync_with_transport(handler)
    number, url = _run(sync.create_issue(_sample_report()))
    assert (number, url) == (None, None)


def test_create_issue_returns_none_on_malformed_response_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/issues"):
            # Missing ``number`` and ``html_url`` keys
            return httpx.Response(201, json={"id": "wrong-shape"})
        return httpx.Response(201, json={})

    sync, _ = _make_sync_with_transport(handler)
    assert _run(sync.create_issue(_sample_report())) == (None, None)


# -----------------------------------------------------------------------------
# sync_issue_state
# -----------------------------------------------------------------------------


def test_sync_issue_state_patches_correct_url_and_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"state": "closed"})

    sync, captured = _make_sync_with_transport(handler)
    ok = _run(sync.sync_issue_state(42, "fixed"))
    assert ok is True

    patches = [r for r in captured if r.method == "PATCH"]
    assert len(patches) == 1
    assert patches[0].url.path.endswith("/issues/42")
    assert json.loads(patches[0].content) == {"state": "closed"}


@pytest.mark.parametrize(
    "status,expected_state",
    [
        ("open", "open"),
        ("investigating", "open"),
        ("fixed", "closed"),
        ("closed", "closed"),
    ],
)
def test_sync_issue_state_status_to_state_mapping(status: str, expected_state: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"state": expected_state})

    sync, captured = _make_sync_with_transport(handler)
    _run(sync.sync_issue_state(7, status))
    body = json.loads([r for r in captured if r.method == "PATCH"][0].content)
    assert body == {"state": expected_state}


def test_sync_issue_state_unknown_status_falls_back_to_open() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    sync, captured = _make_sync_with_transport(handler)
    _run(sync.sync_issue_state(9, "wat"))
    body = json.loads([r for r in captured if r.method == "PATCH"][0].content)
    assert body == {"state": "open"}


def test_sync_issue_state_returns_false_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    sync, _ = _make_sync_with_transport(handler)
    assert _run(sync.sync_issue_state(1, "fixed")) is False


def test_sync_issue_state_returns_false_on_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    sync, _ = _make_sync_with_transport(handler)
    assert _run(sync.sync_issue_state(1, "fixed")) is False


# -----------------------------------------------------------------------------
# ensure_labels
# -----------------------------------------------------------------------------


def test_ensure_labels_treats_422_as_already_exists() -> None:
    """422 from /labels means "already exists" — the flow continues silently."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/labels"):
            return httpx.Response(422, json={"message": "already_exists"})
        return httpx.Response(201, json={})

    sync, captured = _make_sync_with_transport(handler)
    _run(sync.ensure_labels())
    # Every default label triggered one POST
    label_posts = [r for r in captured if r.url.path.endswith("/labels")]
    assert len(label_posts) == len(DEFAULT_LABEL_COLORS)


def test_ensure_labels_runs_once_per_instance() -> None:
    """Second call is a no-op (the internal flag short-circuits it)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={})

    sync, captured = _make_sync_with_transport(handler)
    _run(sync.ensure_labels())
    first_count = len(captured)
    _run(sync.ensure_labels())
    second_count = len(captured)
    assert second_count == first_count


def test_ensure_labels_tolerates_transport_error_per_label() -> None:
    """Per-label HTTPError is logged but does not raise."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    sync, _ = _make_sync_with_transport(handler)
    # Should not raise; control returns
    _run(sync.ensure_labels())


# -----------------------------------------------------------------------------
# Integration with submit flow: GitHub disabled means no outbound calls
# -----------------------------------------------------------------------------


def test_submit_does_not_call_github_when_disabled(app_factory, tiny_png: bytes) -> None:
    """When ``github_sync`` is None, the submit router never touches httpx."""
    captured_calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_calls.append(request)
        return httpx.Response(200, json={})

    # Even if we monkey-patched the transport, the submit router should never
    # construct a client because github_sync=None.
    import bug_fab.integrations.github as github_module

    original = github_module.httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    class _MockClient(original):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    github_module.httpx.AsyncClient = _MockClient  # type: ignore[attr-defined]
    try:
        client = app_factory(github_sync=None)
        response = client.post(
            "/bug-reports",
            data={
                "metadata": json.dumps(
                    {
                        "protocol_version": "0.1",
                        "title": "no-github",
                        "client_ts": "2026-04-29T12:00:00+00:00",
                        "context": {},
                    }
                )
            },
            files={"screenshot": ("shot.png", tiny_png, "image/png")},
        )
        assert response.status_code == 201
        assert captured_calls == []
    finally:
        github_module.httpx.AsyncClient = original


def test_submit_succeeds_even_when_github_create_issue_errors(app_factory, tiny_png: bytes) -> None:
    """A 500 from GitHub during submit MUST NOT block local persistence."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="github down")

    sync, _ = _make_sync_with_transport(handler)
    client = app_factory(github_sync=sync)
    response = client.post(
        "/bug-reports",
        data={
            "metadata": json.dumps(
                {
                    "protocol_version": "0.1",
                    "title": "github fail",
                    "client_ts": "2026-04-29T12:00:00+00:00",
                    "context": {},
                }
            )
        },
        files={"screenshot": ("shot.png", tiny_png, "image/png")},
    )
    assert response.status_code == 201
    body = response.json()
    # Minimal envelope: id present, github_issue_url null (sync failed but
    # local persistence succeeded — best-effort guarantee).
    assert body["id"].startswith("bug-")
    assert body["github_issue_url"] is None
    # No issue linkage on the report
    assert body.get("github_issue_number") in (None, 0)


# -----------------------------------------------------------------------------
# Body / labels / title rendering helpers
# -----------------------------------------------------------------------------


def test_build_issue_title_bug_prefix() -> None:
    title = _build_issue_title({"title": "Submit fails", "report_type": "bug"})
    assert title == "[Bug] Submit fails"


def test_build_issue_title_feature_request_prefix() -> None:
    title = _build_issue_title({"title": "Add darkmode", "report_type": "feature_request"})
    assert title == "[Feature Request] Add darkmode"


def test_build_issue_labels_includes_severity_and_env() -> None:
    labels = _build_issue_labels(
        {
            "report_type": "bug",
            "severity": "critical",
            "environment": "production",
        }
    )
    assert "bug" in labels
    assert "severity:critical" in labels
    assert "env:production" in labels


def test_build_issue_labels_omits_environment_when_empty() -> None:
    labels = _build_issue_labels({"severity": "low", "environment": ""})
    assert all(not label.startswith("env:") for label in labels)


def test_build_issue_body_contains_description_and_metadata() -> None:
    body = _build_issue_body(_sample_report())
    assert "Submit form does not clear" not in body  # title NOT echoed in body
    assert "## Description" in body
    assert "Steps to reproduce" in body
    assert "## Expected Behavior" in body
    assert "Form clears." in body
    assert "## Metadata" in body
    assert "bug-001" in body
    assert "<details>" in body
    assert "Auto-captured context" in body


def test_build_issue_body_handles_missing_optional_fields() -> None:
    body = _build_issue_body({"title": "t"})
    assert "## Description" in body
    assert "_No description provided._" in body


def test_default_state_map_pins_documented_mapping() -> None:
    """Sanity check that the default state map matches the docstring."""
    assert DEFAULT_STATE_MAP == {
        "open": "open",
        "investigating": "open",
        "fixed": "closed",
        "closed": "closed",
    }
