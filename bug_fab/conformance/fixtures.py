"""Canonical request/response fixtures for the conformance test suite.

These helpers exist so every conformance test starts from an identical,
known-good baseline payload. Tests then mutate the baseline (drop a field,
inject an invalid value, oversize the screenshot) to exercise specific
protocol clauses.

WHY hand-built PNG (`make_test_png`): the PNG is constructed from raw bytes
via `struct.pack` so the conformance plugin has zero image-library
dependencies. Adapter authors install `bug-fab` and run the plugin without
needing Pillow, OpenCV, or any other heavyweight imaging dep.

WHY string-typed metadata (`make_test_metadata`): the wire protocol carries
metadata as a JSON string inside a multipart form, not as a parsed object.
Returning the JSON-serialized string lets tests pass it straight to
`httpx.Client.post(..., data={"metadata": ...})` without re-serializing.
"""

from __future__ import annotations

import json
import struct
import zlib
from typing import Any


def make_test_png(width: int = 10, height: int = 10) -> bytes:
    """Build a minimal valid PNG byte sequence at the given dimensions.

    The PNG is a solid red 8-bit RGB image. The implementation hand-rolls the
    IHDR / IDAT / IEND chunks with CRC32 checksums per the PNG spec so it
    survives strict magic-byte validators (which adapters MUST run on the
    incoming screenshot — see `validate_upload_file` in the reference
    adapter).

    WHY default 10x10 (not 1x1): some image-validation libraries reject
    1x1 images as suspicious; 10x10 is small enough to keep the test
    payload tiny but large enough to look real.

    Args:
        width: Image width in pixels. Must be >= 1.
        height: Image height in pixels. Must be >= 1.

    Returns:
        Bytes of a valid PNG file.
    """
    if width < 1 or height < 1:
        raise ValueError("width and height must be >= 1")

    # PNG signature
    signature = b"\x89PNG\r\n\x1a\n"

    # IHDR chunk: width, height, bit_depth=8, color_type=2 (RGB), compression=0, filter=0, interlace=0
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    ihdr_crc = zlib.crc32(b"IHDR" + ihdr_data)
    ihdr = struct.pack(">I", len(ihdr_data)) + b"IHDR" + ihdr_data + struct.pack(">I", ihdr_crc)

    # IDAT chunk: each scanline starts with a filter byte (0 = None) followed by RGB triplets
    raw_scanlines = b""
    for _ in range(height):
        raw_scanlines += b"\x00" + (b"\xff\x00\x00" * width)
    idat_data = zlib.compress(raw_scanlines)
    idat_crc = zlib.crc32(b"IDAT" + idat_data)
    idat = struct.pack(">I", len(idat_data)) + b"IDAT" + idat_data + struct.pack(">I", idat_crc)

    # IEND chunk: empty payload
    iend_crc = zlib.crc32(b"IEND")
    iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", iend_crc)

    return signature + ihdr + idat + iend


def _baseline_metadata() -> dict[str, Any]:
    """The canonical baseline metadata dict — every required field populated.

    Required fields per `docs/protocol-schema.json`:
    `protocol_version`, `title`, `client_ts`. Adapter authors should be able
    to use this baseline as-is and have intake return 201; if they need to
    drop or mutate a required field, that's what `make_test_metadata`
    overrides are for.
    """
    return {
        "protocol_version": "0.1",
        "title": "Conformance test bug report",
        "client_ts": "2026-04-30T12:00:00+00:00",
        "report_type": "bug",
        "description": "Submitted by the bug-fab-conformance pytest plugin.",
        "expected_behavior": "Adapter accepts the submission and returns 201.",
        "severity": "medium",
        "tags": ["conformance"],
        "reporter": {"name": "", "email": "", "user_id": ""},
        "context": {
            "url": "http://localhost/sample/path",
            "module": "sample",
            "user_agent": "bug-fab-conformance/0.1",
            "viewport_width": 1280,
            "viewport_height": 720,
            "console_errors": [],
            "network_log": [],
        },
    }


def make_test_metadata(**overrides: Any) -> str:
    """Return a JSON-serialized metadata string with optional field overrides.

    Top-level fields override directly. To override a nested context field,
    pass a full `context=` dict; partial-merge of nested dicts is intentionally
    NOT supported — tests should be explicit about the full nested shape they
    want to send.

    Args:
        **overrides: Top-level metadata fields to override (e.g. `title=`,
            `severity=`, `tags=`, `context=`).

    Returns:
        JSON string ready to drop into a multipart `metadata` form field.
    """
    payload = _baseline_metadata()
    payload.update(overrides)
    return json.dumps(payload)


def make_invalid_severity_metadata() -> str:
    """Return metadata with `severity: "urgent"` for the strict-validation test.

    Per CC11 (decisions.md, 2026-04-27 post-audit pass), adapters MUST reject
    invalid severity values with 422 — silent coercion to `medium` (which is
    one prior implementation's behavior) fails conformance.

    Returns:
        JSON string with severity set to the disallowed value `"urgent"`.
    """
    return make_test_metadata(severity="urgent")


def make_legacy_status_payload() -> dict[str, Any]:
    """Return a stored-report dict carrying the deprecated `status: "resolved"`.

    Per CC12 (decisions.md, 2026-04-27 post-audit pass), adapters MUST
    accept deprecated enum values on read (so long-lived storage stays
    parseable across protocol revisions). They MAY reject the same value on
    write — the test_deprecated_values conformance test asserts both halves
    of this rule.

    The `resolved` value comes from a real-world prior implementation's
    `index.json` carrying an orphan from an earlier schema version.

    Returns:
        Dict shaped like a stored `BugReportDetail`, with `status` set to
        the deprecated `"resolved"` value.
    """
    return {
        "id": "bug-legacy-001",
        "title": "Legacy report carrying deprecated status",
        "report_type": "bug",
        "severity": "medium",
        "status": "resolved",
        "module": "legacy",
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-02T00:00:00Z",
        "has_screenshot": False,
        "github_issue_url": None,
        "description": "Pre-existing report with a deprecated status enum value.",
        "expected_behavior": "",
        "tags": [],
        "reporter": {"name": "", "email": "", "user_id": ""},
        "context": {
            "url": "",
            "module": "legacy",
            "user_agent": "",
            "viewport_width": 0,
            "viewport_height": 0,
            "console_errors": [],
            "network_log": [],
        },
        "lifecycle": [
            {"action": "created", "by": "legacy", "at": "2024-01-01T00:00:00Z"},
            {"action": "status:resolved", "by": "legacy", "at": "2024-01-02T00:00:00Z"},
        ],
        "server_user_agent": "",
        "client_reported_user_agent": "",
        "environment": "",
        "client_ts": "",
        "protocol_version": "0.1",
    }
