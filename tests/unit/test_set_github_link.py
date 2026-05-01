"""Unit tests for the ``Storage.set_github_link`` ABC method.

Exercises the same surface across both backends shipped in v0.1: the
file-backed store and the shared SQL implementation (via SQLite). The
Postgres backend uses the same ``SqlStorageBase._set_github_link_sync``
path, so an additional backend test there would only re-run the SQL
mixin against a different driver — covered separately by the Postgres
suite when its DSN env var is set.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from bug_fab.storage.files import FileStorage
from bug_fab.storage.sqlite import SQLiteStorage


def _run(coro):  # type: ignore[no-untyped-def]
    return asyncio.new_event_loop().run_until_complete(coro)


def _baseline_metadata(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": "Sample report",
        "report_type": "bug",
        "description": "desc",
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
        "module": "modA",
    }
    payload.update(overrides)
    return payload


_ISSUE_URL = "https://github.com/example/repo/issues/42"
_ISSUE_NUMBER = 42


# -----------------------------------------------------------------------------
# FileStorage
# -----------------------------------------------------------------------------


def test_file_set_github_link_persists_url_and_number(
    file_storage: FileStorage, tiny_png: bytes
) -> None:
    report_id = _run(file_storage.save_report(_baseline_metadata(), tiny_png))
    detail = _run(file_storage.set_github_link(report_id, _ISSUE_NUMBER, _ISSUE_URL))
    assert detail is not None
    assert detail.id == report_id
    assert detail.github_issue_url == _ISSUE_URL
    assert detail.github_issue_number == _ISSUE_NUMBER


def test_file_set_github_link_writes_through_to_disk(
    file_storage: FileStorage, tmp_storage_dir: Path, tiny_png: bytes
) -> None:
    report_id = _run(file_storage.save_report(_baseline_metadata(), tiny_png))
    _run(file_storage.set_github_link(report_id, _ISSUE_NUMBER, _ISSUE_URL))
    raw = json.loads((tmp_storage_dir / f"{report_id}.json").read_text(encoding="utf-8"))
    assert raw["github_issue_url"] == _ISSUE_URL
    assert raw["github_issue_number"] == _ISSUE_NUMBER


def test_file_set_github_link_updates_index_entry(
    file_storage: FileStorage, tmp_storage_dir: Path, tiny_png: bytes
) -> None:
    report_id = _run(file_storage.save_report(_baseline_metadata(), tiny_png))
    _run(file_storage.set_github_link(report_id, _ISSUE_NUMBER, _ISSUE_URL))
    index = json.loads((tmp_storage_dir / "index.json").read_text(encoding="utf-8"))
    entry = next(e for e in index["reports"] if e["id"] == report_id)
    assert entry["github_issue_url"] == _ISSUE_URL


def test_file_set_github_link_missing_id_returns_none(file_storage: FileStorage) -> None:
    assert _run(file_storage.set_github_link("bug-999", _ISSUE_NUMBER, _ISSUE_URL)) is None


def test_file_set_github_link_invalid_id_shape_returns_none(
    file_storage: FileStorage,
) -> None:
    # Path-traversal / malformed IDs are rejected up front, matching
    # update_status's contract.
    assert _run(file_storage.set_github_link("../etc/passwd", _ISSUE_NUMBER, _ISSUE_URL)) is None


def test_file_set_github_link_is_idempotent(file_storage: FileStorage, tiny_png: bytes) -> None:
    report_id = _run(file_storage.save_report(_baseline_metadata(), tiny_png))
    first = _run(file_storage.set_github_link(report_id, _ISSUE_NUMBER, _ISSUE_URL))
    second = _run(file_storage.set_github_link(report_id, _ISSUE_NUMBER, _ISSUE_URL))
    assert first is not None and second is not None
    assert first.github_issue_url == second.github_issue_url == _ISSUE_URL
    assert first.github_issue_number == second.github_issue_number == _ISSUE_NUMBER
    # Lifecycle log MUST NOT grow on idempotent re-calls — the link is
    # metadata, not a state transition.
    assert len(second.lifecycle) == len(first.lifecycle)


def test_file_set_github_link_overwrite_replaces_url(
    file_storage: FileStorage, tiny_png: bytes
) -> None:
    report_id = _run(file_storage.save_report(_baseline_metadata(), tiny_png))
    _run(file_storage.set_github_link(report_id, 1, "https://github.com/example/repo/issues/1"))
    detail = _run(
        file_storage.set_github_link(report_id, 2, "https://github.com/example/repo/issues/2")
    )
    assert detail is not None
    assert detail.github_issue_number == 2
    assert detail.github_issue_url == "https://github.com/example/repo/issues/2"


# -----------------------------------------------------------------------------
# SQLiteStorage (SqlStorageBase mixin)
# -----------------------------------------------------------------------------


def test_sqlite_set_github_link_persists_url_and_number(
    sqlite_storage: SQLiteStorage, tiny_png: bytes
) -> None:
    report_id = _run(sqlite_storage.save_report(_baseline_metadata(), tiny_png))
    detail = _run(sqlite_storage.set_github_link(report_id, _ISSUE_NUMBER, _ISSUE_URL))
    assert detail is not None
    assert detail.id == report_id
    assert detail.github_issue_url == _ISSUE_URL
    assert detail.github_issue_number == _ISSUE_NUMBER


def test_sqlite_set_github_link_round_trip_via_get(
    sqlite_storage: SQLiteStorage, tiny_png: bytes
) -> None:
    report_id = _run(sqlite_storage.save_report(_baseline_metadata(), tiny_png))
    _run(sqlite_storage.set_github_link(report_id, _ISSUE_NUMBER, _ISSUE_URL))
    fetched = _run(sqlite_storage.get_report(report_id))
    assert fetched is not None
    assert fetched.github_issue_url == _ISSUE_URL
    assert fetched.github_issue_number == _ISSUE_NUMBER


def test_sqlite_set_github_link_missing_id_returns_none(
    sqlite_storage: SQLiteStorage,
) -> None:
    assert _run(sqlite_storage.set_github_link("bug-999", _ISSUE_NUMBER, _ISSUE_URL)) is None


def test_sqlite_set_github_link_invalid_id_returns_none(
    sqlite_storage: SQLiteStorage,
) -> None:
    assert _run(sqlite_storage.set_github_link("not-a-valid-id", _ISSUE_NUMBER, _ISSUE_URL)) is None


def test_sqlite_set_github_link_is_idempotent(
    sqlite_storage: SQLiteStorage, tiny_png: bytes
) -> None:
    report_id = _run(sqlite_storage.save_report(_baseline_metadata(), tiny_png))
    first = _run(sqlite_storage.set_github_link(report_id, _ISSUE_NUMBER, _ISSUE_URL))
    second = _run(sqlite_storage.set_github_link(report_id, _ISSUE_NUMBER, _ISSUE_URL))
    assert first is not None and second is not None
    assert first.github_issue_url == second.github_issue_url == _ISSUE_URL
    # Lifecycle entries reflect creation only — no extra lifecycle row per
    # link write, since the link is metadata not a state transition.
    assert len(second.lifecycle) == len(first.lifecycle)


def test_sqlite_set_github_link_overwrite_replaces_url(
    sqlite_storage: SQLiteStorage, tiny_png: bytes
) -> None:
    report_id = _run(sqlite_storage.save_report(_baseline_metadata(), tiny_png))
    _run(sqlite_storage.set_github_link(report_id, 1, "https://github.com/example/repo/issues/1"))
    detail = _run(
        sqlite_storage.set_github_link(report_id, 2, "https://github.com/example/repo/issues/2")
    )
    assert detail is not None
    assert detail.github_issue_number == 2
    assert detail.github_issue_url == "https://github.com/example/repo/issues/2"
