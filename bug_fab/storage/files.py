"""File-backed storage backend (zero external dependencies).

Layout under `storage_dir`:

    <storage_dir>/
    ├── index.json              denormalized listing for fast filter/page
    ├── bug-001.json            full report payload
    ├── bug-001.png             screenshot
    └── archive/
        ├── bug-002.json        archived report
        └── bug-002.png

Atomicity uses tmp+os.replace for both the index and per-report JSON
(audit finding B3 — the prior implementation wrote in place and could
corrupt the index on crash).

Concurrency is coordinated by a per-instance `asyncio.Lock`. This is
process-local — multi-worker uvicorn deployments must use a SQL backend
or an external lock; see the class docstring.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bug_fab.schemas import (
    BugReportContext,
    BugReportDetail,
    BugReportSummary,
    LifecycleEvent,
    Severity,
    Status,
)
from bug_fab.storage.base import Storage

_REPORT_ID_RE = re.compile(r"^bug-[A-Za-z]?\d{3,}$")
_INDEX_FILENAME = "index.json"
_ARCHIVE_SUBDIR = "archive"


def _now_iso() -> str:
    """UTC timestamp in ISO-8601 with timezone — server clock is authoritative."""
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_text(path: Path, payload: str) -> None:
    """Write `payload` to `path` via tmp+rename so partial writes never publish."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    """Binary equivalent of `_atomic_write_text` for screenshot blobs."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(payload)
    tmp.replace(path)


class FileStorage(Storage):
    """JSON-on-disk implementation of the `Storage` ABC.

    Two correctness notes for operators:

    1. Multi-worker caveat — `asyncio.Lock` only coordinates coroutines
       in the same process. If you run uvicorn with `--workers 2+`,
       two workers can race on `index.json` and lose the loser's write.
       Use `SQLiteStorage` or `PostgresStorage` for multi-worker setups.

    2. ID prefix — when `id_prefix` is empty (default), assigned ids look
       like `bug-001`. When set (e.g., `id_prefix="P"`), ids become
       `bug-P001`. The prefix is taken once at construction; changing it
       between runs over the same storage dir mixes formats but does not
       collide because the numeric counter is shared.
    """

    def __init__(self, storage_dir: Path | str, id_prefix: str = "") -> None:
        self.storage_dir = Path(storage_dir)
        self.id_prefix = id_prefix
        self.archive_dir = self.storage_dir / _ARCHIVE_SUBDIR
        self._index_path = self.storage_dir / _INDEX_FILENAME
        self._lock = asyncio.Lock()
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)

    async def save_report(self, metadata: dict, screenshot_bytes: bytes) -> str:
        """Assign an id, write screenshot + JSON atomically, append to index."""
        async with self._lock:
            index = self._read_index()
            report_id = self._next_id(index)
            now = _now_iso()
            report = self._build_report(report_id, metadata, now)
            self._write_screenshot(report_id, screenshot_bytes)
            self._write_report(report_id, report)
            index_entry = self._build_index_entry(report)
            index["reports"].append(index_entry)
            index["next_number"] = index.get("next_number", 1) + 1
            self._write_index(index)
            return report_id

    async def get_report(self, report_id: str) -> BugReportDetail | None:
        """Read one report's full payload from disk."""
        if not _REPORT_ID_RE.match(report_id):
            return None
        async with self._lock:
            data = self._read_report(report_id)
            if data is None:
                return None
            return self._coerce_detail(data)

    async def list_reports(
        self, filters: dict, page: int, page_size: int
    ) -> tuple[list[BugReportSummary], int]:
        """Filter the in-memory index and return a page of summaries."""
        async with self._lock:
            index = self._read_index()
            entries = list(index.get("reports", []))
        matched = [e for e in entries if self._matches_filters(e, filters)]
        matched.sort(key=lambda e: e.get("created_at", ""), reverse=True)
        total = len(matched)
        start = max(0, (page - 1) * page_size)
        end = start + page_size
        page_items = [self._coerce_summary(e) for e in matched[start:end]]
        return page_items, total

    async def get_screenshot_path(self, report_id: str) -> Path | None:
        """Return the on-disk path to the screenshot, or `None` if missing."""
        if not _REPORT_ID_RE.match(report_id):
            return None
        candidate = self.storage_dir / f"{report_id}.png"
        if candidate.exists():
            return candidate
        archived = self.archive_dir / f"{report_id}.png"
        if archived.exists():
            return archived
        return None

    async def update_status(
        self,
        report_id: str,
        status: str,
        fix_commit: str = "",
        fix_description: str = "",
        by: str = "",
    ) -> BugReportDetail | None:
        """Mutate the report's status, append a lifecycle entry, persist."""
        if not _REPORT_ID_RE.match(report_id):
            return None
        async with self._lock:
            data = self._read_report(report_id)
            if data is None:
                return None
            data["status"] = status
            data["updated_at"] = _now_iso()
            event = {
                "action": "status_changed",
                "by": by,
                "at": data["updated_at"],
                "fix_commit": fix_commit,
                "fix_description": fix_description,
            }
            data.setdefault("lifecycle", []).append(event)
            self._write_report(report_id, data)
            self._update_index_entry(report_id, status=status)
            return self._coerce_detail(data)

    async def set_github_link(
        self,
        report_id: str,
        issue_number: int,
        issue_url: str,
    ) -> BugReportDetail | None:
        """Stamp the GitHub issue link onto the report's JSON + index entry."""
        if not _REPORT_ID_RE.match(report_id):
            return None
        async with self._lock:
            data = self._read_report(report_id)
            if data is None:
                return None
            data["github_issue_number"] = issue_number
            data["github_issue_url"] = issue_url
            self._write_report(report_id, data)
            self._update_index_entry(report_id, github_issue_url=issue_url)
            return self._coerce_detail(data)

    async def delete_report(self, report_id: str) -> bool:
        """Hard-delete: remove JSON, PNG, and index entry."""
        if not _REPORT_ID_RE.match(report_id):
            return False
        async with self._lock:
            removed = False
            for path in self._candidate_paths(report_id):
                if path.exists():
                    path.unlink()
                    removed = True
            if removed:
                index = self._read_index()
                index["reports"] = [e for e in index.get("reports", []) if e.get("id") != report_id]
                self._write_index(index)
            return removed

    async def archive_report(self, report_id: str) -> bool:
        """Move report JSON+PNG to `archive/`, drop from the index."""
        if not _REPORT_ID_RE.match(report_id):
            return False
        async with self._lock:
            return self._archive_one(report_id)

    async def bulk_close_fixed(self, by: str = "") -> int:
        """Transition every `fixed` report to `closed` in one pass."""
        async with self._lock:
            index = self._read_index()
            ids = [e.get("id") for e in index.get("reports", []) if e.get("status") == "fixed"]
        closed = 0
        for report_id in ids:
            updated = await self.update_status(report_id, status=Status.CLOSED.value, by=by)
            if updated is not None:
                closed += 1
        return closed

    async def bulk_archive_closed(self) -> int:
        """Archive every `closed` report. Skips reports archived since list-time."""
        async with self._lock:
            index = self._read_index()
            ids = [e.get("id") for e in index.get("reports", []) if e.get("status") == "closed"]
            archived = 0
            for report_id in ids:
                if self._archive_one(report_id):
                    archived += 1
            return archived

    def _next_id(self, index: dict) -> str:
        """Format the next sequential id, optionally with the configured prefix."""
        n = int(index.get("next_number", 1))
        return f"bug-{self.id_prefix}{n:03d}"

    def _build_report(self, report_id: str, metadata: dict, now: str) -> dict:
        """Assemble the on-disk report dict from the validated wire payload."""
        context = dict(metadata.get("context") or {})
        reporter = dict(metadata.get("reporter") or {})
        report = {
            "id": report_id,
            "protocol_version": metadata.get("protocol_version", "0.1"),
            "title": metadata.get("title", ""),
            "client_ts": metadata.get("client_ts", ""),
            "report_type": metadata.get("report_type", "bug"),
            "description": metadata.get("description", ""),
            "expected_behavior": metadata.get("expected_behavior", ""),
            "severity": metadata.get("severity", Severity.MEDIUM.value),
            "status": Status.OPEN.value,
            "tags": list(metadata.get("tags") or []),
            "reporter": {
                "name": reporter.get("name", ""),
                "email": reporter.get("email", ""),
                "user_id": reporter.get("user_id", ""),
            },
            "context": context,
            "module": metadata.get("module") or context.get("module") or "",
            "created_at": now,
            "updated_at": now,
            "has_screenshot": True,
            "server_user_agent": metadata.get("server_user_agent", ""),
            "client_reported_user_agent": context.get("user_agent", ""),
            "environment": metadata.get("environment") or context.get("environment", ""),
            "github_issue_url": None,
            "github_issue_number": None,
            "lifecycle": [
                {
                    "action": "created",
                    "by": metadata.get("submitted_by", ""),
                    "at": now,
                    "fix_commit": "",
                    "fix_description": "",
                }
            ],
        }
        return report

    def _build_index_entry(self, report: dict) -> dict:
        """Denormalize the fields needed for fast list/filter into the index."""
        return {
            "id": report["id"],
            "title": report.get("title", ""),
            "report_type": report.get("report_type", "bug"),
            "severity": report.get("severity", Severity.MEDIUM.value),
            "status": report.get("status", Status.OPEN.value),
            "module": report.get("module", ""),
            "created_at": report.get("created_at", ""),
            "has_screenshot": report.get("has_screenshot", True),
            "github_issue_url": report.get("github_issue_url"),
        }

    def _matches_filters(self, entry: dict, filters: dict) -> bool:
        """Return True if every non-empty filter matches the entry."""
        for key in ("status", "severity", "module", "report_type"):
            wanted = filters.get(key)
            if wanted and entry.get(key) != wanted:
                return False
        search = filters.get("search")
        if search:
            needle = str(search).lower()
            haystack = " ".join(
                str(entry.get(field, "")).lower() for field in ("title", "module", "id")
            )
            if needle not in haystack:
                return False
        return True

    def _coerce_summary(self, entry: dict) -> BugReportSummary:
        """Coerce a raw index entry into the summary schema (read-tolerant)."""
        return BugReportSummary.model_validate(entry)

    def _coerce_detail(self, data: dict) -> BugReportDetail:
        """Coerce a raw report dict into the detail schema (read-tolerant)."""
        context_raw = dict(data.get("context") or {})
        payload = dict(data)
        payload["context"] = BugReportContext.model_validate(context_raw)
        payload["lifecycle"] = [
            LifecycleEvent.model_validate(event) for event in data.get("lifecycle", [])
        ]
        return BugReportDetail.model_validate(payload)

    def _read_index(self) -> dict[str, Any]:
        """Read `index.json`, returning a fresh empty index on missing/corrupt."""
        if not self._index_path.exists():
            return {"reports": [], "next_number": 1}
        try:
            data = json.loads(self._index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"reports": [], "next_number": 1}
        data.setdefault("reports", [])
        data.setdefault("next_number", len(data["reports"]) + 1)
        return data

    def _write_index(self, index: dict[str, Any]) -> None:
        """Atomically write the index — tmp+rename guards against torn writes."""
        _atomic_write_text(self._index_path, json.dumps(index, indent=2, ensure_ascii=False))

    def _read_report(self, report_id: str) -> dict | None:
        """Load one report JSON; falls back to the archive subdir if needed."""
        primary = self.storage_dir / f"{report_id}.json"
        if primary.exists():
            return json.loads(primary.read_text(encoding="utf-8"))
        archived = self.archive_dir / f"{report_id}.json"
        if archived.exists():
            return json.loads(archived.read_text(encoding="utf-8"))
        return None

    def _write_report(self, report_id: str, data: dict) -> None:
        """Atomically persist a single report's JSON payload."""
        path = self.storage_dir / f"{report_id}.json"
        if not path.exists():
            archived = self.archive_dir / f"{report_id}.json"
            if archived.exists():
                path = archived
        _atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False))

    def _write_screenshot(self, report_id: str, screenshot_bytes: bytes) -> None:
        """Atomically write the screenshot PNG."""
        path = self.storage_dir / f"{report_id}.png"
        _atomic_write_bytes(path, screenshot_bytes)

    def _update_index_entry(self, report_id: str, **fields: Any) -> None:
        """Mutate matching fields on a report's index entry and persist."""
        index = self._read_index()
        for entry in index.get("reports", []):
            if entry.get("id") == report_id:
                entry.update(fields)
                break
        self._write_index(index)

    def _candidate_paths(self, report_id: str) -> list[Path]:
        """All on-disk paths that may belong to a report (live + archived)."""
        return [
            self.storage_dir / f"{report_id}.json",
            self.storage_dir / f"{report_id}.png",
            self.archive_dir / f"{report_id}.json",
            self.archive_dir / f"{report_id}.png",
        ]

    def _archive_one(self, report_id: str) -> bool:
        """Move a single report's files into `archive/` and drop the index row."""
        json_src = self.storage_dir / f"{report_id}.json"
        png_src = self.storage_dir / f"{report_id}.png"
        if not json_src.exists() and not png_src.exists():
            return False
        if json_src.exists():
            shutil.move(str(json_src), str(self.archive_dir / f"{report_id}.json"))
        if png_src.exists():
            shutil.move(str(png_src), str(self.archive_dir / f"{report_id}.png"))
        index = self._read_index()
        index["reports"] = [e for e in index.get("reports", []) if e.get("id") != report_id]
        self._write_index(index)
        return True
