"""Abstract storage backend contract.

Every storage backend (file, SQLite, Postgres, future contrib backends)
implements `Storage`. The router layer talks only to this ABC so swapping
backends is a config change, not a code change.

Methods are defined `async` so backends that issue network or DB calls
can use native async drivers; in-memory or pure-disk backends simply
`async def` without awaiting anything internally.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from bug_fab.schemas import BugReportDetail, BugReportSummary


class Storage(ABC):
    """Backend-agnostic persistence contract for bug reports.

    Implementers MUST honor:
    - `save_report` returns the assigned id (string, format owned by the
      backend; the file backend uses `bug-NNN`).
    - List filters use plain string keys (`status`, `severity`, `module`,
      `report_type`, `search`); unknown keys are ignored, not rejected.
    - All mutation methods are idempotent for the "already in target state"
      case (e.g., archiving an already-archived report returns False).
    """

    @abstractmethod
    async def save_report(self, metadata: dict, screenshot_bytes: bytes) -> str:
        """Persist a new report and return its assigned id."""

    @abstractmethod
    async def get_report(self, report_id: str) -> BugReportDetail | None:
        """Return the full report or `None` if absent."""

    @abstractmethod
    async def list_reports(
        self, filters: dict, page: int, page_size: int
    ) -> tuple[list[BugReportSummary], int]:
        """Return `(items, total)` honoring filters and pagination."""

    @abstractmethod
    async def get_screenshot_path(self, report_id: str) -> Path | None:
        """Return the on-disk path to the report's screenshot, or `None`."""

    @abstractmethod
    async def update_status(
        self,
        report_id: str,
        status: str,
        fix_commit: str = "",
        fix_description: str = "",
        by: str = "",
    ) -> BugReportDetail | None:
        """Apply a status change, append a lifecycle entry, return the updated report."""

    @abstractmethod
    async def delete_report(self, report_id: str) -> bool:
        """Permanently remove the report and its screenshot. Returns True if found."""

    @abstractmethod
    async def archive_report(self, report_id: str) -> bool:
        """Soft-delete: move report files into the backend's archive area."""

    @abstractmethod
    async def bulk_close_fixed(self, by: str = "") -> int:
        """Transition every `fixed` report to `closed`. Returns the count."""

    @abstractmethod
    async def bulk_archive_closed(self) -> int:
        """Archive every `closed` report. Returns the count."""
