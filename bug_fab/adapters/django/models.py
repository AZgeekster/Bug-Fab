"""Django ORM models for the Bug-Fab reusable app.

Modeled after the SQLAlchemy schema in ``bug_fab/storage/_models.py``,
but **not column-for-column identical** — the two backends agree on the
wire-protocol projection (see ``storage.py:_to_summary`` /
``_to_detail``), and diverge wherever a Django idiom is cleaner than a
direct port. Snake_case field names match ``bug_fab/schemas.py`` so the
Pydantic models round-trip without renames.

Known-by-design differences vs ``bug_fab/storage/_models.py`` — do NOT
"fix" these without re-running the conformance suite:

* ``screenshot``: ``FileField(upload_to="bug_fab_screenshots/", max_length=512)``
  replaces the SQLAlchemy ``screenshot_path`` string column. The Django
  idiom owns its own on-disk layout via ``MEDIA_ROOT``; the read
  projection serializes the resolved path back to the wire field.
* Blank-string defaults vs nullable: most optional string columns
  (``description``, ``environment``, ``app_name``, ``app_version``,
  ``reporter``, ``page_url``, ``module``, ``user_agent_*``,
  ``metadata_json``) use ``blank=True, default=""`` here instead of the
  SQLAlchemy ``nullable=True``. The read projection coerces both shapes
  via ``or ""`` so the wire response is identical.
* ``severity`` default: ``"medium"`` here vs nullable in SQLAlchemy.
  ``_to_summary`` / ``_to_detail`` coerce ``None``/``""`` to
  :attr:`bug_fab.schemas.Severity.MEDIUM` on the read side, so the
  observable response shape matches.
* ``protocol_version`` default: ``"0.1"`` here vs no default in
  SQLAlchemy. Intake always writes the validated value, so the default
  only applies if a row is created outside the intake pipeline.
* ``id`` length cap: ``CharField(max_length=64)`` here vs unbounded
  varchar in SQLAlchemy. Adequate for ``bug-{prefix}{12digits}``
  (~17 chars) plus headroom for a longer ``BUG_FAB_ID_PREFIX``.

Two tables:

* ``bug_fab_bugreport`` — one row per report. Stores the denormalized
  hot columns (status, severity, environment, etc.) plus the original
  metadata blob in :attr:`BugReport.metadata_json` for round-trip
  fidelity per ``docs/PROTOCOL.md`` § Storage round-trip notes.
* ``bug_fab_bugreportlifecycle`` — append-only audit log. The reusable
  app NEVER updates rows here; new state transitions insert.

Screenshots live on disk under ``MEDIA_ROOT / bug_fab_screenshots / ...``
via :class:`~django.db.models.FileField`. Bytes are not stored in the
database.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone

#: Locked enum values for write-side validation. Reads accept any string
#: per the deprecated-values rule in ``docs/PROTOCOL.md`` § Versioning,
#: so the model fields use ``CharField`` rather than a Django choices
#: enum constraint at the DB layer (the application-level validators in
#: ``bug_fab.intake.validate_payload`` enforce write-time strictness).
ALLOWED_SEVERITIES = ("low", "medium", "high", "critical")
ALLOWED_STATUSES = ("open", "investigating", "fixed", "closed")
ALLOWED_REPORT_TYPES = ("bug", "feature_request")


class BugReport(models.Model):
    """One bug report. Wire-protocol-equivalent to
    :class:`bug_fab.storage._models.BugReport` (see module docstring for
    the list of intentional Django-idiom divergences).
    """

    #: Human-readable IDs in the ``bug-NNN`` shape (or ``bug-{prefix}NNN``
    #: when the optional env var ``BUG_FAB_ID_PREFIX`` is set). Allocated
    #: by :class:`bug_fab.adapters.django.storage.DjangoORMStorage` inside
    #: the same transaction as the row insert.
    id = models.CharField(primary_key=True, max_length=64)

    received_at = models.DateTimeField(default=timezone.now, db_index=True)
    protocol_version = models.CharField(max_length=16, default="0.1")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")

    # Wire-protocol "report_type" lives in metadata_json (no typed column
    # in v0.1) — matches the SQL backends' choice. Filtering on it forces
    # a full scan + JSON parse, so list-view filters silently ignore it.

    severity = models.CharField(max_length=16, blank=True, default="medium", db_index=True)
    status = models.CharField(max_length=16, default="open", db_index=True)

    environment = models.CharField(max_length=64, blank=True, default="", db_index=True)
    app_name = models.CharField(max_length=128, blank=True, default="")
    app_version = models.CharField(max_length=64, blank=True, default="")

    # Denormalized printable reporter identifier (priority: email > user_id > name).
    # Full reporter object is preserved verbatim in metadata_json.
    reporter = models.CharField(max_length=256, blank=True, default="")

    page_url = models.TextField(blank=True, default="")
    module = models.CharField(max_length=128, blank=True, default="")

    # User-Agent trust boundary — keep both, never overwrite the
    # server-captured value with the client value.
    user_agent_server = models.TextField(blank=True, default="")
    user_agent_client = models.TextField(blank=True, default="")

    metadata_json = models.TextField(blank=True, default="")
    screenshot = models.FileField(upload_to="bug_fab_screenshots/", max_length=512)

    github_issue_url = models.URLField(blank=True, default="", max_length=512)
    github_issue_number = models.IntegerField(null=True, blank=True)

    archived_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        verbose_name = "Bug report"
        verbose_name_plural = "Bug reports"
        ordering = ["-received_at", "-id"]
        # Index names are pinned (not Django-auto-generated) so the
        # hand-authored migration ``0001_initial.py`` stays in sync
        # with the model — the ``makemigrations --check`` CI guard
        # fails otherwise.
        indexes = [
            models.Index(fields=["status", "severity"], name="bug_fab_bug_status_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover - admin-readable repr
        return f"{self.id} — {self.title}"


class BugReportLifecycle(models.Model):
    """Append-only audit log of state changes for a :class:`BugReport`.

    Action enum: ``created`` | ``status_changed`` | ``deleted`` |
    ``archived``. The Django adapter never updates existing rows here —
    state transitions always insert.
    """

    bug_report = models.ForeignKey(
        BugReport,
        on_delete=models.CASCADE,
        related_name="lifecycle",
    )
    action = models.CharField(max_length=32)
    by = models.CharField(max_length=256, blank=True, default="anonymous")
    at = models.DateTimeField(default=timezone.now)
    fix_commit = models.CharField(max_length=512, blank=True, default="")
    fix_description = models.TextField(blank=True, default="")
    metadata_json = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["at", "id"]
        # Index name is pinned to match the hand-authored migration
        # ``0001_initial.py`` — keeps ``makemigrations --check`` clean.
        indexes = [
            models.Index(fields=["bug_report", "at"], name="bug_fab_life_report_at_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover - admin-readable repr
        return f"{self.bug_report_id} {self.action} @ {self.at.isoformat()}"
