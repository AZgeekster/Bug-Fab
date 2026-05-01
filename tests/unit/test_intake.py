"""Unit tests for :mod:`bug_fab.intake`.

Covers each branch of the validation pipeline so non-FastAPI adapters can
trust the helper before swapping their hand-rolled validation for it.
The order of test cases mirrors the order of validation steps in
:func:`bug_fab.intake.validate_payload` (size → content-type → magic
bytes → metadata → user-agent).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from bug_fab.intake import (
    IntakeError,
    PayloadTooLarge,
    UnsupportedMediaType,
    ValidatedPayload,
    ValidationError,
    validate_payload,
)
from bug_fab.schemas import BugReportCreate


def _valid_metadata_json() -> str:
    """Build a canonical valid metadata JSON string for the happy path."""
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
        "context": {
            "url": "http://localhost/sample",
            "module": "sample",
            "user_agent": "client-reported-ua/1.0",
            "viewport_width": 1280,
            "viewport_height": 720,
            "console_errors": [],
            "network_log": [],
            "source_mapping": {},
            "app_version": "0.1.0",
            "environment": "dev",
        },
    }
    return json.dumps(payload)


def test_happy_path(tiny_png: bytes) -> None:
    """Valid PNG + valid metadata returns a ValidatedPayload with all fields."""
    metadata_json = _valid_metadata_json()
    result = validate_payload(
        metadata_json=metadata_json,
        screenshot_bytes=tiny_png,
        screenshot_content_type="image/png",
        request_user_agent="bug-fab-tests/0.1",
    )
    assert isinstance(result, ValidatedPayload)
    assert isinstance(result.metadata, BugReportCreate)
    assert result.metadata.title == "Submit form does not clear after success"
    assert result.metadata.severity.value == "medium"
    assert result.screenshot_bytes == tiny_png
    # The server-captured UA is preserved verbatim from the header arg, NOT
    # cross-contaminated by the client-reported context.user_agent.
    assert result.user_agent == "bug-fab-tests/0.1"
    assert result.metadata.context.user_agent == "client-reported-ua/1.0"


def test_oversize_raises_payload_too_large(tiny_png: bytes) -> None:
    """Bytes longer than max_screenshot_bytes raises 413."""
    oversized = tiny_png + b"\x00" * 1024
    with pytest.raises(PayloadTooLarge) as exc_info:
        validate_payload(
            metadata_json=_valid_metadata_json(),
            screenshot_bytes=oversized,
            screenshot_content_type="image/png",
            request_user_agent="bug-fab-tests/0.1",
            max_screenshot_bytes=len(tiny_png),
        )
    err = exc_info.value
    assert err.status_code == 413
    assert err.code == "payload_too_large"
    assert "exceeds" in err.message.lower() or "exceeds" in str(err).lower()
    assert isinstance(err, IntakeError)


def test_wrong_content_type_raises_415(tiny_png: bytes) -> None:
    """Content-type 'image/jpeg' raises UnsupportedMediaType (415)."""
    with pytest.raises(UnsupportedMediaType) as exc_info:
        validate_payload(
            metadata_json=_valid_metadata_json(),
            screenshot_bytes=tiny_png,
            screenshot_content_type="image/jpeg",
            request_user_agent="bug-fab-tests/0.1",
        )
    err = exc_info.value
    assert err.status_code == 415
    assert err.code == "unsupported_media_type"
    assert isinstance(err, IntakeError)


def test_bad_magic_raises_415() -> None:
    """Content-type image/png but bytes that don't start with PNG magic raises 415."""
    not_a_png = b"this is plain text masquerading as a png"
    with pytest.raises(UnsupportedMediaType) as exc_info:
        validate_payload(
            metadata_json=_valid_metadata_json(),
            screenshot_bytes=not_a_png,
            screenshot_content_type="image/png",
            request_user_agent="bug-fab-tests/0.1",
        )
    err = exc_info.value
    assert err.status_code == 415
    assert err.code == "unsupported_media_type"
    assert "magic" in err.message.lower() or "png" in err.message.lower()


def test_invalid_metadata_raises_422(tiny_png: bytes) -> None:
    """Metadata JSON missing required fields raises ValidationError with detail list."""
    bad_metadata = json.dumps({"title": "no protocol_version, no client_ts"})
    with pytest.raises(ValidationError) as exc_info:
        validate_payload(
            metadata_json=bad_metadata,
            screenshot_bytes=tiny_png,
            screenshot_content_type="image/png",
            request_user_agent="bug-fab-tests/0.1",
        )
    err = exc_info.value
    assert err.status_code == 422
    assert err.code == "schema_error"
    assert isinstance(err.detail, list)
    assert len(err.detail) > 0
    for entry in err.detail:
        assert isinstance(entry, dict)


def test_unparseable_json_raises_422(tiny_png: bytes) -> None:
    """Metadata that is not valid JSON also raises ValidationError (422)."""
    with pytest.raises(ValidationError) as exc_info:
        validate_payload(
            metadata_json="this is not json {",
            screenshot_bytes=tiny_png,
            screenshot_content_type="image/png",
            request_user_agent="bug-fab-tests/0.1",
        )
    err = exc_info.value
    assert err.status_code == 422
    assert err.code == "schema_error"
    assert "json" in err.message.lower()


def test_user_agent_default_is_empty(tiny_png: bytes) -> None:
    """When request_user_agent is None the returned UA is the empty string, never None."""
    result = validate_payload(
        metadata_json=_valid_metadata_json(),
        screenshot_bytes=tiny_png,
        screenshot_content_type="image/png",
        request_user_agent=None,
    )
    assert result.user_agent == ""
    assert isinstance(result.user_agent, str)


def test_validation_order_size_before_content_type(tiny_png: bytes) -> None:
    """Size check runs before content-type so an oversized JPEG raises 413, not 415."""
    oversized = tiny_png + b"\x00" * 2048
    with pytest.raises(PayloadTooLarge):
        validate_payload(
            metadata_json=_valid_metadata_json(),
            screenshot_bytes=oversized,
            screenshot_content_type="image/jpeg",
            request_user_agent=None,
            max_screenshot_bytes=len(tiny_png),
        )


def test_missing_content_type_raises_415(tiny_png: bytes) -> None:
    """A None content-type is rejected as unsupported, not silently accepted."""
    with pytest.raises(UnsupportedMediaType) as exc_info:
        validate_payload(
            metadata_json=_valid_metadata_json(),
            screenshot_bytes=tiny_png,
            screenshot_content_type=None,
            request_user_agent=None,
        )
    assert exc_info.value.status_code == 415
