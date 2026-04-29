"""Conformance tests for the viewer JSON read endpoints.

Covers `GET /reports`, `GET /reports/{id}`, and `GET /reports/{id}/screenshot`.
Each test seeds its own report via the intake endpoint to keep the suite
self-contained — adapters do not need to pre-populate fixtures.

WHY seed-then-read instead of mocking storage: the conformance plugin
exists to validate the wire protocol end-to-end. Mocking storage would
defeat the purpose; we instead trust the adapter's own intake to be the
arrange step (and `test_intake.py` proves intake conforms separately).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from bug_fab.conformance.fixtures import make_test_metadata, make_test_png

if TYPE_CHECKING:
    import httpx


INTAKE_PATH = "/bug-reports"
LIST_PATH = "/reports"


def _seed_report(client: httpx.Client, **metadata_overrides: object) -> str:
    """Submit a report and return its id, skipping if the intake leg fails.

    Used as the arrange step in every viewer test that needs a known-id to
    fetch. Skipping (not failing) keeps the failure attribution clean — a
    broken intake should fail in `test_intake.py`, not poison every viewer
    test downstream.
    """
    response = client.post(
        INTAKE_PATH,
        data={"metadata": make_test_metadata(**metadata_overrides)},
        files={"screenshot": ("screenshot.png", make_test_png(), "image/png")},
    )
    if response.status_code != 201:
        pytest.skip(
            f"Could not seed report — intake returned {response.status_code}. "
            f"Fix `test_intake.py` failures first."
        )
    body = response.json()
    return body["id"]


def test_list_returns_pagination_envelope(conformance_client: httpx.Client) -> None:
    """`GET /reports` MUST return `{items, total, page, page_size}`."""
    _seed_report(conformance_client, title="List pagination envelope check")
    response = conformance_client.get(LIST_PATH)
    if response.status_code != 200:
        pytest.fail(
            f"GET /reports MUST return 200; got {response.status_code}. Body: {response.text[:500]}"
        )
    body = response.json()
    required_keys = {"items", "total", "page", "page_size"}
    missing = required_keys - set(body.keys())
    if missing:
        pytest.fail(
            f"GET /reports MUST return pagination envelope with keys "
            f"{sorted(required_keys)}; missing: {sorted(missing)}. "
            f"Got: {sorted(body.keys())}"
        )
    if not isinstance(body["items"], list):
        pytest.fail(f"`items` MUST be a list; got {type(body['items']).__name__}")
    if not isinstance(body["total"], int):
        pytest.fail(f"`total` MUST be an int; got {type(body['total']).__name__}")


def test_list_filter_by_status_open(conformance_client: httpx.Client) -> None:
    """`GET /reports?status=open` MUST return only open reports."""
    _seed_report(conformance_client, title="Filter-by-status seed (open)")
    response = conformance_client.get(LIST_PATH, params={"status": "open"})
    if response.status_code != 200:
        pytest.fail(
            f"Status-filtered list MUST return 200; got {response.status_code}. "
            f"Body: {response.text[:500]}"
        )
    body = response.json()
    bad = [item for item in body["items"] if item.get("status") != "open"]
    if bad:
        pytest.fail(
            f"GET /reports?status=open returned {len(bad)} non-open report(s). "
            f"First offending item: {bad[0]!r}"
        )


def test_list_filter_by_severity_critical(conformance_client: httpx.Client) -> None:
    """`GET /reports?severity=critical` MUST return only critical reports."""
    _seed_report(
        conformance_client, title="Filter-by-severity seed (critical)", severity="critical"
    )
    response = conformance_client.get(LIST_PATH, params={"severity": "critical"})
    if response.status_code != 200:
        pytest.fail(
            f"Severity-filtered list MUST return 200; got {response.status_code}. "
            f"Body: {response.text[:500]}"
        )
    body = response.json()
    bad = [item for item in body["items"] if item.get("severity") != "critical"]
    if bad:
        pytest.fail(
            f"GET /reports?severity=critical returned {len(bad)} non-critical report(s). "
            f"First offending item: {bad[0]!r}"
        )


def test_detail_returns_documented_fields(conformance_client: httpx.Client) -> None:
    """`GET /reports/{id}` MUST return the `BugReportDetail` shape."""
    report_id = _seed_report(
        conformance_client,
        title="Detail-shape conformance",
        description="Detail body content.",
        severity="high",
        tags=["detail-check"],
    )
    response = conformance_client.get(f"{LIST_PATH}/{report_id}")
    if response.status_code != 200:
        pytest.fail(
            f"GET /reports/{{id}} MUST return 200 for a known id; "
            f"got {response.status_code}. Body: {response.text[:500]}"
        )
    body = response.json()
    required_fields = {
        "id",
        "title",
        "severity",
        "status",
        "created_at",
        "description",
        "tags",
        "context",
        "lifecycle",
    }
    missing = required_fields - set(body.keys())
    if missing:
        pytest.fail(
            f"BugReportDetail MUST include {sorted(required_fields)}; missing: {sorted(missing)}"
        )
    if body.get("id") != report_id:
        pytest.fail(f"Detail id mismatch: requested {report_id}, got {body.get('id')!r}")
    if not isinstance(body.get("lifecycle"), list):
        pytest.fail(
            f"`lifecycle` MUST be a list (audit log); got {type(body.get('lifecycle')).__name__}"
        )


def test_screenshot_returns_image_png(conformance_client: httpx.Client) -> None:
    """`GET /reports/{id}/screenshot` MUST return PNG bytes with `image/png` content-type."""
    report_id = _seed_report(conformance_client, title="Screenshot content-type check")
    response = conformance_client.get(f"{LIST_PATH}/{report_id}/screenshot")
    if response.status_code != 200:
        pytest.fail(
            f"Screenshot endpoint MUST return 200 for a known id; "
            f"got {response.status_code}. Body: {response.text[:500]}"
        )
    content_type = response.headers.get("content-type", "")
    if not content_type.lower().startswith("image/png"):
        pytest.fail(f"Screenshot Content-Type MUST be image/png; got {content_type!r}")
    if not response.content.startswith(b"\x89PNG\r\n\x1a\n"):
        pytest.fail("Screenshot body MUST start with the PNG magic bytes (\\x89PNG\\r\\n\\x1a\\n).")


@pytest.mark.parametrize(
    "subpath",
    ["", "/screenshot"],
)
def test_unknown_id_returns_404(conformance_client: httpx.Client, subpath: str) -> None:
    """Both detail and screenshot endpoints MUST return 404 for an unknown id."""
    response = conformance_client.get(f"{LIST_PATH}/bug-does-not-exist-xyz{subpath}")
    if response.status_code != 404:
        pytest.fail(
            f"Unknown report id MUST return 404 on {LIST_PATH}/<id>{subpath}; "
            f"got {response.status_code}. Body: {response.text[:500]}"
        )
