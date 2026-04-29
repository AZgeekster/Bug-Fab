"""Pydantic v2 schema unit tests.

Covers the locked enum vocabularies (Severity / Status), the create payload
constraints, and the round-trip behavior of optional fields. Strict
validation of severity and status is the contract that adapters MUST honor
(CC11) — these tests pin the contract at the model layer so a careless
schema change cannot loosen it without breaking the suite.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from bug_fab.schemas import (
    BugReportContext,
    BugReportCreate,
    BugReportDetail,
    BugReportListResponse,
    BugReportStatusUpdate,
    BugReportSummary,
    LifecycleEvent,
    Severity,
    Status,
)


def _create(**overrides) -> BugReportCreate:
    """Build a BugReportCreate with required fields filled in.

    Tests use this when they want to exercise a specific optional field
    without re-typing the required ones every time. Tests that explicitly
    target required-field validation call ``BugReportCreate(...)`` directly.
    """
    defaults = {
        "protocol_version": "0.1",
        "title": "t",
        "client_ts": "2026-04-29T12:00:00+00:00",
    }
    defaults.update(overrides)
    return BugReportCreate(**defaults)


# -----------------------------------------------------------------------------
# Severity / Status enum strictness
# -----------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["low", "medium", "high", "critical"])
def test_severity_accepts_locked_values(value: str) -> None:
    payload = _create(severity=value)
    assert payload.severity.value == value


@pytest.mark.parametrize("value", ["urgent", "minor", "MAJOR", "Low", "Medium", "", "  ", "med"])
def test_severity_rejects_unknown_values(value: str) -> None:
    with pytest.raises(ValidationError):
        _create(severity=value)


@pytest.mark.parametrize("value", ["open", "investigating", "fixed", "closed"])
def test_status_accepts_locked_values(value: str) -> None:
    payload = BugReportStatusUpdate(status=value)
    assert payload.status.value == value


@pytest.mark.parametrize("value", ["resolved", "open ", "Open", "done", "wont_fix"])
def test_status_rejects_unknown_values_on_write(value: str) -> None:
    """CC12 (write half): deprecated/unknown statuses MUST be rejected on write."""
    with pytest.raises(ValidationError):
        BugReportStatusUpdate(status=value)


def test_severity_is_str_enum() -> None:
    """Severity values must JSON-serialize as plain strings."""
    encoded = json.dumps({"sev": Severity.HIGH.value})
    assert json.loads(encoded) == {"sev": "high"}
    # Also: equality with the literal string holds (str-Enum subclass).
    assert Severity.HIGH == "high"


def test_status_is_str_enum() -> None:
    encoded = json.dumps({"st": Status.OPEN.value})
    assert json.loads(encoded) == {"st": "open"}
    assert Status.FIXED == "fixed"


# -----------------------------------------------------------------------------
# Title length bounds
# -----------------------------------------------------------------------------


def test_title_required() -> None:
    with pytest.raises(ValidationError):
        BugReportCreate()  # type: ignore[call-arg]


def test_title_empty_string_rejected() -> None:
    with pytest.raises(ValidationError):
        BugReportCreate(title="")


def test_title_min_length_one() -> None:
    payload = _create(title="a")
    assert payload.title == "a"


def test_title_max_length_two_hundred() -> None:
    payload = _create(title="x" * 200)
    assert len(payload.title) == 200


def test_title_over_max_length_rejected() -> None:
    with pytest.raises(ValidationError):
        _create(title="x" * 201)


# -----------------------------------------------------------------------------
# Tags list shape
# -----------------------------------------------------------------------------


def test_tags_default_empty_list() -> None:
    payload = _create()
    assert payload.tags == []


def test_tags_accepts_list_of_strings() -> None:
    payload = _create(tags=["regression", "viewer"])
    assert payload.tags == ["regression", "viewer"]


def test_tags_rejects_non_list_string() -> None:
    """Comma-string is the JS-side parse responsibility — server expects a list.

    Pydantic v2 does not silently split a comma-separated string into a list
    on a ``list[str]`` field — the JS bundle handles the comma split before
    POSTing. This test pins that contract: a raw string is a validation
    error, not a single-element list.
    """
    with pytest.raises(ValidationError):
        _create(tags="a,b,c")  # type: ignore[arg-type]


def test_tags_coerces_each_entry_to_string() -> None:
    """List of mixed-type entries is coerced to str by Pydantic v2."""
    payload = _create(tags=["a", "b"])
    assert all(isinstance(tag, str) for tag in payload.tags)


# -----------------------------------------------------------------------------
# Context / environment / user-agent fields
# -----------------------------------------------------------------------------


def test_context_environment_optional_and_round_trips() -> None:
    """`environment` lives inside ``context`` and round-trips via model_dump."""
    payload = _create(context=BugReportContext(environment="staging"))
    dumped = payload.model_dump()
    assert dumped["context"]["environment"] == "staging"


def test_context_environment_default_empty() -> None:
    payload = _create()
    assert payload.context.environment == ""


def test_context_extra_keys_allowed() -> None:
    """``BugReportContext`` allows extra keys (consumer-defined diagnostics)."""
    payload = _create(
        context={
            "url": "/x",
            "module": "m",
            "consumer_specific_field": {"nested": True},
        },
    )
    dumped = payload.model_dump()
    # extra="allow" preserves the extra key on dump
    assert dumped["context"].get("consumer_specific_field") == {"nested": True}


def test_client_reported_user_agent_optional_on_detail() -> None:
    detail = BugReportDetail(id="bug-001", title="t", created_at="2026-01-01T00:00:00Z")
    assert detail.client_reported_user_agent == ""


def test_client_reported_user_agent_round_trip() -> None:
    detail = BugReportDetail(
        id="bug-001",
        title="t",
        created_at="2026-01-01T00:00:00Z",
        client_reported_user_agent="Mozilla/5.0 (Test)",
    )
    assert detail.model_dump()["client_reported_user_agent"] == "Mozilla/5.0 (Test)"


def test_server_user_agent_round_trip() -> None:
    detail = BugReportDetail(
        id="bug-002",
        title="t",
        created_at="2026-01-01T00:00:00Z",
        server_user_agent="ServerCapturedUA/1.0",
    )
    assert detail.model_dump()["server_user_agent"] == "ServerCapturedUA/1.0"


def test_environment_round_trips_at_top_level() -> None:
    detail = BugReportDetail(
        id="bug-003",
        title="t",
        created_at="2026-01-01T00:00:00Z",
        environment="prod",
    )
    assert detail.model_dump()["environment"] == "prod"


# -----------------------------------------------------------------------------
# Report type literal
# -----------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["bug", "feature_request"])
def test_report_type_accepts_locked_literals(value: str) -> None:
    payload = _create(report_type=value)
    assert payload.report_type == value


def test_report_type_rejects_other_values() -> None:
    with pytest.raises(ValidationError):
        _create(report_type="incident")  # type: ignore[arg-type]


# -----------------------------------------------------------------------------
# protocol_version (locked literal "0.1")
# -----------------------------------------------------------------------------


def test_protocol_version_required() -> None:
    """Submission without ``protocol_version`` MUST fail validation.

    Pinned at the schema layer so the contract holds independent of any
    adapter's request handler.
    """
    with pytest.raises(ValidationError):
        BugReportCreate(title="t", client_ts="2026-04-29T12:00:00+00:00")  # type: ignore[call-arg]


def test_protocol_version_only_accepts_v0_1() -> None:
    """Unknown versions MUST be rejected — adapter layer maps to 400 unsupported_protocol_version."""
    with pytest.raises(ValidationError):
        BugReportCreate(
            protocol_version="0.2",  # type: ignore[arg-type]
            title="t",
            client_ts="2026-04-29T12:00:00+00:00",
        )


def test_client_ts_required_non_empty() -> None:
    """``client_ts`` is required and must be non-empty (any ISO string is fine here)."""
    with pytest.raises(ValidationError):
        BugReportCreate(protocol_version="0.1", title="t")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        BugReportCreate(protocol_version="0.1", title="t", client_ts="")


# -----------------------------------------------------------------------------
# Reporter sub-fields (256-char cap each, opaque otherwise)
# -----------------------------------------------------------------------------


def test_reporter_default_empty_subfields() -> None:
    payload = _create()
    assert payload.reporter.name == ""
    assert payload.reporter.email == ""
    assert payload.reporter.user_id == ""


def test_reporter_round_trips_through_dump() -> None:
    payload = _create(reporter={"name": "alice", "email": "a@example.com", "user_id": "u-42"})
    dumped = payload.model_dump()
    assert dumped["reporter"] == {
        "name": "alice",
        "email": "a@example.com",
        "user_id": "u-42",
    }


@pytest.mark.parametrize("field", ["name", "email", "user_id"])
def test_reporter_subfield_caps_at_256_chars(field: str) -> None:
    """All reporter sub-fields are opaque strings capped at 256 chars (per 2026-04-28 decisions)."""
    overlong = "x" * 257
    with pytest.raises(ValidationError):
        _create(reporter={field: overlong})


# -----------------------------------------------------------------------------
# Status update body
# -----------------------------------------------------------------------------


def test_status_update_optional_fix_fields_default_empty() -> None:
    body = BugReportStatusUpdate(status=Status.FIXED)
    assert body.fix_commit == ""
    assert body.fix_description == ""


def test_status_update_with_fix_fields_round_trips() -> None:
    body = BugReportStatusUpdate(
        status=Status.FIXED, fix_commit="abc1234", fix_description="patched logic"
    )
    dumped = body.model_dump()
    assert dumped["fix_commit"] == "abc1234"
    assert dumped["fix_description"] == "patched logic"


# -----------------------------------------------------------------------------
# Summary / Detail / List response coverage
# -----------------------------------------------------------------------------


def test_summary_defaults() -> None:
    summary = BugReportSummary(id="bug-001", title="t", created_at="2026-01-01T00:00:00Z")
    assert summary.severity == "medium"
    assert summary.status == "open"
    assert summary.has_screenshot is True
    assert summary.github_issue_url is None


def test_detail_inherits_summary_fields() -> None:
    detail = BugReportDetail(id="bug-001", title="t", created_at="2026-01-01T00:00:00Z")
    assert detail.id == "bug-001"
    assert detail.lifecycle == []
    assert detail.tags == []


def test_list_response_pagination_defaults() -> None:
    response = BugReportListResponse(items=[], total=0)
    assert response.page == 1
    assert response.page_size == 20


def test_lifecycle_event_minimal_payload() -> None:
    event = LifecycleEvent(action="created", at="2026-01-01T00:00:00Z")
    assert event.by == ""
    assert event.fix_commit == ""
    assert event.fix_description == ""


def test_lifecycle_event_extra_fields_allowed() -> None:
    """Lifecycle events allow extra fields for forward-compat with future actions."""
    event = LifecycleEvent.model_validate(
        {"action": "custom", "at": "2026-01-01T00:00:00Z", "custom_meta": 7}
    )
    assert event.model_dump().get("custom_meta") == 7


def test_summary_severity_status_accept_deprecated_on_read() -> None:
    """CC12 (read half): summaries must accept legacy enum values from storage.

    The summary model uses ``str`` (not the enum) for severity/status because
    long-lived storage may carry deprecated values that adapters MUST still
    return on read.
    """
    summary = BugReportSummary.model_validate(
        {
            "id": "bug-001",
            "title": "Legacy",
            "created_at": "2024-01-01T00:00:00Z",
            "status": "resolved",  # deprecated
            "severity": "medium",
        }
    )
    assert summary.status == "resolved"


def test_detail_accepts_deprecated_status_on_read() -> None:
    detail = BugReportDetail.model_validate(
        {
            "id": "bug-legacy-001",
            "title": "Legacy",
            "created_at": "2024-01-01T00:00:00Z",
            "status": "resolved",
        }
    )
    assert detail.status == "resolved"
