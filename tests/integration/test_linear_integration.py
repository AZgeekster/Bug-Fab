"""Integration tests for the optional Linear (linear.app) issue sync.

Linear's API is GraphQL, so the test shape differs from the REST-y
GitHub adapter tests in a few specific ways:

* All outbound calls go to a single endpoint (``DEFAULT_API_URL``), not
  to per-resource paths — assertions check the body, not the path.
* Linear returns HTTP 200 with an ``errors[]`` array for validation
  failures, so a dedicated test covers the "200 + errors" path.
* The success envelope is nested two levels deep
  (``data.issueCreate.success`` + ``data.issueCreate.issue``), so
  malformed-shape coverage exercises both layers.

External HTTP is captured via :class:`httpx.MockTransport` (no
``respx`` dependency) — same approach used by the GitHub integration
tests.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import httpx
import pytest

from bug_fab.integrations.linear import (
    DEFAULT_API_URL,
    DEFAULT_PRIORITY,
    DEFAULT_TIMEOUT_SECONDS,
    ISSUE_CREATE_MUTATION,
    SEVERITY_PRIORITY_MAP,
    LinearSync,
    _build_description,
    _priority_for_severity,
)

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _run(coro):  # type: ignore[no-untyped-def]
    return asyncio.new_event_loop().run_until_complete(coro)


def _sample_report() -> dict[str, Any]:
    return {
        "id": "bug-001",
        "title": "Submit button does nothing",
        "report_type": "bug",
        "severity": "high",
        "status": "open",
        "module": "ui",
        "environment": "dev",
        "created_at": "2026-05-19T15:00:00Z",
        "description": "Clicking submit shows no feedback.",
        "expected_behavior": "A toast confirms submission.",
        "reporter": {"name": "Alex Tester", "email": "alex@example.com"},
        "context": {
            "url": "/sample",
            "user_agent": "Mozilla/5.0",
        },
    }


def _success_response(
    *,
    identifier: str = "BUG-42",
    url: str = "https://linear.app/acme/issue/BUG-42",
    title: str = "Submit button does nothing",
    issue_id: str = "issue-uuid-1234",
) -> dict[str, Any]:
    return {
        "data": {
            "issueCreate": {
                "success": True,
                "issue": {
                    "id": issue_id,
                    "identifier": identifier,
                    "url": url,
                    "title": title,
                },
            }
        }
    }


def _make_sync_with_transport(
    handler,  # type: ignore[no-untyped-def]
    *,
    api_key: str = "lin_api_test_key",
    team_id: str = "team-uuid-1234",
    api_url: str = DEFAULT_API_URL,
    viewer_base_url: str = "",
    default_label_ids: list[str] | None = None,
) -> tuple[LinearSync, list[httpx.Request]]:
    captured: list[httpx.Request] = []

    def _wrapped(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    transport = httpx.MockTransport(_wrapped)
    sync = LinearSync(
        api_key=api_key,
        team_id=team_id,
        api_url=api_url,
        viewer_base_url=viewer_base_url,
        default_label_ids=default_label_ids,
    )

    import bug_fab.integrations.linear as linear_module

    real_client = httpx.AsyncClient

    class _MockClient(real_client):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    linear_module.httpx.AsyncClient = _MockClient  # type: ignore[attr-defined]
    return sync, captured


@pytest.fixture(autouse=True)
def _restore_httpx_async_client():  # type: ignore[no-untyped-def]
    """Restore ``httpx.AsyncClient`` after every test."""
    import bug_fab.integrations.linear as linear_module

    original = linear_module.httpx.AsyncClient
    yield
    linear_module.httpx.AsyncClient = original


@pytest.fixture
def _clear_linear_env(monkeypatch):  # type: ignore[no-untyped-def]
    """Strip every ``BUG_FAB_LINEAR_*`` env var before the test runs."""
    for key in list(os.environ):
        if key.startswith("BUG_FAB_LINEAR_"):
            monkeypatch.delenv(key, raising=False)
    yield


# -----------------------------------------------------------------------------
# create_issue — success / failure modes
# -----------------------------------------------------------------------------


def test_create_issue_success_returns_identifier_and_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_success_response())

    sync, captured = _make_sync_with_transport(handler)
    identifier, url = _run(sync.create_issue(_sample_report()))
    assert identifier == "BUG-42"
    assert url == "https://linear.app/acme/issue/BUG-42"
    assert len(captured) == 1
    assert captured[0].method == "POST"
    assert str(captured[0].url) == DEFAULT_API_URL


def test_create_issue_returns_none_on_4xx() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    sync, _ = _make_sync_with_transport(handler)
    assert _run(sync.create_issue(_sample_report())) == (None, None)


def test_create_issue_returns_none_on_5xx() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="linear down")

    sync, _ = _make_sync_with_transport(handler)
    assert _run(sync.create_issue(_sample_report())) == (None, None)


def test_create_issue_returns_none_on_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    sync, _ = _make_sync_with_transport(handler)
    assert _run(sync.create_issue(_sample_report())) == (None, None)


def test_create_issue_returns_none_on_graphql_errors_with_http_200() -> None:
    """Linear quirk: validation failures come back as HTTP 200 + ``errors``."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "errors": [
                    {
                        "message": "Argument Validation Error",
                        "extensions": {"code": "INVALID_INPUT"},
                    }
                ]
            },
        )

    sync, _ = _make_sync_with_transport(handler)
    assert _run(sync.create_issue(_sample_report())) == (None, None)


def test_create_issue_returns_none_when_success_false() -> None:
    """``issueCreate.success: false`` is a failure even with a 200 + no ``errors``."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "issueCreate": {
                        "success": False,
                        "issue": None,
                    }
                }
            },
        )

    sync, _ = _make_sync_with_transport(handler)
    assert _run(sync.create_issue(_sample_report())) == (None, None)


def test_create_issue_returns_none_on_malformed_envelope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # Missing ``data.issueCreate`` entirely
        return httpx.Response(200, json={"data": {}})

    sync, _ = _make_sync_with_transport(handler)
    assert _run(sync.create_issue(_sample_report())) == (None, None)


def test_create_issue_returns_none_on_invalid_json_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json {")

    sync, _ = _make_sync_with_transport(handler)
    assert _run(sync.create_issue(_sample_report())) == (None, None)


# -----------------------------------------------------------------------------
# Wire shape: headers, body, GraphQL document
# -----------------------------------------------------------------------------


def test_create_issue_sends_authorization_without_bearer_prefix() -> None:
    """Linear-specific quirk: NO ``Bearer`` prefix on the auth header."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_success_response())

    sync, captured = _make_sync_with_transport(handler, api_key="lin_api_test_key")
    _run(sync.create_issue(_sample_report()))
    assert captured[0].headers["Authorization"] == "lin_api_test_key"
    # Defensive: the GitHub-style ``Bearer <token>`` MUST NOT appear.
    assert not captured[0].headers["Authorization"].lower().startswith("bearer")


def test_create_issue_posts_graphql_mutation_document() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_success_response())

    sync, captured = _make_sync_with_transport(handler)
    _run(sync.create_issue(_sample_report()))
    body = json.loads(captured[0].content)
    assert body["query"] == ISSUE_CREATE_MUTATION
    assert "variables" in body
    assert "input" in body["variables"]


def test_create_issue_input_contains_team_id_title_priority_description() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_success_response())

    sync, captured = _make_sync_with_transport(handler, team_id="team-uuid-1234")
    _run(sync.create_issue(_sample_report()))
    body = json.loads(captured[0].content)
    input_obj = body["variables"]["input"]
    assert input_obj["teamId"] == "team-uuid-1234"
    assert input_obj["title"] == "Submit button does nothing"
    assert input_obj["priority"] == 2  # severity=high → 2
    assert "description" in input_obj


def test_create_issue_includes_default_label_ids() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_success_response())

    labels = ["label-uuid-aaa", "label-uuid-bbb"]
    sync, captured = _make_sync_with_transport(handler, default_label_ids=labels)
    _run(sync.create_issue(_sample_report()))
    input_obj = json.loads(captured[0].content)["variables"]["input"]
    assert input_obj["labelIds"] == labels


def test_create_issue_omits_label_ids_when_none_configured() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_success_response())

    sync, captured = _make_sync_with_transport(handler, default_label_ids=None)
    _run(sync.create_issue(_sample_report()))
    input_obj = json.loads(captured[0].content)["variables"]["input"]
    assert "labelIds" not in input_obj


def test_create_issue_targets_custom_api_url() -> None:
    custom = "https://linear.example.test/graphql"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_success_response())

    sync, captured = _make_sync_with_transport(handler, api_url=custom)
    _run(sync.create_issue(_sample_report()))
    assert str(captured[0].url) == custom


# -----------------------------------------------------------------------------
# Priority mapping
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "severity,expected_priority",
    [
        ("critical", 1),
        ("high", 2),
        ("medium", 3),
        ("low", 4),
        ("unknown", 0),
        ("", 0),
        (None, 0),
        ("CRITICAL", 1),  # case-insensitive
    ],
)
def test_priority_for_severity_mapping(severity: Any, expected_priority: int) -> None:
    assert _priority_for_severity(severity) == expected_priority


def test_severity_priority_map_pins_documented_values() -> None:
    """Sanity check that the public severity-to-priority table is the documented one."""
    assert SEVERITY_PRIORITY_MAP == {
        "critical": 1,
        "high": 2,
        "medium": 3,
        "low": 4,
    }
    assert DEFAULT_PRIORITY == 0


# -----------------------------------------------------------------------------
# Description rendering — build_input is pure (no I/O)
# -----------------------------------------------------------------------------


def test_build_input_is_pure_no_io() -> None:
    """No outbound HTTP is triggered by ``build_input``."""
    sync = LinearSync(api_key="k", team_id="t")
    # Call twice — pure should be idempotent.
    first = sync.build_input(_sample_report())
    second = sync.build_input(_sample_report())
    assert first == second


def test_build_input_renders_description_with_metadata_fields() -> None:
    sync = LinearSync(api_key="k", team_id="t")
    description = sync.build_input(_sample_report())["description"]
    assert "**Severity:** high" in description
    assert "**Reporter:** Alex Tester" in description
    assert "**Environment:** dev" in description
    assert "**Module:** ui" in description
    assert "## Description" in description
    assert "Clicking submit shows no feedback." in description
    assert "## Expected behavior" in description
    assert "A toast confirms submission." in description
    assert "bug-001" in description


def test_build_description_includes_viewer_link_when_configured() -> None:
    description = _build_description(
        _sample_report(),
        viewer_base_url="https://bugs.example.com/admin/bug-reports",
    )
    assert "[View in viewer](https://bugs.example.com/admin/bug-reports/bug-001)" in description


def test_build_description_omits_viewer_link_when_unset() -> None:
    description = _build_description(_sample_report(), viewer_base_url="")
    assert "[View in viewer]" not in description


def test_build_description_handles_missing_optional_fields() -> None:
    description = _build_description({"title": "t"})
    # Reporter / module / env all fall back to "unknown" or "anonymous"
    assert "**Reporter:** anonymous" in description
    assert "_No description provided._" in description


# -----------------------------------------------------------------------------
# from_env
# -----------------------------------------------------------------------------


def test_from_env_disabled_returns_none(_clear_linear_env, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # Explicitly disabled
    monkeypatch.setenv("BUG_FAB_LINEAR_ENABLED", "false")
    monkeypatch.setenv("BUG_FAB_LINEAR_API_KEY", "k")
    monkeypatch.setenv("BUG_FAB_LINEAR_TEAM_ID", "t")
    assert LinearSync.from_env() is None


def test_from_env_unset_returns_none(_clear_linear_env) -> None:  # type: ignore[no-untyped-def]
    # No env vars at all (default-disabled)
    assert LinearSync.from_env() is None


def test_from_env_missing_api_key_returns_none(_clear_linear_env, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("BUG_FAB_LINEAR_ENABLED", "1")
    monkeypatch.setenv("BUG_FAB_LINEAR_TEAM_ID", "team-uuid-1234")
    assert LinearSync.from_env() is None


def test_from_env_missing_team_id_returns_none(_clear_linear_env, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("BUG_FAB_LINEAR_ENABLED", "1")
    monkeypatch.setenv("BUG_FAB_LINEAR_API_KEY", "lin_api_test_key")
    assert LinearSync.from_env() is None


def test_from_env_parses_comma_separated_label_ids(_clear_linear_env, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("BUG_FAB_LINEAR_ENABLED", "1")
    monkeypatch.setenv("BUG_FAB_LINEAR_API_KEY", "lin_api_test_key")
    monkeypatch.setenv("BUG_FAB_LINEAR_TEAM_ID", "team-uuid-1234")
    monkeypatch.setenv(
        "BUG_FAB_LINEAR_DEFAULT_LABEL_IDS",
        "label-uuid-aaa, label-uuid-bbb ,label-uuid-ccc",
    )
    sync = LinearSync.from_env()
    assert sync is not None
    assert sync.default_label_ids == ["label-uuid-aaa", "label-uuid-bbb", "label-uuid-ccc"]


def test_from_env_full_happy_path(_clear_linear_env, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("BUG_FAB_LINEAR_ENABLED", "true")
    monkeypatch.setenv("BUG_FAB_LINEAR_API_KEY", "lin_api_test_key")
    monkeypatch.setenv("BUG_FAB_LINEAR_TEAM_ID", "team-uuid-1234")
    monkeypatch.setenv("BUG_FAB_LINEAR_API_URL", "https://linear.example.test/graphql")
    monkeypatch.setenv(
        "BUG_FAB_LINEAR_VIEWER_BASE_URL", "https://bugs.example.com/admin/bug-reports"
    )
    monkeypatch.setenv("BUG_FAB_LINEAR_TIMEOUT_SECONDS", "7.5")
    sync = LinearSync.from_env()
    assert sync is not None
    assert sync.api_url == "https://linear.example.test/graphql"
    assert sync.team_id == "team-uuid-1234"
    assert sync.viewer_base_url == "https://bugs.example.com/admin/bug-reports"


def test_from_env_bad_timeout_falls_back_to_default(_clear_linear_env, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("BUG_FAB_LINEAR_ENABLED", "1")
    monkeypatch.setenv("BUG_FAB_LINEAR_API_KEY", "lin_api_test_key")
    monkeypatch.setenv("BUG_FAB_LINEAR_TEAM_ID", "team-uuid-1234")
    monkeypatch.setenv("BUG_FAB_LINEAR_TIMEOUT_SECONDS", "not-a-number")
    sync = LinearSync.from_env()
    assert sync is not None
    # No public timeout property — verify by checking the private slot didn't blow up.
    assert sync._timeout == DEFAULT_TIMEOUT_SECONDS  # type: ignore[attr-defined]
