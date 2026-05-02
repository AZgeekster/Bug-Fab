"""Django ORM storage layer for the Bug-Fab reusable app.

Implements the same conceptual surface as :class:`bug_fab.storage.Storage`
but in synchronous Python — Django's ORM is sync, and an
``async_to_sync`` bridge per call would be slower and more surprising
than just exposing sync methods. The Django views call this class
directly; non-Django code keeps using the async ABC.

Storage responsibilities map one-to-one to the FastAPI reference:

* :meth:`save_report` — atomic ``BugReport`` + initial ``created``
  lifecycle insert plus screenshot file write.
* :meth:`get_report` — projection to :class:`bug_fab.schemas.BugReportDetail`.
* :meth:`list_reports` — filtered + paginated summaries.
* :meth:`update_status` — locked row update + ``status_changed`` lifecycle.
* :meth:`delete_report` — hard delete (cascade to lifecycle + file unlink).
* :meth:`archive_report` — stamp ``archived_at`` + lifecycle ``archived``.
* :meth:`bulk_close_fixed` / :meth:`bulk_archive_closed` — batched updates.
* :meth:`set_github_link` — best-effort GitHub Issues link persistence.

The screenshot directory is wherever Django's ``MEDIA_ROOT`` plus the
``upload_to`` argument on the ``FileField`` resolves — no path
hard-coding here. Production deployments should set ``MEDIA_ROOT`` to a
persistent volume.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q
from django.utils import timezone as dj_timezone

from bug_fab.schemas import (
    BugReportContext,
    BugReportDetail,
    BugReportSummary,
    LifecycleEvent,
    Severity,
    Status,
)

from .models import (
    ALLOWED_SEVERITIES,
    ALLOWED_STATUSES,
    BugReport,
    BugReportLifecycle,
)

logger = logging.getLogger(__name__)

#: Path-traversal guard mirroring the FastAPI viewer's regex. Anything
#: outside this character class is rejected at the route layer with 404
#: before it reaches the storage class.
REPORT_ID_RE = re.compile(r"^bug-[A-Za-z]?\d{1,12}$")


class StorageError(ValueError):
    """Raised on validation or invariant failures in the Django storage layer."""


def _id_prefix() -> str:
    """Resolve the optional ``BUG_FAB_ID_PREFIX`` env var.

    Empty / unset means the default ``bug-NNN`` format. Non-empty values
    yield ``bug-{prefix}NNN`` (e.g., ``bug-P038``) — useful for shared
    multi-environment collectors.
    """
    return os.environ.get("BUG_FAB_ID_PREFIX", "")


def _format_id(n: int) -> str:
    """Format ``n`` as ``bug-NNN`` (or ``bug-{prefix}NNN`` if env set)."""
    prefix = _id_prefix()
    return f"bug-{prefix}{n:03d}"


def _validate_severity_for_write(severity: str | None) -> None:
    """Strict severity check — silent coercion fails conformance."""
    if severity is None or severity == "":
        return
    if severity not in ALLOWED_SEVERITIES:
        raise StorageError(f"invalid severity {severity!r}; expected one of {ALLOWED_SEVERITIES}")


def _validate_status_for_write(status: str) -> None:
    """Strict status check — accepted on read, rejected on write."""
    if status not in ALLOWED_STATUSES:
        raise StorageError(f"invalid status {status!r}; expected one of {ALLOWED_STATUSES}")


def _extract_reporter(metadata: dict) -> str:
    """Pull a printable reporter identifier in priority order.

    Wire shape is ``{name?, email?, user_id?}``. We surface the first
    populated value as a denormalized string for the list-view "reporter"
    column; the full nested object stays in :attr:`BugReport.metadata_json`.
    """
    reporter = metadata.get("reporter")
    if not isinstance(reporter, dict):
        return ""
    for key in ("email", "user_id", "name"):
        value = reporter.get(key)
        if value:
            return str(value)
    return ""


def _iso(dt: datetime | None) -> str:
    """ISO-8601 stringify a datetime, treating naive as UTC."""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _safe_metadata(raw: str) -> dict[str, Any]:
    """Parse the stored metadata JSON, returning ``{}`` on any failure."""
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _to_summary(row: BugReport) -> BugReportSummary:
    """Project a :class:`BugReport` row into a :class:`BugReportSummary`."""
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
            "has_screenshot": bool(row.screenshot),
            "github_issue_url": row.github_issue_url or None,
        }
    )


def _to_detail(row: BugReport) -> BugReportDetail:
    """Project a :class:`BugReport` + lifecycle into :class:`BugReportDetail`."""
    metadata = _safe_metadata(row.metadata_json)
    context_data = dict(metadata.get("context") or {})
    reporter_data = metadata.get("reporter") or {}
    if not isinstance(reporter_data, dict):
        reporter_data = {}
    lifecycle_qs = list(row.lifecycle.all().order_by("at", "id"))
    payload = {
        "id": row.id,
        "title": row.title,
        "report_type": metadata.get("report_type", "bug"),
        "severity": row.severity or Severity.MEDIUM.value,
        "status": row.status or Status.OPEN.value,
        "module": row.module or "",
        "created_at": _iso(row.received_at),
        "has_screenshot": bool(row.screenshot),
        "github_issue_url": row.github_issue_url or None,
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
            for entry in lifecycle_qs
        ],
        "server_user_agent": row.user_agent_server or "",
        "client_reported_user_agent": row.user_agent_client or "",
        "environment": row.environment or "",
        "updated_at": _iso(_latest_lifecycle_at(lifecycle_qs) or row.received_at),
    }
    return BugReportDetail.model_validate(payload)


def _latest_lifecycle_at(entries: list[BugReportLifecycle]) -> datetime | None:
    """Return the most-recent lifecycle ``at`` timestamp, or ``None``."""
    if not entries:
        return None
    return max((entry.at for entry in entries if entry.at is not None), default=None)


class DjangoORMStorage:
    """Synchronous Django-ORM-backed storage for Bug-Fab reports.

    Mirrors the surface of :class:`bug_fab.storage.Storage` (sync, not
    async) so the views layer in :mod:`bug_fab.adapters.django.views`
    has a uniform persistence API regardless of the underlying database
    (SQLite for dev, Postgres for prod, etc.).
    """

    # ---- core CRUD -------------------------------------------------------

    def save_report(
        self,
        metadata: dict,
        screenshot_bytes: bytes,
    ) -> str:
        """Persist a new report and return its assigned ``bug-NNN`` ID.

        Inserts the row, writes the screenshot via the ``FileField``, and
        appends the initial ``created`` lifecycle entry — all inside one
        transaction. ``select_for_update`` on the highest-existing-row
        keeps the next-id allocation safe under multi-worker concurrency.
        """
        severity = metadata.get("severity") or Severity.MEDIUM.value
        _validate_severity_for_write(severity)

        title = metadata.get("title", "")
        if not title:
            raise StorageError("metadata.title is required")

        protocol_version = metadata.get("protocol_version", "0.1")
        context = dict(metadata.get("context") or {})
        received_at = dj_timezone.now()

        with transaction.atomic():
            # Allocate the next ID by inspecting the existing max. For
            # production Postgres a SEQUENCE would be tighter, but the
            # locked select_for_update + atomic block keeps this safe
            # across workers for the small-N workloads Django consumers
            # typically run.
            last_row = BugReport.objects.select_for_update().order_by("-received_at", "-id").first()
            next_int = _next_int_after(last_row)
            report_id = _format_id(next_int)

            report = BugReport(
                id=report_id,
                received_at=received_at,
                protocol_version=protocol_version,
                title=title,
                description=metadata.get("description", "") or "",
                severity=severity,
                status=Status.OPEN.value,
                environment=(metadata.get("environment") or context.get("environment") or ""),
                app_name=(metadata.get("app_name") or context.get("app_name") or ""),
                app_version=(metadata.get("app_version") or context.get("app_version") or ""),
                reporter=_extract_reporter(metadata),
                page_url=(metadata.get("page_url") or context.get("url") or ""),
                module=(metadata.get("module") or context.get("module") or ""),
                user_agent_server=metadata.get("server_user_agent", "") or "",
                user_agent_client=(
                    metadata.get("client_reported_user_agent") or context.get("user_agent") or ""
                ),
                metadata_json=json.dumps(metadata, default=str, ensure_ascii=False),
                github_issue_url=metadata.get("github_issue_url", "") or "",
                github_issue_number=metadata.get("github_issue_number"),
            )
            report.screenshot.save(
                f"{report_id}.png",
                ContentFile(screenshot_bytes),
                save=False,
            )
            report.save()

            BugReportLifecycle.objects.create(
                bug_report=report,
                action="created",
                by=metadata.get("submitted_by", "") or "anonymous",
                at=received_at,
            )
            return report_id

    def get_report(self, report_id: str) -> BugReportDetail | None:
        """Return the full detail payload, or ``None`` if not found."""
        if not REPORT_ID_RE.match(report_id or ""):
            return None
        try:
            row = BugReport.objects.prefetch_related("lifecycle").get(pk=report_id)
        except BugReport.DoesNotExist:
            return None
        return _to_detail(row)

    def list_reports(
        self,
        filters: dict,
        page: int,
        page_size: int,
    ) -> tuple[list[BugReportSummary], int]:
        """Return ``(items, total)`` honoring filters and pagination.

        Filters silently drop empty / whitespace-only values to match the
        FastAPI reference. ``include_archived`` is honored when present;
        absent → archived rows excluded.
        """
        page = max(1, int(page or 1))
        page_size = max(1, min(int(page_size or 20), 200))
        offset = (page - 1) * page_size

        qs = BugReport.objects.all()
        if not (filters or {}).get("include_archived"):
            qs = qs.filter(archived_at__isnull=True)

        for key in ("status", "severity", "environment", "module"):
            wanted = (filters or {}).get(key)
            if wanted:
                qs = qs.filter(**{key: wanted})

        search = (filters or {}).get("search")
        if search:
            needle = str(search).lower()
            qs = qs.filter(
                Q(title__icontains=needle) | Q(module__icontains=needle) | Q(id__icontains=needle)
            )

        total = qs.count()
        rows = list(qs.order_by("-received_at", "-id")[offset : offset + page_size])
        return [_to_summary(row) for row in rows], total

    def get_screenshot_path(self, report_id: str) -> Path | None:
        """Return the on-disk path to the screenshot, or ``None``."""
        if not REPORT_ID_RE.match(report_id or ""):
            return None
        try:
            row = BugReport.objects.get(pk=report_id)
        except BugReport.DoesNotExist:
            return None
        if not row.screenshot:
            return None
        path = Path(row.screenshot.path)
        return path if path.exists() else None

    def update_status(
        self,
        report_id: str,
        status: str,
        fix_commit: str = "",
        fix_description: str = "",
        by: str = "",
    ) -> BugReportDetail | None:
        """Apply a status transition + lifecycle entry."""
        if not REPORT_ID_RE.match(report_id or ""):
            return None
        _validate_status_for_write(status)

        with transaction.atomic():
            try:
                row = BugReport.objects.select_for_update().get(pk=report_id)
            except BugReport.DoesNotExist:
                return None
            row.status = status
            row.save(update_fields=["status"])
            BugReportLifecycle.objects.create(
                bug_report=row,
                action="status_changed",
                by=by or "",
                at=dj_timezone.now(),
                fix_commit=fix_commit or "",
                fix_description=fix_description or "",
                metadata_json=json.dumps({"status": status}),
            )
        # Re-fetch to refresh the prefetched lifecycle relation.
        row = BugReport.objects.prefetch_related("lifecycle").get(pk=report_id)
        return _to_detail(row)

    def set_github_link(
        self,
        report_id: str,
        issue_number: int,
        issue_url: str,
    ) -> BugReportDetail | None:
        """Persist a GitHub issue link on the report row."""
        if not REPORT_ID_RE.match(report_id or ""):
            return None
        with transaction.atomic():
            try:
                row = BugReport.objects.select_for_update().get(pk=report_id)
            except BugReport.DoesNotExist:
                return None
            row.github_issue_number = issue_number
            row.github_issue_url = issue_url
            row.save(update_fields=["github_issue_number", "github_issue_url"])
        row = BugReport.objects.prefetch_related("lifecycle").get(pk=report_id)
        return _to_detail(row)

    def delete_report(self, report_id: str) -> bool:
        """Hard-delete the report + cascade to lifecycle + unlink the screenshot."""
        if not REPORT_ID_RE.match(report_id or ""):
            return False
        with transaction.atomic():
            try:
                row = BugReport.objects.select_for_update().get(pk=report_id)
            except BugReport.DoesNotExist:
                return False
            screenshot_path: Path | None = None
            if row.screenshot:
                with contextlib.suppress(ValueError, FileNotFoundError):
                    screenshot_path = Path(row.screenshot.path)
            row.delete()
        if screenshot_path is not None:
            with contextlib.suppress(OSError):
                screenshot_path.unlink(missing_ok=True)
        return True

    def archive_report(self, report_id: str) -> bool:
        """Soft-archive — stamp ``archived_at`` and append the lifecycle entry."""
        if not REPORT_ID_RE.match(report_id or ""):
            return False
        with transaction.atomic():
            try:
                row = BugReport.objects.select_for_update().get(pk=report_id)
            except BugReport.DoesNotExist:
                return False
            if row.archived_at is not None:
                return False
            now = dj_timezone.now()
            row.archived_at = now
            row.save(update_fields=["archived_at"])
            BugReportLifecycle.objects.create(
                bug_report=row,
                action="archived",
                by="",
                at=now,
            )
        return True

    def bulk_close_fixed(self, by: str = "") -> int:
        """Transition every ``fixed`` report to ``closed``.

        Returns the count of reports actually transitioned. Reports
        already in ``closed`` are not touched, matching protocol
        idempotency semantics.
        """
        now = dj_timezone.now()
        with transaction.atomic():
            target_ids = list(
                BugReport.objects.select_for_update()
                .filter(status=Status.FIXED.value)
                .values_list("id", flat=True)
            )
            if not target_ids:
                return 0
            BugReport.objects.filter(id__in=target_ids).update(status=Status.CLOSED.value)
            BugReportLifecycle.objects.bulk_create(
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
        return len(target_ids)

    def bulk_archive_closed(self) -> int:
        """Archive every non-archived ``closed`` report. Returns the count."""
        now = dj_timezone.now()
        with transaction.atomic():
            target_ids = list(
                BugReport.objects.select_for_update()
                .filter(status=Status.CLOSED.value, archived_at__isnull=True)
                .values_list("id", flat=True)
            )
            if not target_ids:
                return 0
            BugReport.objects.filter(id__in=target_ids).update(archived_at=now)
            BugReportLifecycle.objects.bulk_create(
                [
                    BugReportLifecycle(
                        bug_report_id=report_id,
                        action="archived",
                        by="",
                        at=now,
                    )
                    for report_id in target_ids
                ]
            )
        return len(target_ids)


def _next_int_after(last_row: BugReport | None) -> int:
    """Compute the next sequential integer for ``bug-NNN`` IDs.

    Parses the trailing digits off the last row's ID. Returns ``1`` when
    the table is empty. Stripping the optional ``[A-Za-z]`` prefix keeps
    multi-environment collectors (``bug-P038`` / ``bug-D012``) aligned
    on a single counter.
    """
    if last_row is None:
        return 1
    raw = last_row.id.removeprefix("bug-")
    digits = "".join(ch for ch in raw if ch.isdigit())
    return int(digits) + 1 if digits else 1
