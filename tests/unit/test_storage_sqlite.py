"""Unit tests for the SQLite storage backend.

Covers the same surface as ``test_storage_files`` plus dialect-specific
behaviors: lifecycle table population, bulk transactional writes, and the
Alembic upgrade path against a fresh DB.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from bug_fab.storage._models import (
    BugReport,
    BugReportIdCounter,
    BugReportLifecycle,
)
from bug_fab.storage.sqlite import SQLiteStorage


def _run(coro):  # type: ignore[no-untyped-def]
    return asyncio.new_event_loop().run_until_complete(coro)


def _baseline_metadata(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": "SQLite test report",
        "report_type": "bug",
        "description": "desc",
        "expected_behavior": "should work",
        "severity": "medium",
        "tags": ["sqlite"],
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
        "module": "modA",
    }
    payload.update(overrides)
    return payload


# -----------------------------------------------------------------------------
# Save / get / list
# -----------------------------------------------------------------------------


def test_save_then_get_round_trip(sqlite_storage: SQLiteStorage, tiny_png: bytes) -> None:
    bid = _run(sqlite_storage.save_report(_baseline_metadata(), tiny_png))
    detail = _run(sqlite_storage.get_report(bid))
    assert detail is not None
    assert detail.id == bid
    assert detail.title == "SQLite test report"
    assert detail.severity == "medium"
    assert detail.module == "modA"
    assert detail.environment == "dev"
    assert detail.client_reported_user_agent == "client-ua/1.0"
    assert detail.server_user_agent == "server-ua/1.0"


def test_first_save_id_is_bug_001(sqlite_storage: SQLiteStorage, tiny_png: bytes) -> None:
    bid = _run(sqlite_storage.save_report(_baseline_metadata(), tiny_png))
    assert bid == "bug-001"


def test_subsequent_ids_increment(sqlite_storage: SQLiteStorage, tiny_png: bytes) -> None:
    a = _run(sqlite_storage.save_report(_baseline_metadata(), tiny_png))
    b = _run(sqlite_storage.save_report(_baseline_metadata(), tiny_png))
    c = _run(sqlite_storage.save_report(_baseline_metadata(), tiny_png))
    assert (a, b, c) == ("bug-001", "bug-002", "bug-003")


def test_get_report_invalid_id_returns_none(
    sqlite_storage: SQLiteStorage,
) -> None:
    assert _run(sqlite_storage.get_report("../etc")) is None


def test_get_report_missing_id_returns_none(
    sqlite_storage: SQLiteStorage,
) -> None:
    assert _run(sqlite_storage.get_report("bug-999")) is None


def test_screenshot_path_resolves(sqlite_storage: SQLiteStorage, tiny_png: bytes) -> None:
    bid = _run(sqlite_storage.save_report(_baseline_metadata(), tiny_png))
    path = _run(sqlite_storage.get_screenshot_path(bid))
    assert path is not None
    assert path.exists()
    assert path.read_bytes() == tiny_png


def test_list_pagination(sqlite_storage: SQLiteStorage, tiny_png: bytes) -> None:
    for i in range(5):
        _run(sqlite_storage.save_report(_baseline_metadata(title=f"R{i}"), tiny_png))
    items, total = _run(sqlite_storage.list_reports({}, page=1, page_size=2))
    assert total == 5
    assert len(items) == 2


def test_list_filter_status(sqlite_storage: SQLiteStorage, tiny_png: bytes) -> None:
    a = _run(sqlite_storage.save_report(_baseline_metadata(title="a"), tiny_png))
    _run(sqlite_storage.save_report(_baseline_metadata(title="b"), tiny_png))
    _run(sqlite_storage.update_status(a, status="fixed"))

    items, total = _run(sqlite_storage.list_reports({"status": "fixed"}, page=1, page_size=20))
    assert total == 1
    assert items[0].id == a


def test_list_filter_severity(sqlite_storage: SQLiteStorage, tiny_png: bytes) -> None:
    _run(sqlite_storage.save_report(_baseline_metadata(severity="critical"), tiny_png))
    _run(sqlite_storage.save_report(_baseline_metadata(severity="low"), tiny_png))
    items, total = _run(sqlite_storage.list_reports({"severity": "critical"}, page=1, page_size=20))
    assert total == 1


def test_list_filter_module(sqlite_storage: SQLiteStorage, tiny_png: bytes) -> None:
    _run(sqlite_storage.save_report(_baseline_metadata(module="alpha"), tiny_png))
    _run(sqlite_storage.save_report(_baseline_metadata(module="beta"), tiny_png))
    items, total = _run(sqlite_storage.list_reports({"module": "alpha"}, page=1, page_size=20))
    assert total == 1


def test_list_filter_search(sqlite_storage: SQLiteStorage, tiny_png: bytes) -> None:
    _run(sqlite_storage.save_report(_baseline_metadata(title="alpha widget"), tiny_png))
    _run(sqlite_storage.save_report(_baseline_metadata(title="beta gadget"), tiny_png))
    items, total = _run(sqlite_storage.list_reports({"search": "widget"}, page=1, page_size=20))
    assert total == 1


# -----------------------------------------------------------------------------
# Lifecycle population on status change
# -----------------------------------------------------------------------------


def test_update_status_appends_lifecycle_row(
    sqlite_storage: SQLiteStorage, tiny_png: bytes
) -> None:
    bid = _run(sqlite_storage.save_report(_baseline_metadata(), tiny_png))
    updated = _run(
        sqlite_storage.update_status(
            bid,
            status="fixed",
            fix_commit="cafef00d",
            fix_description="patched",
            by="bob",
        )
    )
    assert updated is not None
    assert updated.status == "fixed"
    # The lifecycle now has both 'created' and the new 'status_changed'
    actions = [e.action for e in updated.lifecycle]
    assert actions == ["created", "status_changed"]
    last = updated.lifecycle[-1]
    assert last.by == "bob"
    assert last.fix_commit == "cafef00d"
    assert last.fix_description == "patched"


def test_update_status_returns_none_for_unknown_id(
    sqlite_storage: SQLiteStorage,
) -> None:
    assert _run(sqlite_storage.update_status("bug-999", status="fixed")) is None


def test_update_status_invalid_value_raises(sqlite_storage: SQLiteStorage, tiny_png: bytes) -> None:
    bid = _run(sqlite_storage.save_report(_baseline_metadata(), tiny_png))
    from bug_fab.storage._sql_base import StorageError

    with pytest.raises(StorageError):
        _run(sqlite_storage.update_status(bid, status="resolved"))  # deprecated


def test_save_invalid_severity_raises(sqlite_storage: SQLiteStorage, tiny_png: bytes) -> None:
    from bug_fab.storage._sql_base import StorageError

    bad = _baseline_metadata(severity="urgent")
    with pytest.raises(StorageError):
        _run(sqlite_storage.save_report(bad, tiny_png))


def test_save_missing_title_raises(sqlite_storage: SQLiteStorage, tiny_png: bytes) -> None:
    from bug_fab.storage._sql_base import StorageError

    with pytest.raises(StorageError):
        _run(sqlite_storage.save_report(_baseline_metadata(title=""), tiny_png))


# -----------------------------------------------------------------------------
# Delete + archive
# -----------------------------------------------------------------------------


def test_delete_removes_row_and_screenshot(
    sqlite_storage: SQLiteStorage, tiny_png: bytes, tmp_storage_dir: Path
) -> None:
    bid = _run(sqlite_storage.save_report(_baseline_metadata(), tiny_png))
    png = tmp_storage_dir / "screenshots" / f"{bid}.png"
    assert png.exists()

    deleted = _run(sqlite_storage.delete_report(bid))
    assert deleted is True
    assert _run(sqlite_storage.get_report(bid)) is None
    assert not png.exists()


def test_delete_returns_false_for_missing(sqlite_storage: SQLiteStorage) -> None:
    assert _run(sqlite_storage.delete_report("bug-999")) is False


def test_delete_invalid_id_returns_false(sqlite_storage: SQLiteStorage) -> None:
    assert _run(sqlite_storage.delete_report("../etc")) is False


def test_archive_stamps_archived_at(
    sqlite_storage: SQLiteStorage, tiny_png: bytes, tmp_storage_dir: Path
) -> None:
    bid = _run(sqlite_storage.save_report(_baseline_metadata(), tiny_png))
    archived = _run(sqlite_storage.archive_report(bid))
    assert archived is True

    # Listing excludes archived rows
    items, total = _run(sqlite_storage.list_reports({}, page=1, page_size=20))
    assert total == 0

    # Direct DB inspection: archived_at populated
    with sqlite_storage._session_factory() as session:
        row = session.get(BugReport, bid)
        assert row is not None
        assert row.archived_at is not None

    # Screenshot moved to archive subdir
    archived_path = tmp_storage_dir / "screenshots" / "archive" / f"{bid}.png"
    assert archived_path.exists()


def test_archive_idempotent_on_already_archived(
    sqlite_storage: SQLiteStorage, tiny_png: bytes
) -> None:
    bid = _run(sqlite_storage.save_report(_baseline_metadata(), tiny_png))
    assert _run(sqlite_storage.archive_report(bid)) is True
    # Already-archived returns False per the ABC contract
    assert _run(sqlite_storage.archive_report(bid)) is False


def test_archive_missing_id_returns_false(sqlite_storage: SQLiteStorage) -> None:
    assert _run(sqlite_storage.archive_report("bug-999")) is False


# -----------------------------------------------------------------------------
# Bulk operations are transactional and append lifecycle
# -----------------------------------------------------------------------------


def test_bulk_close_fixed_writes_lifecycle_rows(
    sqlite_storage: SQLiteStorage, tiny_png: bytes
) -> None:
    a = _run(sqlite_storage.save_report(_baseline_metadata(title="a"), tiny_png))
    b = _run(sqlite_storage.save_report(_baseline_metadata(title="b"), tiny_png))
    c = _run(sqlite_storage.save_report(_baseline_metadata(title="c"), tiny_png))
    _run(sqlite_storage.update_status(a, status="fixed"))
    _run(sqlite_storage.update_status(b, status="fixed"))
    _run(sqlite_storage.update_status(c, status="investigating"))

    closed = _run(sqlite_storage.bulk_close_fixed(by="job-runner"))
    assert closed == 2

    # Both 'fixed' reports now closed; investigating one untouched.
    detail_a = _run(sqlite_storage.get_report(a))
    detail_c = _run(sqlite_storage.get_report(c))
    assert detail_a is not None and detail_a.status == "closed"
    assert detail_c is not None and detail_c.status == "investigating"

    # Lifecycle: created + status_changed (fixed) + status_changed (closed)
    actions = [e.action for e in detail_a.lifecycle]
    assert actions == ["created", "status_changed", "status_changed"]


def test_bulk_close_fixed_zero_when_no_fixed(
    sqlite_storage: SQLiteStorage, tiny_png: bytes
) -> None:
    _run(sqlite_storage.save_report(_baseline_metadata(), tiny_png))
    assert _run(sqlite_storage.bulk_close_fixed()) == 0


def test_bulk_archive_closed_targets_only_closed(
    sqlite_storage: SQLiteStorage, tiny_png: bytes, tmp_storage_dir: Path
) -> None:
    a = _run(sqlite_storage.save_report(_baseline_metadata(title="a"), tiny_png))
    b = _run(sqlite_storage.save_report(_baseline_metadata(title="b"), tiny_png))
    c = _run(sqlite_storage.save_report(_baseline_metadata(title="c"), tiny_png))
    _run(sqlite_storage.update_status(a, status="closed"))
    _run(sqlite_storage.update_status(b, status="fixed"))

    archived = _run(sqlite_storage.bulk_archive_closed())
    assert archived == 1

    # Only 'a' was archived; b/c remain visible
    items, total = _run(sqlite_storage.list_reports({}, page=1, page_size=20))
    assert total == 2
    visible_ids = {item.id for item in items}
    assert visible_ids == {b, c}


# -----------------------------------------------------------------------------
# Schema migration
# -----------------------------------------------------------------------------


def test_create_all_creates_required_tables(
    sqlite_storage: SQLiteStorage,
) -> None:
    """``create_all`` should produce all model tables ready for use."""
    with sqlite_storage._session_factory() as session:
        # All tables exist and are queryable (no rows yet)
        assert session.scalars(select(BugReport)).all() == []
        assert session.scalars(select(BugReportLifecycle)).all() == []
        # The id-counter table starts empty; first save initializes it
        counter_rows = session.scalars(select(BugReportIdCounter)).all()
        assert counter_rows == []


def test_alembic_upgrade_head_creates_schema(tmp_path: Path) -> None:
    """The Alembic migration runs cleanly against a fresh SQLite database.

    This proves consumers who run ``alembic upgrade head`` get a working
    schema without using the test-only ``create_all`` shortcut.
    """
    from alembic import command
    from alembic.config import Config

    db_path = tmp_path / "alembic.db"
    url = f"sqlite:///{db_path.as_posix()}"

    # Locate the bundled alembic env
    import bug_fab

    pkg_dir = Path(bug_fab.__file__).parent
    alembic_dir = pkg_dir / "storage" / "_alembic"
    assert alembic_dir.exists(), "expected bundled alembic folder"

    cfg = Config()
    cfg.set_main_option("script_location", str(alembic_dir))
    cfg.set_main_option("sqlalchemy.url", url)

    command.upgrade(cfg, "head")

    # After migration, the DB file exists and is non-empty
    assert db_path.exists()
    assert db_path.stat().st_size > 0


# -----------------------------------------------------------------------------
# Deprecated values on read
# -----------------------------------------------------------------------------


def test_get_report_accepts_deprecated_status_on_read(
    sqlite_storage: SQLiteStorage, tiny_png: bytes
) -> None:
    bid = _run(sqlite_storage.save_report(_baseline_metadata(), tiny_png))
    # Stuff a deprecated status directly into the row, bypassing the validator
    with sqlite_storage._session_factory() as session:
        session.begin()
        row = session.get(BugReport, bid)
        assert row is not None
        row.status = "resolved"
        session.commit()

    detail = _run(sqlite_storage.get_report(bid))
    assert detail is not None
    assert detail.status == "resolved"
