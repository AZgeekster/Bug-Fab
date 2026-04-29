"""SQLAlchemy 2.0 declarative models for the SQL storage backends.

These models back both ``SQLiteStorage`` and ``PostgresStorage``. Screenshots
are NEVER stored in the database — the ``screenshot_path`` column points to a
file on disk written by the storage backend.

The schema mirrors ``docs/database_schema.md`` § "SQL Backends" exactly.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Locked enum values that may be written. Reads are permissive (any string from
# the database is accepted, including deprecated values like a legacy
# ``"resolved"`` status — see PROTOCOL.md § "Deprecated values rule").
ALLOWED_SEVERITIES = ("low", "medium", "high", "critical")
ALLOWED_STATUSES = ("open", "investigating", "fixed", "closed")


class Base(DeclarativeBase):
    """Declarative base shared by every model in this package."""


class BugReport(Base):
    """A single bug report row.

    The ``metadata_json`` column holds the full original wire-protocol payload
    for fidelity; the typed columns above it are denormalized for efficient
    indexed queries (status, severity, environment, received_at, archived_at).
    """

    __tablename__ = "bug_reports"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    protocol_version: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="open")
    environment: Mapped[str | None] = mapped_column(String, nullable=True)
    app_name: Mapped[str | None] = mapped_column(String, nullable=True)
    app_version: Mapped[str | None] = mapped_column(String, nullable=True)
    reporter: Mapped[str | None] = mapped_column(String, nullable=True)
    page_url: Mapped[str | None] = mapped_column(String, nullable=True)
    module: Mapped[str | None] = mapped_column(String, nullable=True)
    user_agent_server: Mapped[str | None] = mapped_column(String, nullable=True)
    user_agent_client: Mapped[str | None] = mapped_column(String, nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False)
    screenshot_path: Mapped[str] = mapped_column(String, nullable=False)
    github_issue_url: Mapped[str | None] = mapped_column(String, nullable=True)
    github_issue_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    lifecycle: Mapped[list[BugReportLifecycle]] = relationship(
        back_populates="bug_report",
        cascade="all, delete-orphan",
        order_by="BugReportLifecycle.at",
    )

    __table_args__ = (
        # Severity is enforced at write time on the application side too, but a
        # CHECK keeps deprecated/garbage values out of the table on direct DB
        # writes. Note: read paths are permissive — they use the raw column
        # value without re-validating, so this constraint only blocks new rows.
        CheckConstraint(
            f"severity IS NULL OR severity IN {ALLOWED_SEVERITIES}",
            name="ck_bug_reports_severity",
        ),
        Index("idx_bug_reports_received_at", "received_at"),
        Index("idx_bug_reports_status", "status"),
        Index("idx_bug_reports_severity", "severity"),
        Index("idx_bug_reports_environment", "environment"),
        Index("idx_bug_reports_archived_at", "archived_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<BugReport id={self.id!r} status={self.status!r}>"


class BugReportLifecycle(Base):
    """Append-only audit log of state changes for a bug report.

    Each status change, deletion, or archive operation appends one row. The
    ``metadata_json`` column is free-form per action.
    """

    __tablename__ = "bug_report_lifecycle"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bug_report_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("bug_reports.id", ondelete="CASCADE"),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(String, nullable=False)
    by: Mapped[str | None] = mapped_column(String, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fix_commit: Mapped[str | None] = mapped_column(String, nullable=True)
    fix_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    bug_report: Mapped[BugReport] = relationship(back_populates="lifecycle")

    __table_args__ = (Index("idx_bug_report_lifecycle_bug_report_id", "bug_report_id"),)

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<BugReportLifecycle id={self.id} bug_report_id={self.bug_report_id!r} "
            f"action={self.action!r}>"
        )


# ID-counter helper table for SQLite. Postgres uses a dedicated SEQUENCE
# created in the initial Alembic migration. SQLite does not support sequences,
# so we keep a single-row counter table and bump it in a transaction.
class BugReportIdCounter(Base):
    """SQLite-only counter row used to generate sequential ``bug-NNN`` IDs.

    Postgres backends ignore this table and use ``bug_report_id_seq`` instead.
    A single row with ``id=1`` holds the last-issued integer; the storage
    backend updates it inside the same transaction as the insert.
    """

    __tablename__ = "bug_report_id_counter"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    last_value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
