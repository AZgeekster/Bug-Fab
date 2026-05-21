"""Tests for the structured-logging vocabulary.

Pins the event-name constants + the schema of the ``extra`` dict
emitted by :func:`bug_fab._observability.emit`. Consumers wiring a
JSON log shipper (Loki, Datadog, Sentry, Logfire, etc.) will key off
these field names; renaming them is a breaking change for them.
"""

from __future__ import annotations

import logging

import pytest

from bug_fab._observability import (
    ALL_EVENTS,
    EVENT_BULK_ARCHIVE_CLOSED,
    EVENT_BULK_CLOSE_FIXED,
    EVENT_REPORT_DELETED,
    EVENT_REPORT_RECEIVED,
    EVENT_STATUS_CHANGED,
    emit,
)


def test_event_constants_match_documented_vocabulary() -> None:
    """Pin the wire-stable event names — renaming is a breaking change."""
    assert EVENT_REPORT_RECEIVED == "bug_fab_report_received"
    assert EVENT_STATUS_CHANGED == "bug_fab_status_changed"
    assert EVENT_REPORT_DELETED == "bug_fab_report_deleted"
    assert EVENT_BULK_CLOSE_FIXED == "bug_fab_bulk_close_fixed"
    assert EVENT_BULK_ARCHIVE_CLOSED == "bug_fab_bulk_archive_closed"


def test_all_events_tuple_covers_every_published_constant() -> None:
    """A contract test: ALL_EVENTS must enumerate every public event."""
    assert set(ALL_EVENTS) == {
        EVENT_REPORT_RECEIVED,
        EVENT_STATUS_CHANGED,
        EVENT_REPORT_DELETED,
        EVENT_BULK_CLOSE_FIXED,
        EVENT_BULK_ARCHIVE_CLOSED,
    }


def test_emit_writes_info_record_with_event_in_extra(caplog: pytest.LogCaptureFixture) -> None:
    """Each call produces one INFO record with the event name in `extra`."""
    caplog.set_level(logging.INFO, logger="bug_fab.events")
    emit(EVENT_REPORT_RECEIVED, report_id="bug-001", severity="high")
    records = [r for r in caplog.records if r.name == "bug_fab.events"]
    assert len(records) == 1
    rec = records[0]
    assert rec.levelno == logging.INFO
    assert rec.msg == EVENT_REPORT_RECEIVED
    # `extra` fields are attached to the LogRecord directly.
    assert rec.event == EVENT_REPORT_RECEIVED  # type: ignore[attr-defined]
    assert rec.report_id == "bug-001"  # type: ignore[attr-defined]
    assert rec.severity == "high"  # type: ignore[attr-defined]


def test_emit_with_no_extra_fields_still_carries_event(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The event name alone is a valid log shape."""
    caplog.set_level(logging.INFO, logger="bug_fab.events")
    emit(EVENT_BULK_ARCHIVE_CLOSED)
    records = [r for r in caplog.records if r.name == "bug_fab.events"]
    assert len(records) == 1
    assert records[0].event == EVENT_BULK_ARCHIVE_CLOSED  # type: ignore[attr-defined]


def test_emit_uses_dedicated_logger_so_consumers_can_filter(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Events flow through ``bug_fab.events`` — consumers can isolate them."""
    caplog.set_level(logging.INFO, logger="bug_fab.events")
    emit(EVENT_STATUS_CHANGED, report_id="bug-002", status="fixed")
    assert any(r.name == "bug_fab.events" for r in caplog.records)


def test_logger_suppression_above_info_silences_events(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Setting the events logger to WARNING silences the full vocabulary."""
    events_logger = logging.getLogger("bug_fab.events")
    original = events_logger.level
    try:
        events_logger.setLevel(logging.WARNING)
        caplog.set_level(logging.WARNING, logger="bug_fab.events")
        emit(EVENT_REPORT_DELETED, report_id="bug-099")
        # No INFO records survive the level gate.
        assert not [
            r for r in caplog.records if r.name == "bug_fab.events" and r.levelno == logging.INFO
        ]
    finally:
        events_logger.setLevel(original)
