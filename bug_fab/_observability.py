"""Structured-logging hooks for Bug-Fab's lifecycle events.

Every intake / status-change / delete / archive / bulk-op emits one
``logger.info`` call with a stable, machine-readable
``extra={"event": ..., "report_id": ..., ...}`` payload. Consumers
who want JSON line output for Loki / Datadog / Sentry plug in a
standard formatter (e.g., ``python-json-logger``) on the
``bug_fab`` logger tree — this module does NOT take a dependency on
any particular log shipper.

Why a thin helper instead of inline ``logger.info`` calls at each
site: the value of structured logging is in the **stability of the
event vocabulary**. Centralising the event names + the schema of
the extra dict here means a future protocol change is a single-file
edit and not a forensic crawl across routers.

Stable event vocabulary (don't rename without a deprecation window;
ops dashboards and alerts will key off these names):

- ``bug_fab_report_received`` — POST /api/bug-reports succeeded
- ``bug_fab_status_changed`` — PUT /reports/{id}/status succeeded
- ``bug_fab_report_deleted`` — DELETE /reports/{id} succeeded
- ``bug_fab_bulk_close_fixed`` — POST /bulk-close-fixed succeeded
- ``bug_fab_bulk_archive_closed`` — POST /bulk-archive-closed succeeded

All events are emitted at ``INFO`` so consumers can suppress the
whole vocabulary by setting the ``bug_fab`` logger above INFO.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("bug_fab.events")

#: Event name constants. Imported by routers to avoid stringly-typed
#: drift; tests assert on these values to catch renames.
EVENT_REPORT_RECEIVED = "bug_fab_report_received"
EVENT_STATUS_CHANGED = "bug_fab_status_changed"
EVENT_REPORT_DELETED = "bug_fab_report_deleted"
EVENT_BULK_CLOSE_FIXED = "bug_fab_bulk_close_fixed"
EVENT_BULK_ARCHIVE_CLOSED = "bug_fab_bulk_archive_closed"

#: Every published event name, for tests that want to enumerate them
#: (e.g., a contract test that asserts the vocabulary is closed).
ALL_EVENTS: tuple[str, ...] = (
    EVENT_REPORT_RECEIVED,
    EVENT_STATUS_CHANGED,
    EVENT_REPORT_DELETED,
    EVENT_BULK_CLOSE_FIXED,
    EVENT_BULK_ARCHIVE_CLOSED,
)


def emit(event: str, **fields: Any) -> None:
    """Emit one structured lifecycle event at INFO level.

    Always uses the constant ``event`` field name in the extra dict
    (callers may NOT pass an ``event`` kwarg — the helper owns that
    key). Other fields pass through verbatim so consumers can extend
    the schema without coordinating with this module — typical
    fields are ``report_id``, ``severity``, ``status``, ``by``,
    ``count`` (for bulk), and ``environment``.

    The ``msg`` argument to ``logger.info`` is the event name as a
    plain string so handlers that don't render ``extra`` still get
    something useful in the message line.
    """
    extra: dict[str, Any] = {"event": event}
    extra.update(fields)
    logger.info(event, extra=extra)


__all__ = [
    "ALL_EVENTS",
    "EVENT_BULK_ARCHIVE_CLOSED",
    "EVENT_BULK_CLOSE_FIXED",
    "EVENT_REPORT_DELETED",
    "EVENT_REPORT_RECEIVED",
    "EVENT_STATUS_CHANGED",
    "emit",
]
