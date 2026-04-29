"""Regenerate ``docs/protocol-schema.json`` from the Pydantic models.

The JSON Schema is the **authoritative** wire-protocol contract for Bug-Fab v0.1.
``docs/PROTOCOL.md`` is human-readable commentary on top of it; if the prose ever
disagrees with the schema, the schema wins.

Usage
-----

Regenerate the on-disk schema after editing ``bug_fab/schemas.py``::

    python scripts/generate_protocol_schema.py

CI drift-check mode (exits non-zero if the on-disk schema is stale)::

    python scripts/generate_protocol_schema.py --check

The drift check is the gate that prevents Pydantic models and the published
schema from getting out of sync.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic.json_schema import models_json_schema

from bug_fab.schemas import (
    BugReportContext,
    BugReportCreate,
    BugReportDetail,
    BugReportListResponse,
    BugReportStatusUpdate,
    BugReportSummary,
    LifecycleEvent,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "docs" / "protocol-schema.json"


def build_schema() -> dict:
    """Build the combined JSON Schema document from the Pydantic models."""
    models = [
        (BugReportCreate, "validation"),
        (BugReportContext, "validation"),
        (BugReportStatusUpdate, "validation"),
        (LifecycleEvent, "validation"),
        (BugReportSummary, "validation"),
        (BugReportDetail, "validation"),
        (BugReportListResponse, "validation"),
    ]
    _, combined = models_json_schema(models, ref_template="#/$defs/{model}")
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://github.com/AZgeekster/Bug-Fab/blob/main/docs/protocol-schema.json",
        "title": "Bug-Fab Wire Protocol v0.1",
        "description": (
            "Authoritative JSON Schema for the Bug-Fab v0.1 wire protocol. "
            "Auto-generated from bug_fab/schemas.py Pydantic models. "
            "If this disagrees with docs/PROTOCOL.md prose, this schema wins."
        ),
        **combined,
    }


def serialize(schema: dict) -> str:
    """Pretty-print the schema with stable, deterministic formatting."""
    return json.dumps(schema, indent=2, ensure_ascii=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Exit non-zero if the on-disk schema does not match what the "
            "current Pydantic models would generate. Used by CI."
        ),
    )
    args = parser.parse_args()

    expected = serialize(build_schema())

    if args.check:
        try:
            actual = SCHEMA_PATH.read_text(encoding="utf-8")
        except FileNotFoundError:
            print(
                f"protocol-schema.json drift: {SCHEMA_PATH} is missing.\n"
                "Regenerate with: python scripts/generate_protocol_schema.py",
                file=sys.stderr,
            )
            return 1
        if actual != expected:
            print(
                f"protocol-schema.json drift detected.\n"
                f"On-disk:   {SCHEMA_PATH}\n"
                f"Generated from bug_fab/schemas.py does not match.\n\n"
                "The Pydantic models have changed since the JSON Schema was last regenerated.\n"
                "Regenerate with: python scripts/generate_protocol_schema.py",
                file=sys.stderr,
            )
            return 1
        print("protocol-schema.json is up to date.")
        return 0

    SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_PATH.write_text(expected, encoding="utf-8")
    print(f"Wrote {SCHEMA_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
