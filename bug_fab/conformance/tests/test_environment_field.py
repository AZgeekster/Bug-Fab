"""Conformance tests for the optional `environment` metadata field (CC4).

Per `docs/decisions.md` § Decisions Locked 2026-04-27 (post-audit pass) and
the protocol spec, `environment` is an optional consumer-defined string in
the metadata payload. Typical values: `dev`, `staging`, `prod`. The field
prevents real dev/prod data mixing in shared collectors.

Two clauses to assert:
1. Submitting WITH `environment` MUST succeed and round-trip through
   retrieval — the field is preserved end-to-end.
2. Submitting WITHOUT `environment` MUST also succeed — the field is
   genuinely optional, not a soft requirement.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from bug_fab.conformance.fixtures import make_test_metadata, make_test_png

if TYPE_CHECKING:
    import httpx


INTAKE_PATH = "/bug-reports"
LIST_PATH = "/reports"


def _post_with_metadata(client: httpx.Client, metadata: str) -> httpx.Response:
    """Helper: POST a multipart submission with the given metadata string."""
    return client.post(
        INTAKE_PATH,
        data={"metadata": metadata},
        files={"screenshot": ("screenshot.png", make_test_png(), "image/png")},
    )


def test_environment_dev_round_trips(conformance_client: httpx.Client) -> None:
    """Submitting `environment: "dev"` MUST succeed and the value MUST persist."""
    metadata_dict = json.loads(make_test_metadata(title="Environment field — dev"))
    metadata_dict.setdefault("context", {})["environment"] = "dev"

    response = _post_with_metadata(conformance_client, json.dumps(metadata_dict))
    if response.status_code != 201:
        pytest.fail(
            f"Submission with environment='dev' MUST return 201; "
            f"got {response.status_code}. Body: {response.text[:500]}"
        )
    report_id = response.json()["id"]

    detail = conformance_client.get(f"{LIST_PATH}/{report_id}")
    if detail.status_code != 200:
        pytest.fail(
            f"Detail fetch failed with {detail.status_code}; cannot verify environment round-trip."
        )
    body = detail.json()

    # Environment may surface either at the top level OR inside context — both
    # shapes are acceptable per protocol; just assert the value is preserved
    # somewhere reachable.
    top_level = body.get("environment")
    nested = body.get("context", {}).get("environment")
    if top_level != "dev" and nested != "dev":
        pytest.fail(
            f"environment='dev' MUST round-trip via either top-level "
            f"`environment` or `context.environment`; got top-level={top_level!r}, "
            f"context.environment={nested!r}"
        )


def test_environment_field_is_optional(conformance_client: httpx.Client) -> None:
    """Submitting WITHOUT the `environment` field MUST still return 201."""
    metadata_dict = json.loads(make_test_metadata(title="Environment field — omitted"))
    # Strip the field from both top level and context to be thorough.
    metadata_dict.pop("environment", None)
    if "context" in metadata_dict:
        metadata_dict["context"].pop("environment", None)

    response = _post_with_metadata(conformance_client, json.dumps(metadata_dict))
    if response.status_code != 201:
        pytest.fail(
            f"environment is optional — submission without it MUST return 201; "
            f"got {response.status_code}. Body: {response.text[:500]}"
        )
