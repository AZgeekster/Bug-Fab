"""initial schema: bug_reports + bug_report_lifecycle + id helpers

Revision ID: 0001_initial
Revises:
Create Date: 2026-04-27 00:00:00.000000

Creates the v0.1 schema exactly per ``docs/database_schema.md`` § "SQL
Backends":

- ``bug_reports`` — denormalized report row with full ``metadata_json`` blob.
- ``bug_report_lifecycle`` — append-only audit log keyed by ``bug_report_id``.
- ``bug_report_id_counter`` — single-row counter for SQLite ID generation.
- ``bug_report_id_seq`` — Postgres-only SEQUENCE for ID generation (skipped
  on other dialects).
- Indexes on ``received_at``, ``status``, ``severity``, ``environment``,
  ``archived_at``, and ``bug_report_lifecycle.bug_report_id``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    is_sqlite = bind.dialect.name == "sqlite"

    # --- bug_reports -------------------------------------------------
    op.create_table(
        "bug_reports",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("protocol_version", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="open"),
        sa.Column("environment", sa.String(), nullable=True),
        sa.Column("app_name", sa.String(), nullable=True),
        sa.Column("app_version", sa.String(), nullable=True),
        sa.Column("reporter", sa.String(), nullable=True),
        sa.Column("page_url", sa.String(), nullable=True),
        sa.Column("module", sa.String(), nullable=True),
        sa.Column("user_agent_server", sa.String(), nullable=True),
        sa.Column("user_agent_client", sa.String(), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.Column("screenshot_path", sa.String(), nullable=False),
        sa.Column("github_issue_url", sa.String(), nullable=True),
        sa.Column("github_issue_number", sa.Integer(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "severity IS NULL OR severity IN ('low','medium','high','critical')",
            name="ck_bug_reports_severity",
        ),
    )
    op.create_index("idx_bug_reports_received_at", "bug_reports", ["received_at"])
    op.create_index("idx_bug_reports_status", "bug_reports", ["status"])
    op.create_index("idx_bug_reports_severity", "bug_reports", ["severity"])
    op.create_index("idx_bug_reports_environment", "bug_reports", ["environment"])
    op.create_index("idx_bug_reports_archived_at", "bug_reports", ["archived_at"])

    # --- bug_report_lifecycle ----------------------------------------
    op.create_table(
        "bug_report_lifecycle",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "bug_report_id",
            sa.String(),
            sa.ForeignKey("bug_reports.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("by", sa.String(), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fix_commit", sa.String(), nullable=True),
        sa.Column("fix_description", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
    )
    op.create_index(
        "idx_bug_report_lifecycle_bug_report_id",
        "bug_report_lifecycle",
        ["bug_report_id"],
    )

    # --- ID generation helpers ---------------------------------------
    if is_sqlite:
        # SQLite has no SEQUENCE — we use a single-row counter table.
        op.create_table(
            "bug_report_id_counter",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
            sa.Column("last_value", sa.Integer(), nullable=False, server_default="0"),
        )
    elif is_postgres:
        op.execute("CREATE SEQUENCE IF NOT EXISTS bug_report_id_seq START 1 INCREMENT 1")
    else:  # pragma: no cover - other dialects unsupported in v0.1
        # Fall back to the counter table on any unknown dialect (e.g. MySQL,
        # if a contributor enables it post-v0.1).
        op.create_table(
            "bug_report_id_counter",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
            sa.Column("last_value", sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    is_sqlite = bind.dialect.name == "sqlite"

    if is_postgres:
        op.execute("DROP SEQUENCE IF EXISTS bug_report_id_seq")
    elif is_sqlite:
        op.drop_table("bug_report_id_counter")
    else:  # pragma: no cover
        op.drop_table("bug_report_id_counter")

    op.drop_index("idx_bug_report_lifecycle_bug_report_id", table_name="bug_report_lifecycle")
    op.drop_table("bug_report_lifecycle")

    op.drop_index("idx_bug_reports_archived_at", table_name="bug_reports")
    op.drop_index("idx_bug_reports_environment", table_name="bug_reports")
    op.drop_index("idx_bug_reports_severity", table_name="bug_reports")
    op.drop_index("idx_bug_reports_status", table_name="bug_reports")
    op.drop_index("idx_bug_reports_received_at", table_name="bug_reports")
    op.drop_table("bug_reports")
