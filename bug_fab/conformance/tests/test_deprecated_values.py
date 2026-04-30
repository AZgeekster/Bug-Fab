"""Conformance tests for the deprecated-values rule (CC12).

Per `docs/PROTOCOL.md` and decisions.md § Decisions Locked 2026-04-27
(post-audit pass), adapters MUST accept deprecated enum values on read so
long-lived storage stays parseable across protocol revisions. They MAY
reject the same value on write — strictness on write keeps new data clean
while leniency on read keeps old data accessible.

The `"resolved"` status comes from a real production deployment that
carried orphan `resolved` values from an earlier schema. The CC12 rule
exists to guarantee that upgrading a long-lived consumer never breaks
their backlog.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from bug_fab.conformance.fixtures import (
    make_legacy_status_payload,
    make_test_metadata,
    make_test_png,
)

if TYPE_CHECKING:
    import httpx


INTAKE_PATH = "/bug-reports"
LIST_PATH = "/reports"
DEPRECATED_STATUS = "resolved"


def _seed_report(client: httpx.Client) -> str:
    """Submit a normal report and return its id (used as a write-target)."""
    response = client.post(
        INTAKE_PATH,
        data={"metadata": make_test_metadata(title="Deprecated-status write test")},
        files={"screenshot": ("screenshot.png", make_test_png(), "image/png")},
    )
    if response.status_code != 201:
        pytest.skip(f"Could not seed report — intake returned {response.status_code}.")
    return response.json()["id"]


def test_deprecated_status_rejected_on_write(
    conformance_client: httpx.Client,
    conformance_viewer_client: httpx.Client,
) -> None:
    """CC12 (write half): `PUT status: "resolved"` MUST return 422.

    Adapters MAY reject deprecated values on write — and the v0.1
    conformance contract says they MUST. Accepting a deprecated value on
    write would slowly re-pollute clean storage with values the protocol
    has already retired.
    """
    report_id = _seed_report(conformance_client)
    response = conformance_viewer_client.put(
        f"{LIST_PATH}/{report_id}/status",
        json={"status": DEPRECATED_STATUS},
    )
    if response.status_code != 422:
        pytest.fail(
            f"CC12: deprecated status {DEPRECATED_STATUS!r} MUST be rejected "
            f"on write with 422; got {response.status_code}. "
            f"Body: {response.text[:500]}"
        )


def test_legacy_payload_helper_carries_deprecated_status() -> None:
    """The `make_legacy_status_payload` helper itself MUST carry `status: resolved`.

    This is a sanity check on the fixture used by the read-side test below;
    if the helper drifts to a non-deprecated status, the read-side test
    silently stops exercising CC12.
    """
    payload = make_legacy_status_payload()
    if payload.get("status") != DEPRECATED_STATUS:
        pytest.fail(
            f"make_legacy_status_payload() MUST carry status={DEPRECATED_STATUS!r} "
            f"(got {payload.get('status')!r}). Update the helper."
        )


def test_deprecated_status_payload_is_json_round_trippable() -> None:
    """The legacy payload MUST be a serializable dict.

    Adapters that read this payload from disk/DB into Python land via
    `json.loads(...)` will see exactly this shape — the test pins the
    contract so future fixture edits do not silently break adapter
    deserialization tests downstream.
    """
    payload = make_legacy_status_payload()
    try:
        round_tripped = json.loads(json.dumps(payload))
    except (TypeError, ValueError) as exc:  # noqa: BLE001
        pytest.fail(f"Legacy payload must JSON round-trip; failed with: {exc!r}")
        return

    if round_tripped != payload:
        pytest.fail(
            "Legacy payload changed shape across JSON round-trip; "
            "non-trivial values (sets, datetimes) need normalising in the helper."
        )
