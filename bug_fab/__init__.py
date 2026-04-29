"""Bug-Fab — framework-agnostic in-app bug reporter.

Public surface re-exports the core schema, storage types, and routers so
consumers write `from bug_fab import FileStorage, submit_router` without
reaching into submodules.
"""

from __future__ import annotations

from bug_fab.routers import submit_router, viewer_router
from bug_fab.schemas import (
    BugReportContext,
    BugReportCreate,
    BugReportDetail,
    BugReportListResponse,
    BugReportStatusUpdate,
    BugReportSummary,
    LifecycleEvent,
    Severity,
    Status,
)
from bug_fab.storage import FileStorage, Storage

__version__ = "0.1.0a1"

BugReport = BugReportDetail

__all__ = [
    "BugReport",
    "BugReportContext",
    "BugReportCreate",
    "BugReportDetail",
    "BugReportListResponse",
    "BugReportStatusUpdate",
    "BugReportSummary",
    "FileStorage",
    "LifecycleEvent",
    "PostgresStorage",
    "SQLiteStorage",
    "Severity",
    "Status",
    "Storage",
    "__version__",
    "submit_router",
    "viewer_router",
]


def __getattr__(name: str):
    """Lazy import of SQL backends so SQLAlchemy stays optional."""
    if name in ("SQLiteStorage", "PostgresStorage"):
        from bug_fab import storage as _storage

        return getattr(_storage, name)
    raise AttributeError(f"module 'bug_fab' has no attribute {name!r}")
