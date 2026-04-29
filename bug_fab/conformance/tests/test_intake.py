"""Conformance tests for the `POST /bug-reports` intake endpoint.

Each test maps directly to a clause in `docs/PROTOCOL.md`. Test names start
with the protocol behavior they assert, not the HTTP shape, so failure
output reads as a spec-violation report rather than a generic HTTP error
(`test_invalid_severity_returns_422` not `test_post_returns_422`).

WHY assert via `pytest.fail` with a quoted expectation: pytest's default
assertion-introspection reads well for simple `==` checks but loses the
"this is the protocol clause that failed" framing for status-code asserts.
The `pytest.fail` calls below give the adapter author a one-liner that says
exactly which clause is broken.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from bug_fab.conformance.fixtures import (
    make_invalid_severity_metadata,
    make_test_metadata,
    make_test_png,
)

if TYPE_CHECKING:
    import httpx


INTAKE_PATH = "/bug-reports"
ALLOWED_BAD_REQUEST_CODES = {400, 422}
ALLOWED_OVERSIZE_CODES = {400, 413}


def _post_multipart(
    client: httpx.Client,
    metadata: str,
    screenshot: bytes | None,
    *,
    screenshot_filename: str = "screenshot.png",
    screenshot_content_type: str = "image/png",
) -> httpx.Response:
    """Issue a multipart POST with optional screenshot omission.

    Centralised so every test path uses the same multipart shape; differences
    between tests live only in the metadata/screenshot they pass in.
    """
    files: dict[str, tuple[str, bytes, str]] = {}
    if screenshot is not None:
        files["screenshot"] = (screenshot_filename, screenshot, screenshot_content_type)
    data = {"metadata": metadata}
    return client.post(INTAKE_PATH, data=data, files=files or None)


def test_minimal_valid_submission_returns_201(conformance_client: httpx.Client) -> None:
    """Minimal-but-complete metadata + a valid PNG MUST return 201 Created."""
    response = _post_multipart(
        conformance_client,
        metadata=make_test_metadata(),
        screenshot=make_test_png(),
    )
    if response.status_code != 201:
        pytest.fail(
            f"PROTOCOL.md requires 201 Created on minimal valid submission; "
            f"got {response.status_code}. Body: {response.text[:500]}"
        )


def test_full_submission_with_all_optional_fields_returns_201(
    conformance_client: httpx.Client,
) -> None:
    """All optional metadata fields populated MUST still produce 201."""
    metadata = make_test_metadata(
        description="Detailed reproduction steps and expected vs actual outcome.",
        expected_behavior="Submit button should clear the form on success.",
        severity="high",
        tags=["regression", "viewer", "conformance"],
        context={
            "url": "http://localhost/sample/page?id=123",
            "module": "sample",
            "user_agent": "Mozilla/5.0 (conformance-suite)",
            "viewport_width": 1920,
            "viewport_height": 1080,
            "console_errors": [
                {
                    "level": "error",
                    "message": "TypeError: cannot read property of undefined",
                    "stack": "at handler (file.js:42:7)",
                    "ts": "2026-04-27T15:00:00Z",
                },
            ],
            "network_log": [
                {
                    "method": "GET",
                    "url": "/api/widgets",
                    "status": 500,
                    "duration_ms": 412,
                    "ts": "2026-04-27T14:59:58Z",
                },
            ],
            "source_mapping": {"route": "routes/widgets.py"},
            "environment": "dev",
        },
    )
    response = _post_multipart(
        conformance_client,
        metadata=metadata,
        screenshot=make_test_png(width=20, height=20),
    )
    if response.status_code != 201:
        pytest.fail(
            f"Full submission with all optional fields MUST return 201; "
            f"got {response.status_code}. Body: {response.text[:500]}"
        )


def test_missing_screenshot_is_rejected(conformance_client: httpx.Client) -> None:
    """Submitting without the `screenshot` form field MUST be rejected."""
    response = _post_multipart(
        conformance_client,
        metadata=make_test_metadata(),
        screenshot=None,
    )
    if response.status_code not in ALLOWED_BAD_REQUEST_CODES:
        pytest.fail(
            f"Missing screenshot MUST return 400 or 422 per PROTOCOL.md; "
            f"got {response.status_code}. Body: {response.text[:500]}"
        )


def test_missing_metadata_is_rejected(conformance_client: httpx.Client) -> None:
    """Submitting without the `metadata` form field MUST be rejected."""
    files = {"screenshot": ("screenshot.png", make_test_png(), "image/png")}
    response = conformance_client.post(INTAKE_PATH, files=files)
    if response.status_code not in ALLOWED_BAD_REQUEST_CODES:
        pytest.fail(
            f"Missing metadata MUST return 400 or 422; "
            f"got {response.status_code}. Body: {response.text[:500]}"
        )


def test_malformed_metadata_json_is_rejected(conformance_client: httpx.Client) -> None:
    """`metadata` form field that is not valid JSON MUST be rejected."""
    response = _post_multipart(
        conformance_client,
        metadata="{not valid json,,,",
        screenshot=make_test_png(),
    )
    if response.status_code not in ALLOWED_BAD_REQUEST_CODES:
        pytest.fail(
            f"Malformed metadata JSON MUST return 400 or 422; "
            f"got {response.status_code}. Body: {response.text[:500]}"
        )


def test_invalid_severity_returns_422(conformance_client: httpx.Client) -> None:
    """CC11: invalid `severity` MUST return 422 (no silent coercion).

    Per decisions.md § Decisions Locked 2026-04-27 (post-audit): adapters
    that silently coerce `"urgent"` to `"medium"` (one prior implementation's
    behavior) FAIL conformance. The protocol-as-contract design demands
    strict validation.
    """
    response = _post_multipart(
        conformance_client,
        metadata=make_invalid_severity_metadata(),
        screenshot=make_test_png(),
    )
    if response.status_code != 422:
        pytest.fail(
            f"CC11: invalid severity 'urgent' MUST return 422 (no silent "
            f"coercion to a default value); got {response.status_code}. "
            f"Body: {response.text[:500]}"
        )


def test_oversize_screenshot_is_rejected(conformance_client: httpx.Client) -> None:
    """A screenshot exceeding the documented 10 MiB cap MUST be rejected.

    Sized at 11 MiB to exceed the 10 MiB default — adapter authors who
    configured a higher cap should bump this test's ceiling in their own
    overlay (out of scope for v0.1 conformance).
    """
    oversized = b"\x89PNG\r\n\x1a\n" + (b"\x00" * (11 * 1024 * 1024))
    response = _post_multipart(
        conformance_client,
        metadata=make_test_metadata(),
        screenshot=oversized,
    )
    if response.status_code not in ALLOWED_OVERSIZE_CODES:
        pytest.fail(
            f"Oversize screenshot (11 MiB > 10 MiB cap) MUST return 413 or 400; "
            f"got {response.status_code}. Body: {response.text[:500]}"
        )


def test_wrong_content_type_is_rejected(conformance_client: httpx.Client) -> None:
    """A JSON-only POST (no multipart wrapper) MUST be rejected."""
    response = conformance_client.post(
        INTAKE_PATH,
        content=json.dumps({"title": "wrong content-type"}),
        headers={"Content-Type": "application/json"},
    )
    # 415 is the "right" answer; 400/422 also acceptable since some frameworks
    # treat the missing multipart parts as a validation error before reaching
    # content-type negotiation.
    if response.status_code not in {400, 415, 422}:
        pytest.fail(
            f"JSON content-type on intake (expected multipart) MUST return "
            f"415, 400, or 422; got {response.status_code}. "
            f"Body: {response.text[:500]}"
        )


def test_response_body_includes_documented_fields(
    conformance_client: httpx.Client,
) -> None:
    """The 201 response MUST include `id`, `received_at`, `stored_at`, `github_issue_url`."""
    response = _post_multipart(
        conformance_client,
        metadata=make_test_metadata(title="Response-shape conformance check"),
        screenshot=make_test_png(),
    )
    if response.status_code != 201:
        pytest.fail(
            f"Setup failed — needed 201 to inspect response shape, "
            f"got {response.status_code}. Body: {response.text[:500]}"
        )
    try:
        body = response.json()
    except ValueError:
        pytest.fail(f"201 response body MUST be JSON; got: {response.text[:500]}")
        return  # appeases the type-checker; pytest.fail raises

    required_fields = {"id", "received_at", "stored_at", "github_issue_url"}
    missing = required_fields - set(body.keys())
    if missing:
        pytest.fail(
            f"PROTOCOL.md requires response fields {sorted(required_fields)}; "
            f"missing from this response: {sorted(missing)}. Got: {body!r}"
        )
    if not isinstance(body["id"], str) or not body["id"]:
        pytest.fail(f"Response `id` MUST be a non-empty string; got {body['id']!r}")
    if not isinstance(body["received_at"], str):
        pytest.fail(
            f"Response `received_at` MUST be an ISO-8601 string; got {body['received_at']!r}"
        )


@pytest.mark.parametrize(
    "field_name",
    ["title", "description", "severity"],
)
def test_response_does_not_echo_metadata_at_top_level(
    conformance_client: httpx.Client, field_name: str
) -> None:
    """The intake response MUST NOT echo unrelated metadata fields at the top level.

    Strictly speaking the protocol enumerates `id`/`received_at`/`stored_at`/
    `github_issue_url` and is silent on extras. This test catches adapters
    that accidentally serialize the entire stored report into the 201 body
    (which leaks server-injected fields adapter authors may not intend to
    expose).
    """
    response = _post_multipart(
        conformance_client,
        metadata=make_test_metadata(title="Echo-leak conformance check"),
        screenshot=make_test_png(),
    )
    if response.status_code != 201:
        pytest.skip(
            f"Setup failed — needed 201, got {response.status_code}. Body: {response.text[:500]}"
        )
    body = response.json()
    if field_name in body:
        pytest.fail(
            f"Intake 201 response leaks `{field_name}` from the request payload "
            f"at the top level. PROTOCOL.md only documents id/received_at/"
            f"stored_at/github_issue_url; consider returning the full report "
            f"under a `report:` key instead."
        )
