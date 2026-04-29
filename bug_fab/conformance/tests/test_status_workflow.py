"""Conformance tests for the status workflow + bulk operations.

Covers `PUT /reports/{id}/status`, `POST /bulk-close-fixed`, and
`POST /bulk-archive-closed`. The lifecycle audit log is asserted alongside
each status mutation because the audit log IS the contract — a status
change without a lifecycle entry is a silent loss of provenance.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from bug_fab.conformance.fixtures import make_test_metadata, make_test_png

if TYPE_CHECKING:
    import httpx


INTAKE_PATH = "/bug-reports"
LIST_PATH = "/reports"
BULK_CLOSE_PATH = "/bulk-close-fixed"
BULK_ARCHIVE_PATH = "/bulk-archive-closed"


def _seed_report(client: httpx.Client, **metadata_overrides: object) -> str:
    """Submit a report, return its id, skipping on intake failure.

    Same pattern as `test_viewer._seed_report` — intentionally duplicated
    rather than centralised to keep each test module independently
    runnable when an adapter author scopes pytest to a single file.
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
    return response.json()["id"]


def test_valid_status_change_succeeds(conformance_client: httpx.Client) -> None:
    """`PUT /reports/{id}/status` with `status: fixed` MUST succeed."""
    report_id = _seed_report(conformance_client, title="Valid status change")
    response = conformance_client.put(
        f"{LIST_PATH}/{report_id}/status",
        json={"status": "fixed", "fix_commit": "deadbeef", "fix_description": "fixed it"},
    )
    if response.status_code not in {200, 204}:
        pytest.fail(
            f"PUT /reports/{{id}}/status with valid status MUST return 200/204; "
            f"got {response.status_code}. Body: {response.text[:500]}"
        )

    # Verify the status actually changed
    detail = conformance_client.get(f"{LIST_PATH}/{report_id}").json()
    if detail.get("status") != "fixed":
        pytest.fail(
            f"After PUT status=fixed, detail status MUST be 'fixed'; got {detail.get('status')!r}"
        )


def test_invalid_status_returns_422(conformance_client: httpx.Client) -> None:
    """An unknown status value MUST be rejected with 422.

    Mirrors the severity strictness rule (CC11) for the status enum: silent
    coercion of `"unknown"` to a default loses the spec's "locked enum"
    guarantee.
    """
    report_id = _seed_report(conformance_client, title="Invalid status check")
    response = conformance_client.put(
        f"{LIST_PATH}/{report_id}/status",
        json={"status": "unknown"},
    )
    if response.status_code != 422:
        pytest.fail(
            f"Invalid status 'unknown' MUST return 422; "
            f"got {response.status_code}. Body: {response.text[:500]}"
        )


def test_lifecycle_audit_log_is_appended(conformance_client: httpx.Client) -> None:
    """Each status change MUST append an entry to `lifecycle: list[dict]`."""
    report_id = _seed_report(conformance_client, title="Lifecycle audit log check")
    before = conformance_client.get(f"{LIST_PATH}/{report_id}").json()
    before_count = len(before.get("lifecycle", []))

    response = conformance_client.put(
        f"{LIST_PATH}/{report_id}/status",
        json={"status": "investigating"},
    )
    if response.status_code not in {200, 204}:
        pytest.skip(
            f"Setup failed — status update returned {response.status_code}. "
            f"Fix `test_valid_status_change_succeeds` first."
        )

    after = conformance_client.get(f"{LIST_PATH}/{report_id}").json()
    after_count = len(after.get("lifecycle", []))

    if after_count != before_count + 1:
        pytest.fail(
            f"Status change MUST append exactly one lifecycle entry; "
            f"before={before_count}, after={after_count}"
        )

    last_entry = after["lifecycle"][-1]
    required_keys = {"action", "by", "at"}
    missing = required_keys - set(last_entry.keys())
    if missing:
        pytest.fail(
            f"Lifecycle entry MUST include {sorted(required_keys)} per protocol; "
            f"missing: {sorted(missing)}. Entry: {last_entry!r}"
        )


def test_bulk_close_fixed_returns_count(conformance_client: httpx.Client) -> None:
    """`POST /bulk-close-fixed` MUST return `{closed: int}`."""
    report_id = _seed_report(conformance_client, title="Bulk close-fixed seed")
    fix_response = conformance_client.put(
        f"{LIST_PATH}/{report_id}/status",
        json={"status": "fixed"},
    )
    if fix_response.status_code not in {200, 204}:
        pytest.skip("Could not transition report to 'fixed' to seed bulk-close test.")

    response = conformance_client.post(BULK_CLOSE_PATH)
    if response.status_code not in {200, 204}:
        pytest.fail(
            f"POST /bulk-close-fixed MUST return 200/204; "
            f"got {response.status_code}. Body: {response.text[:500]}"
        )
    if response.status_code == 200:
        body = response.json()
        if "closed" not in body:
            pytest.fail(f"Bulk-close response MUST include `closed: int`; got {body!r}")
        if not isinstance(body["closed"], int):
            pytest.fail(f"`closed` MUST be an int; got {type(body['closed']).__name__}")


def test_bulk_archive_closed_returns_count(conformance_client: httpx.Client) -> None:
    """`POST /bulk-archive-closed` MUST return `{archived: int}`."""
    report_id = _seed_report(conformance_client, title="Bulk archive-closed seed")
    for next_status in ("fixed", "closed"):
        step = conformance_client.put(
            f"{LIST_PATH}/{report_id}/status",
            json={"status": next_status},
        )
        if step.status_code not in {200, 204}:
            pytest.skip(
                f"Could not transition seed report to {next_status!r} "
                f"(got {step.status_code}); cannot verify bulk-archive."
            )

    response = conformance_client.post(BULK_ARCHIVE_PATH)
    if response.status_code not in {200, 204}:
        pytest.fail(
            f"POST /bulk-archive-closed MUST return 200/204; "
            f"got {response.status_code}. Body: {response.text[:500]}"
        )
    if response.status_code == 200:
        body = response.json()
        if "archived" not in body:
            pytest.fail(f"Bulk-archive response MUST include `archived: int`; got {body!r}")
        if not isinstance(body["archived"], int):
            pytest.fail(f"`archived` MUST be an int; got {type(body['archived']).__name__}")
