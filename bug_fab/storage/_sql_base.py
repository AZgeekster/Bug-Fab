"""Shared SQL implementation of the ``Storage`` ABC.

Both ``SQLiteStorage`` and ``PostgresStorage`` inherit from
``SqlStorageBase``. The dialect-specific behaviors — the ID generator strategy
and the bind URL construction — are pushed into the subclasses; everything
else lives here.

All public methods are ``async`` to match the ``Storage`` ABC. The synchronous
SQLAlchemy session work runs inside ``asyncio.to_thread`` so the event loop
isn't blocked on blocking driver calls. v0.2 may switch to native async
SQLAlchemy; the ABC contract stays the same.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar

from sqlalchemy import Engine, func, select, text, update
from sqlalchemy.orm import Session

from bug_fab.schemas import (
    BugReportContext,
    BugReportDetail,
    BugReportSummary,
    LifecycleEvent,
    Severity,
    Status,
)
from bug_fab.storage.base import Storage

from ._engine import make_session_factory
from ._models import (
    ALLOWED_SEVERITIES,
    ALLOWED_STATUSES,
    Base,
    BugReport,
    BugReportIdCounter,
    BugReportLifecycle,
)

logger = logging.getLogger(__name__)

_REPORT_ID_RE = re.compile(r"^bug-[A-Za-z]?\d{3,}$")
_ARCHIVE_SUBDIR = "archive"


class StorageError(Exception):
    """Raised on validation or invariant failures inside the SQL backends."""


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _utcnow_iso() -> str:
    """ISO-8601 UTC string used for lifecycle ``at`` and timestamp fields."""
    return _utcnow().isoformat()


def _validate_severity_for_write(severity: str | None) -> None:
    if severity is None or severity == "":
        return
    if severity not in ALLOWED_SEVERITIES:
        raise StorageError(f"invalid severity {severity!r}; expected one of {ALLOWED_SEVERITIES}")


def _validate_status_for_write(status: str) -> None:
    if status not in ALLOWED_STATUSES:
        raise StorageError(f"invalid status {status!r}; expected one of {ALLOWED_STATUSES}")


def _id_prefix() -> str:
    """Resolve the optional ``BUG_FAB_ID_PREFIX`` env var.

    Empty / unset means the default ``bug-NNN`` format. A non-empty value
    yields ``bug-{prefix}NNN`` (e.g., ``bug-P038``).
    """
    return os.environ.get("BUG_FAB_ID_PREFIX", "")


def _format_id(n: int) -> str:
    """Format ``n`` as ``bug-NNN`` (or ``bug-{prefix}NNN`` if env set).

    Zero-padded to 3 digits to keep the common case sortable; longer values
    extend naturally past 999 without padding (``bug-1000``).
    """
    prefix = _id_prefix()
    return f"bug-{prefix}{n:03d}"


class SqlStorageBase(Storage):
    """Base implementation of ``Storage`` for SQL backends.

    Subclasses provide the engine, the screenshot directory, and the
    dialect-specific next-id strategy. Everything else — CRUD, lifecycle
    audit, bulk operations, status workflow — lives here.
    """

    #: Whether the dialect supports ``RETURNING`` from ``INSERT`` /
    #: ``UPDATE`` clauses. Postgres yes; SQLite >= 3.35 yes; we keep it
    #: portable by sticking to ORM patterns and avoiding ``RETURNING`` in
    #: hand-rolled SQL.
    supports_returning: ClassVar[bool] = False

    def __init__(self, engine: Engine, screenshot_dir: Path) -> None:
        self.engine = engine
        self.screenshot_dir = Path(screenshot_dir)
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir = self.screenshot_dir / _ARCHIVE_SUBDIR
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self._session_factory = make_session_factory(engine)

    # ---- schema lifecycle helpers ---------------------------------------

    def create_all(self) -> None:
        """Create every table from the declarative metadata.

        Convenience for tests and the file-default-to-SQL upgrade path. In
        production, consumers should run Alembic instead so schema versions
        track in ``alembic_version``.
        """
        Base.metadata.create_all(self.engine)

    # ---- next-id strategy (overridden per dialect) ----------------------

    def _next_id_value(self, session: Session) -> int:
        """Return the next sequential integer for ID generation.

        Default implementation uses the ``bug_report_id_counter`` row-counter
        table (works on every dialect). Postgres overrides this to use a
        SEQUENCE for better concurrency.
        """
        result = session.execute(
            text("UPDATE bug_report_id_counter SET last_value = last_value + 1 WHERE id = 1")
        )
        if result.rowcount == 0:
            session.add(BugReportIdCounter(id=1, last_value=1))
            session.flush()
            return 1
        next_value = session.execute(
            select(BugReportIdCounter.last_value).where(BugReportIdCounter.id == 1)
        ).scalar_one()
        return int(next_value)

    # ---- screenshot helpers ---------------------------------------------

    def _live_screenshot_path(self, report_id: str) -> Path:
        return self.screenshot_dir / f"{report_id}.png"

    def _archived_screenshot_path(self, report_id: str) -> Path:
        return self.archive_dir / f"{report_id}.png"

    def _write_screenshot(self, report_id: str, screenshot_bytes: bytes) -> Path:
        """Atomically write the screenshot file for ``report_id``.

        Uses a ``.tmp`` sibling + ``replace`` so a crash mid-write doesn't
        leave a half-written PNG behind.
        """
        path = self._live_screenshot_path(report_id)
        tmp_path = path.with_suffix(".png.tmp")
        tmp_path.write_bytes(screenshot_bytes)
        tmp_path.replace(path)
        return path

    # ---- core CRUD (Storage ABC) ----------------------------------------

    async def save_report(self, metadata: dict, screenshot_bytes: bytes) -> str:
        """Persist a bug report and return the assigned ID.

        Inserts a row, writes the screenshot to disk, appends a ``created``
        lifecycle entry, and returns the new ``bug-NNN`` ID. The DB insert
        and screenshot write share one transaction; if either fails, both
        roll back.
        """
        return await asyncio.to_thread(self._save_report_sync, metadata, screenshot_bytes)

    async def get_report(self, report_id: str) -> BugReportDetail | None:
        """Return the full report record for ``report_id``, or ``None``."""
        if not _REPORT_ID_RE.match(report_id or ""):
            return None
        return await asyncio.to_thread(self._get_report_sync, report_id)

    async def list_reports(
        self, filters: dict, page: int, page_size: int
    ) -> tuple[list[BugReportSummary], int]:
        """Return ``(items, total)`` for the requested page and filters."""
        return await asyncio.to_thread(self._list_reports_sync, filters, page, page_size)

    async def get_screenshot_path(self, report_id: str) -> Path | None:
        """Return the on-disk screenshot path, falling back to archive."""
        if not _REPORT_ID_RE.match(report_id or ""):
            return None
        return await asyncio.to_thread(self._get_screenshot_path_sync, report_id)

    async def update_status(
        self,
        report_id: str,
        status: str,
        fix_commit: str = "",
        fix_description: str = "",
        by: str = "",
    ) -> BugReportDetail | None:
        """Apply a status transition + lifecycle entry, return the updated report."""
        if not _REPORT_ID_RE.match(report_id or ""):
            return None
        _validate_status_for_write(status)
        return await asyncio.to_thread(
            self._update_status_sync,
            report_id,
            status,
            fix_commit,
            fix_description,
            by,
        )

    async def delete_report(self, report_id: str) -> bool:
        """Permanently delete the report and its screenshot."""
        if not _REPORT_ID_RE.match(report_id or ""):
            return False
        return await asyncio.to_thread(self._delete_report_sync, report_id)

    async def archive_report(self, report_id: str) -> bool:
        """Soft-delete: stamp ``archived_at`` and move the screenshot file."""
        if not _REPORT_ID_RE.match(report_id or ""):
            return False
        return await asyncio.to_thread(self._archive_report_sync, report_id)

    async def bulk_close_fixed(self, by: str = "") -> int:
        """Transition every ``fixed`` report to ``closed``."""
        return await asyncio.to_thread(self._bulk_close_fixed_sync, by)

    async def bulk_archive_closed(self) -> int:
        """Archive every ``closed`` report not already archived."""
        return await asyncio.to_thread(self._bulk_archive_closed_sync)

    # ---- sync implementations -------------------------------------------

    def _save_report_sync(self, metadata: dict, screenshot_bytes: bytes) -> str:
        # Validate enums BEFORE allocating an ID, so a bad payload does not
        # burn a sequence value.
        severity = metadata.get("severity") or Severity.MEDIUM.value
        _validate_severity_for_write(severity)

        protocol_version = metadata.get("protocol_version", "0.1")

        title = metadata.get("title", "")
        if not title:
            raise StorageError("metadata.title is required")

        context = dict(metadata.get("context") or {})

        report_id: str | None = None
        with self._session_factory() as session:
            session.begin()
            try:
                next_int = self._next_id_value(session)
                report_id = _format_id(next_int)

                screenshot_path = self._write_screenshot(report_id, screenshot_bytes)

                received_at = _utcnow()
                report = BugReport(
                    id=report_id,
                    received_at=received_at,
                    protocol_version=protocol_version,
                    title=title,
                    description=metadata.get("description", "") or "",
                    severity=severity,
                    status=Status.OPEN.value,
                    environment=(
                        metadata.get("environment") or context.get("environment", "") or None
                    ),
                    app_name=(metadata.get("app_name") or context.get("app_name") or None),
                    app_version=(metadata.get("app_version") or context.get("app_version") or None),
                    reporter=(metadata.get("submitted_by") or _extract_reporter(metadata)),
                    page_url=(metadata.get("page_url") or context.get("url") or None),
                    module=(metadata.get("module") or context.get("module") or None),
                    user_agent_server=metadata.get("server_user_agent") or None,
                    user_agent_client=(
                        metadata.get("client_reported_user_agent")
                        or context.get("user_agent")
                        or None
                    ),
                    metadata_json=json.dumps(metadata, default=str, ensure_ascii=False),
                    screenshot_path=str(screenshot_path),
                )
                session.add(report)

                session.add(
                    BugReportLifecycle(
                        bug_report_id=report_id,
                        action="created",
                        by=metadata.get("submitted_by", "") or "",
                        at=received_at,
                        metadata_json=None,
                    )
                )

                session.commit()
                return report_id
            except Exception:
                session.rollback()
                # Best-effort cleanup of the screenshot we may have written.
                if report_id is not None:
                    with contextlib.suppress(OSError):  # pragma: no cover - defensive cleanup
                        self._live_screenshot_path(report_id).unlink(missing_ok=True)
                raise

    def _get_report_sync(self, report_id: str) -> BugReportDetail | None:
        with self._session_factory() as session:
            report = session.get(BugReport, report_id)
            if report is None:
                return None
            return _to_detail(report)

    def _list_reports_sync(
        self, filters: dict, page: int, page_size: int
    ) -> tuple[list[BugReportSummary], int]:
        page = max(1, int(page or 1))
        page_size = max(1, int(page_size or 20))
        offset = (page - 1) * page_size

        with self._session_factory() as session:
            base_stmt = select(BugReport).where(BugReport.archived_at.is_(None))
            count_stmt = (
                select(func.count()).select_from(BugReport).where(BugReport.archived_at.is_(None))
            )

            for key in ("status", "severity", "module"):
                wanted = (filters or {}).get(key)
                if wanted:
                    column = getattr(BugReport, key)
                    base_stmt = base_stmt.where(column == wanted)
                    count_stmt = count_stmt.where(column == wanted)

            search = (filters or {}).get("search")
            if search:
                needle = f"%{str(search).lower()}%"
                # Search across title + module + id, case-insensitively.
                base_stmt = base_stmt.where(
                    func.lower(BugReport.title).like(needle)
                    | func.lower(func.coalesce(BugReport.module, "")).like(needle)
                    | func.lower(BugReport.id).like(needle)
                )
                count_stmt = count_stmt.where(
                    func.lower(BugReport.title).like(needle)
                    | func.lower(func.coalesce(BugReport.module, "")).like(needle)
                    | func.lower(BugReport.id).like(needle)
                )

            # Note: ``report_type`` is not stored as a typed column in v0.1 —
            # it lives in ``metadata_json``. Filtering on it would force a
            # full scan + JSON parse, so we silently ignore the filter at the
            # SQL layer (matches FileStorage tolerance).

            total = int(session.execute(count_stmt).scalar_one())

            rows = list(
                session.scalars(
                    base_stmt.order_by(BugReport.received_at.desc()).offset(offset).limit(page_size)
                ).all()
            )
            summaries = [_to_summary(row) for row in rows]
            return summaries, total

    def _get_screenshot_path_sync(self, report_id: str) -> Path | None:
        live = self._live_screenshot_path(report_id)
        if live.exists():
            return live
        archived = self._archived_screenshot_path(report_id)
        if archived.exists():
            return archived
        # Fallback: the row may carry a custom path (e.g., legacy import).
        with self._session_factory() as session:
            report = session.get(BugReport, report_id)
            if report is None:
                return None
            stored = Path(report.screenshot_path)
            return stored if stored.exists() else None

    def _update_status_sync(
        self,
        report_id: str,
        status: str,
        fix_commit: str,
        fix_description: str,
        by: str,
    ) -> BugReportDetail | None:
        with self._session_factory() as session:
            session.begin()
            try:
                report = session.get(BugReport, report_id)
                if report is None:
                    session.rollback()
                    return None

                report.status = status
                session.add(
                    BugReportLifecycle(
                        bug_report_id=report_id,
                        action="status_changed",
                        by=by or "",
                        at=_utcnow(),
                        fix_commit=fix_commit or None,
                        fix_description=fix_description or None,
                        metadata_json=json.dumps({"status": status}),
                    )
                )
                session.commit()
                # Re-fetch so the relationship reflects the new lifecycle row.
                refreshed = session.get(BugReport, report_id)
                return _to_detail(refreshed) if refreshed is not None else None
            except Exception:
                session.rollback()
                raise

    def _delete_report_sync(self, report_id: str) -> bool:
        with self._session_factory() as session:
            session.begin()
            report = session.get(BugReport, report_id)
            if report is None:
                session.rollback()
                return False
            stored_path = Path(report.screenshot_path)
            session.delete(report)
            session.commit()

        # Best-effort cleanup of every candidate path (live + archive + the
        # explicitly-stored path in case it differs from convention).
        for path in (
            self._live_screenshot_path(report_id),
            self._archived_screenshot_path(report_id),
            stored_path,
        ):
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:  # pragma: no cover - filesystem edge cases
                logger.warning("failed to unlink screenshot %s: %s", path, exc)
        return True

    def _archive_report_sync(self, report_id: str) -> bool:
        with self._session_factory() as session:
            session.begin()
            try:
                report = session.get(BugReport, report_id)
                if report is None:
                    session.rollback()
                    return False
                if report.archived_at is not None:
                    # Idempotent: already archived → no-op success.
                    session.commit()
                    return False

                now = _utcnow()
                report.archived_at = now
                session.add(
                    BugReportLifecycle(
                        bug_report_id=report_id,
                        action="archived",
                        by="",
                        at=now,
                        metadata_json=None,
                    )
                )
                session.commit()
            except Exception:
                session.rollback()
                raise

        # Move the screenshot file to the archive subdir for layout symmetry
        # with FileStorage. If the source is missing (e.g., already moved),
        # silently continue — the DB row is the source of truth.
        live = self._live_screenshot_path(report_id)
        archived = self._archived_screenshot_path(report_id)
        if live.exists():
            try:
                shutil.move(str(live), str(archived))
            except OSError as exc:  # pragma: no cover - filesystem edge cases
                logger.warning("failed to move screenshot %s -> %s: %s", live, archived, exc)
        return True

    def _bulk_close_fixed_sync(self, by: str) -> int:
        now = _utcnow()
        with self._session_factory() as session:
            session.begin()
            try:
                target_ids = list(
                    session.scalars(
                        select(BugReport.id).where(BugReport.status == Status.FIXED.value)
                    ).all()
                )
                if not target_ids:
                    session.commit()
                    return 0

                session.execute(
                    update(BugReport)
                    .where(BugReport.id.in_(target_ids))
                    .values(status=Status.CLOSED.value)
                )
                session.add_all(
                    [
                        BugReportLifecycle(
                            bug_report_id=report_id,
                            action="status_changed",
                            by=by or "",
                            at=now,
                            metadata_json=json.dumps(
                                {"status": Status.CLOSED.value, "via": "bulk_close_fixed"}
                            ),
                        )
                        for report_id in target_ids
                    ]
                )
                session.commit()
                return len(target_ids)
            except Exception:
                session.rollback()
                raise

    def _bulk_archive_closed_sync(self) -> int:
        now = _utcnow()
        with self._session_factory() as session:
            session.begin()
            try:
                target_ids = list(
                    session.scalars(
                        select(BugReport.id).where(
                            (BugReport.status == Status.CLOSED.value)
                            & (BugReport.archived_at.is_(None))
                        )
                    ).all()
                )
                if not target_ids:
                    session.commit()
                    return 0

                session.execute(
                    update(BugReport).where(BugReport.id.in_(target_ids)).values(archived_at=now)
                )
                session.add_all(
                    [
                        BugReportLifecycle(
                            bug_report_id=report_id,
                            action="archived",
                            by="",
                            at=now,
                            metadata_json=None,
                        )
                        for report_id in target_ids
                    ]
                )
                session.commit()
            except Exception:
                session.rollback()
                raise

        # Move screenshot files to the archive subdir to mirror FileStorage.
        for report_id in target_ids:
            live = self._live_screenshot_path(report_id)
            if live.exists():
                try:
                    shutil.move(str(live), str(self._archived_screenshot_path(report_id)))
                except OSError as exc:  # pragma: no cover
                    logger.warning("failed to archive screenshot %s: %s", live, exc)
        return len(target_ids)


# --------------------------- helpers ------------------------------------


def _extract_reporter(metadata: dict) -> str | None:
    """Pull a printable reporter identifier out of the metadata payload.

    The wire protocol carries reporter as ``{name?, email?, user_id?}``.
    The denormalized ``reporter`` column stores whichever the consumer
    populated, in priority order email > user_id > name. The full reporter
    object is preserved verbatim in ``metadata_json``.
    """
    reporter = metadata.get("reporter")
    if not isinstance(reporter, dict):
        return None
    for key in ("email", "user_id", "name"):
        value = reporter.get(key)
        if value:
            return str(value)
    return None


def _iso(dt: datetime | None) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _to_summary(row: BugReport) -> BugReportSummary:
    """Project a ``BugReport`` row into the ``BugReportSummary`` schema."""
    metadata = _safe_metadata(row.metadata_json)
    return BugReportSummary.model_validate(
        {
            "id": row.id,
            "title": row.title,
            "report_type": metadata.get("report_type", "bug"),
            "severity": row.severity or Severity.MEDIUM.value,
            "status": row.status or Status.OPEN.value,
            "module": row.module or "",
            "created_at": _iso(row.received_at),
            "has_screenshot": bool(row.screenshot_path),
            "github_issue_url": row.github_issue_url,
        }
    )


def _to_detail(row: BugReport) -> BugReportDetail:
    """Project a ``BugReport`` row + lifecycle into ``BugReportDetail``."""
    metadata = _safe_metadata(row.metadata_json)
    context_data = dict(metadata.get("context") or {})

    reporter_data = metadata.get("reporter") or {}
    if not isinstance(reporter_data, dict):
        reporter_data = {}
    payload = {
        "id": row.id,
        "title": row.title,
        "report_type": metadata.get("report_type", "bug"),
        "severity": row.severity or Severity.MEDIUM.value,
        "status": row.status or Status.OPEN.value,
        "module": row.module or "",
        "created_at": _iso(row.received_at),
        "has_screenshot": bool(row.screenshot_path),
        "github_issue_url": row.github_issue_url,
        "github_issue_number": row.github_issue_number,
        "description": row.description or "",
        "expected_behavior": metadata.get("expected_behavior", ""),
        "tags": list(metadata.get("tags") or []),
        "reporter": reporter_data,
        "client_ts": metadata.get("client_ts", ""),
        "protocol_version": row.protocol_version or "0.1",
        "context": BugReportContext.model_validate(context_data),
        "lifecycle": [
            LifecycleEvent.model_validate(
                {
                    "action": entry.action,
                    "by": entry.by or "",
                    "at": _iso(entry.at),
                    "fix_commit": entry.fix_commit or "",
                    "fix_description": entry.fix_description or "",
                }
            )
            for entry in row.lifecycle
        ],
        "server_user_agent": row.user_agent_server or "",
        "client_reported_user_agent": row.user_agent_client or "",
        "environment": row.environment or "",
        "updated_at": _iso(_latest_lifecycle_at(row) or row.received_at),
    }
    return BugReportDetail.model_validate(payload)


def _safe_metadata(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
        return loaded if isinstance(loaded, dict) else {}
    except json.JSONDecodeError:
        return {}


def _latest_lifecycle_at(row: BugReport) -> datetime | None:
    if not row.lifecycle:
        return None
    return max((entry.at for entry in row.lifecycle if entry.at is not None), default=None)
