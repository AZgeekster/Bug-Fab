"""Unit tests for the file-backed storage backend.

Covers the full ``Storage`` ABC surface against ``FileStorage``: round-trip
of save/get, list filtering + pagination, status workflow, delete, archive,
bulk operations, ID-prefix env var, and the path-traversal guard.

Async storage methods run via ``asyncio.run`` wrappers so the synchronous
pytest test bodies stay readable.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from bug_fab.storage.files import _REPORT_ID_RE, FileStorage


def _run(coro):  # type: ignore[no-untyped-def]
    """Run a coroutine in a fresh event loop and return its value."""
    return asyncio.new_event_loop().run_until_complete(coro)


def _baseline_metadata(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": "Sample report",
        "report_type": "bug",
        "description": "desc",
        "expected_behavior": "should work",
        "severity": "medium",
        "tags": ["sample"],
        "context": {
            "url": "/x",
            "module": "modA",
            "user_agent": "client-ua/1.0",
            "viewport_width": 1024,
            "viewport_height": 768,
            "console_errors": [],
            "network_log": [],
            "environment": "dev",
        },
        "server_user_agent": "server-ua/1.0",
        "client_reported_user_agent": "client-ua/1.0",
        "environment": "dev",
    }
    payload.update(overrides)
    return payload


# -----------------------------------------------------------------------------
# Save / get round trip
# -----------------------------------------------------------------------------


def test_save_then_get_preserves_all_fields(file_storage: FileStorage, tiny_png: bytes) -> None:
    metadata = _baseline_metadata(title="Round-trip check")
    report_id = _run(file_storage.save_report(metadata, tiny_png))
    assert _REPORT_ID_RE.match(report_id)

    detail = _run(file_storage.get_report(report_id))
    assert detail is not None
    assert detail.id == report_id
    assert detail.title == "Round-trip check"
    assert detail.severity == "medium"
    assert detail.status == "open"
    assert detail.module == "modA"
    assert detail.environment == "dev"
    assert detail.client_reported_user_agent == "client-ua/1.0"
    assert detail.server_user_agent == "server-ua/1.0"
    assert detail.tags == ["sample"]
    assert len(detail.lifecycle) == 1
    assert detail.lifecycle[0].action == "created"


def test_save_writes_screenshot(file_storage: FileStorage, tiny_png: bytes) -> None:
    report_id = _run(file_storage.save_report(_baseline_metadata(), tiny_png))
    path = _run(file_storage.get_screenshot_path(report_id))
    assert path is not None
    assert path.exists()
    assert path.read_bytes() == tiny_png


def test_get_report_returns_none_for_missing(file_storage: FileStorage) -> None:
    assert _run(file_storage.get_report("bug-999")) is None


# -----------------------------------------------------------------------------
# Listing + filtering + pagination
# -----------------------------------------------------------------------------


def test_list_pagination(file_storage: FileStorage, tiny_png: bytes) -> None:
    for i in range(5):
        _run(file_storage.save_report(_baseline_metadata(title=f"Report {i}"), tiny_png))
    items_p1, total = _run(file_storage.list_reports({}, page=1, page_size=2))
    items_p2, _ = _run(file_storage.list_reports({}, page=2, page_size=2))
    items_p3, _ = _run(file_storage.list_reports({}, page=3, page_size=2))
    assert total == 5
    assert len(items_p1) == 2
    assert len(items_p2) == 2
    assert len(items_p3) == 1
    seen = {item.id for item in items_p1 + items_p2 + items_p3}
    assert len(seen) == 5


@pytest.mark.parametrize(
    "field,value,non_matching",
    [
        ("severity", "critical", "low"),
        ("module", "alpha", "beta"),
        ("report_type", "feature_request", "bug"),
    ],
)
def test_list_filters(
    file_storage: FileStorage, tiny_png: bytes, field: str, value: str, non_matching: str
) -> None:
    md_match = _baseline_metadata()
    md_match[field] = value
    if field == "module":
        md_match["context"]["module"] = value

    md_other = _baseline_metadata()
    md_other[field] = non_matching
    if field == "module":
        md_other["context"]["module"] = non_matching

    _run(file_storage.save_report(md_match, tiny_png))
    _run(file_storage.save_report(md_other, tiny_png))

    items, total = _run(file_storage.list_reports({field: value}, page=1, page_size=20))
    assert total == 1
    assert len(items) == 1


def test_list_filter_status(file_storage: FileStorage, tiny_png: bytes) -> None:
    bid = _run(file_storage.save_report(_baseline_metadata(), tiny_png))
    _run(file_storage.update_status(bid, status="fixed"))
    _run(file_storage.save_report(_baseline_metadata(title="other"), tiny_png))

    fixed_items, total = _run(file_storage.list_reports({"status": "fixed"}, page=1, page_size=20))
    assert total == 1
    assert fixed_items[0].id == bid


def test_list_filter_search_matches_title(file_storage: FileStorage, tiny_png: bytes) -> None:
    _run(file_storage.save_report(_baseline_metadata(title="alpha widget"), tiny_png))
    _run(file_storage.save_report(_baseline_metadata(title="beta gadget"), tiny_png))

    items, total = _run(file_storage.list_reports({"search": "widget"}, page=1, page_size=20))
    assert total == 1
    assert items[0].title == "alpha widget"


# -----------------------------------------------------------------------------
# Update status / lifecycle
# -----------------------------------------------------------------------------


def test_update_status_appends_lifecycle(file_storage: FileStorage, tiny_png: bytes) -> None:
    bid = _run(file_storage.save_report(_baseline_metadata(), tiny_png))
    updated = _run(
        file_storage.update_status(
            bid,
            status="fixed",
            fix_commit="deadbeef",
            fix_description="patched",
            by="alice",
        )
    )
    assert updated is not None
    assert updated.status == "fixed"
    # First entry was 'created', second is 'status_changed'
    assert len(updated.lifecycle) == 2
    last = updated.lifecycle[-1]
    assert last.action == "status_changed"
    assert last.by == "alice"
    assert last.fix_commit == "deadbeef"
    assert last.fix_description == "patched"


def test_update_status_unknown_id_returns_none(file_storage: FileStorage) -> None:
    assert _run(file_storage.update_status("bug-999", status="fixed")) is None


def test_update_status_invalid_id_pattern_returns_none(
    file_storage: FileStorage,
) -> None:
    assert _run(file_storage.update_status("not-an-id", status="fixed")) is None


# -----------------------------------------------------------------------------
# Delete + archive
# -----------------------------------------------------------------------------


def test_delete_removes_metadata_and_screenshot(
    file_storage: FileStorage, tiny_png: bytes, tmp_storage_dir: Path
) -> None:
    bid = _run(file_storage.save_report(_baseline_metadata(), tiny_png))
    json_path = tmp_storage_dir / f"{bid}.json"
    png_path = tmp_storage_dir / f"{bid}.png"
    assert json_path.exists()
    assert png_path.exists()

    deleted = _run(file_storage.delete_report(bid))
    assert deleted is True
    assert not json_path.exists()
    assert not png_path.exists()
    assert _run(file_storage.get_report(bid)) is None


def test_delete_returns_false_for_missing(file_storage: FileStorage) -> None:
    assert _run(file_storage.delete_report("bug-999")) is False


def test_delete_invalid_id_returns_false(file_storage: FileStorage) -> None:
    assert _run(file_storage.delete_report("../etc/passwd")) is False


def test_archive_moves_files_to_archive_subdir(
    file_storage: FileStorage, tiny_png: bytes, tmp_storage_dir: Path
) -> None:
    bid = _run(file_storage.save_report(_baseline_metadata(), tiny_png))
    archived = _run(file_storage.archive_report(bid))
    assert archived is True

    json_archived = tmp_storage_dir / "archive" / f"{bid}.json"
    png_archived = tmp_storage_dir / "archive" / f"{bid}.png"
    assert json_archived.exists()
    assert png_archived.exists()
    # Live copy is gone
    assert not (tmp_storage_dir / f"{bid}.json").exists()
    assert not (tmp_storage_dir / f"{bid}.png").exists()


def test_archive_dropped_from_listing(file_storage: FileStorage, tiny_png: bytes) -> None:
    bid = _run(file_storage.save_report(_baseline_metadata(), tiny_png))
    _run(file_storage.archive_report(bid))
    items, total = _run(file_storage.list_reports({}, page=1, page_size=20))
    assert total == 0
    assert items == []


def test_archive_missing_id_returns_false(file_storage: FileStorage) -> None:
    assert _run(file_storage.archive_report("bug-999")) is False


def test_archived_report_still_readable_via_get(file_storage: FileStorage, tiny_png: bytes) -> None:
    """Archive doesn't delete — ``get_report`` still finds the JSON."""
    bid = _run(file_storage.save_report(_baseline_metadata(), tiny_png))
    _run(file_storage.archive_report(bid))
    detail = _run(file_storage.get_report(bid))
    assert detail is not None
    assert detail.id == bid


def test_archived_screenshot_path_resolves_to_archive(
    file_storage: FileStorage, tiny_png: bytes, tmp_storage_dir: Path
) -> None:
    bid = _run(file_storage.save_report(_baseline_metadata(), tiny_png))
    _run(file_storage.archive_report(bid))
    path = _run(file_storage.get_screenshot_path(bid))
    assert path is not None
    assert path == tmp_storage_dir / "archive" / f"{bid}.png"


# -----------------------------------------------------------------------------
# Bulk ops
# -----------------------------------------------------------------------------


def test_bulk_close_fixed_only_targets_fixed(file_storage: FileStorage, tiny_png: bytes) -> None:
    open_id = _run(file_storage.save_report(_baseline_metadata(title="open"), tiny_png))
    fixed_id = _run(file_storage.save_report(_baseline_metadata(title="fix"), tiny_png))
    invest_id = _run(file_storage.save_report(_baseline_metadata(title="inv"), tiny_png))
    _run(file_storage.update_status(fixed_id, status="fixed"))
    _run(file_storage.update_status(invest_id, status="investigating"))

    closed = _run(file_storage.bulk_close_fixed(by="bot"))
    assert closed == 1

    detail_open = _run(file_storage.get_report(open_id))
    detail_fixed = _run(file_storage.get_report(fixed_id))
    detail_invest = _run(file_storage.get_report(invest_id))
    assert detail_open is not None and detail_open.status == "open"
    assert detail_fixed is not None and detail_fixed.status == "closed"
    assert detail_invest is not None and detail_invest.status == "investigating"

    # Lifecycle entry was appended by the bulk transition
    assert any(e.action == "status_changed" and e.by == "bot" for e in detail_fixed.lifecycle)


def test_bulk_close_fixed_returns_zero_when_none_match(
    file_storage: FileStorage, tiny_png: bytes
) -> None:
    _run(file_storage.save_report(_baseline_metadata(), tiny_png))
    assert _run(file_storage.bulk_close_fixed()) == 0


def test_bulk_archive_closed_archives_only_closed(
    file_storage: FileStorage, tiny_png: bytes, tmp_storage_dir: Path
) -> None:
    open_id = _run(file_storage.save_report(_baseline_metadata(title="o"), tiny_png))
    closed_id = _run(file_storage.save_report(_baseline_metadata(title="c"), tiny_png))
    fixed_id = _run(file_storage.save_report(_baseline_metadata(title="f"), tiny_png))
    _run(file_storage.update_status(closed_id, status="closed"))
    _run(file_storage.update_status(fixed_id, status="fixed"))

    archived = _run(file_storage.bulk_archive_closed())
    assert archived == 1

    archive_dir = tmp_storage_dir / "archive"
    assert (archive_dir / f"{closed_id}.json").exists()
    assert not (archive_dir / f"{open_id}.json").exists()
    assert not (archive_dir / f"{fixed_id}.json").exists()


# -----------------------------------------------------------------------------
# Atomicity (tmp + rename)
# -----------------------------------------------------------------------------


def test_atomic_index_write_uses_tmp_then_rename(
    file_storage: FileStorage, tiny_png: bytes, tmp_storage_dir: Path
) -> None:
    """The published index.json never has a .tmp sibling lingering after save."""
    _run(file_storage.save_report(_baseline_metadata(), tiny_png))
    index = tmp_storage_dir / "index.json"
    tmp_index = tmp_storage_dir / "index.json.tmp"
    assert index.exists()
    # tmp sibling is renamed away — should not linger after a successful save
    assert not tmp_index.exists()


def test_corrupt_index_falls_back_to_empty(tmp_storage_dir: Path, tiny_png: bytes) -> None:
    """An unreadable index.json should not block ``save_report`` — it rebuilds."""
    storage = FileStorage(tmp_storage_dir)
    # Corrupt the index file
    (tmp_storage_dir / "index.json").write_text("{not valid json", encoding="utf-8")
    bid = _run(storage.save_report(_baseline_metadata(), tiny_png))
    # The save still succeeds; the corrupted index is treated as empty.
    assert _REPORT_ID_RE.match(bid)
    items, total = _run(storage.list_reports({}, page=1, page_size=20))
    assert total == 1


# -----------------------------------------------------------------------------
# Path traversal guard
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_id",
    [
        "bug-../etc/passwd",
        "bug-..%2F..%2Fetc%2Fpasswd",
        "../bug-001",
        "bug-",
        "bug-abc",
        "bug-12",  # too short (regex requires 3+ digits)
        "..\\bug-001",
        "bug-../../../sensitive",
        "BUG-001",  # case-sensitive prefix
        "",
    ],
)
def test_invalid_ids_rejected_by_regex(bad_id: str) -> None:
    assert _REPORT_ID_RE.match(bad_id) is None


@pytest.mark.parametrize(
    "good_id",
    [
        "bug-001",
        "bug-100",
        "bug-99999",
        "bug-P001",
        "bug-D042",
        "bug-a042",
    ],
)
def test_valid_ids_accepted_by_regex(good_id: str) -> None:
    assert _REPORT_ID_RE.match(good_id) is not None


def test_get_report_with_path_traversal_id_returns_none(
    file_storage: FileStorage,
) -> None:
    assert _run(file_storage.get_report("bug-../etc/passwd")) is None


def test_get_screenshot_path_with_traversal_id_returns_none(
    file_storage: FileStorage,
) -> None:
    assert _run(file_storage.get_screenshot_path("../bug-001")) is None


# -----------------------------------------------------------------------------
# Deprecated values on read
# -----------------------------------------------------------------------------


def test_get_report_accepts_deprecated_status_resolved(
    file_storage: FileStorage, tmp_storage_dir: Path
) -> None:
    """CC12 (read half): a stored ``status: "resolved"`` MUST parse on read."""
    legacy_id = "bug-099"
    payload = {
        "id": legacy_id,
        "title": "Legacy",
        "report_type": "bug",
        "severity": "medium",
        "status": "resolved",  # deprecated value
        "module": "legacy",
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-02T00:00:00Z",
        "has_screenshot": False,
        "github_issue_url": None,
        "description": "Pre-existing report",
        "expected_behavior": "",
        "tags": [],
        "context": {
            "url": "",
            "module": "legacy",
            "user_agent": "",
            "viewport_width": 0,
            "viewport_height": 0,
            "console_errors": [],
            "network_log": [],
            "environment": "",
        },
        "lifecycle": [
            {
                "action": "created",
                "by": "legacy",
                "at": "2024-01-01T00:00:00Z",
                "fix_commit": "",
                "fix_description": "",
            }
        ],
        "server_user_agent": "",
        "client_reported_user_agent": "",
        "environment": "",
        "github_issue_number": None,
    }
    (tmp_storage_dir / f"{legacy_id}.json").write_text(json.dumps(payload), encoding="utf-8")

    detail = _run(file_storage.get_report(legacy_id))
    assert detail is not None
    assert detail.status == "resolved"


# -----------------------------------------------------------------------------
# ID prefix
# -----------------------------------------------------------------------------


def test_default_id_format_no_prefix(file_storage_factory, tiny_png: bytes) -> None:
    storage = file_storage_factory()
    bid = _run(storage.save_report(_baseline_metadata(), tiny_png))
    assert bid == "bug-001"


def test_id_prefix_constructor_arg(file_storage_factory, tiny_png: bytes) -> None:
    storage = file_storage_factory(id_prefix="P", subdir="prefix-dir")
    bid = _run(storage.save_report(_baseline_metadata(), tiny_png))
    assert bid == "bug-P001"


def test_subsequent_ids_increment(file_storage: FileStorage, tiny_png: bytes) -> None:
    a = _run(file_storage.save_report(_baseline_metadata(title="a"), tiny_png))
    b = _run(file_storage.save_report(_baseline_metadata(title="b"), tiny_png))
    c = _run(file_storage.save_report(_baseline_metadata(title="c"), tiny_png))
    assert (a, b, c) == ("bug-001", "bug-002", "bug-003")


def test_ids_persist_across_storage_instances(file_storage_factory, tiny_png: bytes) -> None:
    """Re-creating ``FileStorage`` over the same dir continues the counter."""
    storage_a = file_storage_factory(subdir="persist")
    _run(storage_a.save_report(_baseline_metadata(), tiny_png))
    _run(storage_a.save_report(_baseline_metadata(), tiny_png))
    storage_b = file_storage_factory(subdir="persist")
    third = _run(storage_b.save_report(_baseline_metadata(), tiny_png))
    assert third == "bug-003"
