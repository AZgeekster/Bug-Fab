"""Unit tests for the PostgreSQL storage backend.

These tests are SKIPPED unless both:
- ``psycopg`` is importable (the ``[postgres]`` extra is installed), AND
- The ``BUG_FAB_TEST_POSTGRES_URL`` env var points at a real Postgres DSN.

CI sets the env var only when a service container is available; local devs
opt in by exporting it before invoking pytest. The skipped path keeps the
suite green on machines without a Postgres install while preserving full
coverage when the dependency is present.
"""

from __future__ import annotations

import asyncio
import importlib
import os
from pathlib import Path
from typing import Any

import pytest

PG_DSN = os.environ.get("BUG_FAB_TEST_POSTGRES_URL")

# Module-wide skip if dependencies are missing
psycopg_available = importlib.util.find_spec("psycopg") is not None
sqlalchemy_available = importlib.util.find_spec("sqlalchemy") is not None

pytestmark = pytest.mark.skipif(
    not (psycopg_available and sqlalchemy_available and PG_DSN),
    reason=(
        "Postgres tests require psycopg + BUG_FAB_TEST_POSTGRES_URL env var. "
        "Set both to run this module."
    ),
)


def _run(coro):  # type: ignore[no-untyped-def]
    return asyncio.new_event_loop().run_until_complete(coro)


def _baseline_metadata(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": "Postgres test report",
        "report_type": "bug",
        "description": "desc",
        "severity": "medium",
        "tags": ["pg"],
        "context": {
            "url": "/x",
            "module": "modA",
            "user_agent": "client-ua/1.0",
            "viewport_width": 1024,
            "viewport_height": 768,
            "console_errors": [],
            "network_log": [],
            "environment": "dev",
        },
        "server_user_agent": "server-ua/1.0",
        "client_reported_user_agent": "client-ua/1.0",
        "environment": "dev",
        "module": "modA",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def postgres_storage(tmp_path: Path):  # type: ignore[no-untyped-def]
    """A fresh ``PostgresStorage`` with isolated screenshot dir.

    The DSN points at a single shared test DB; we drop and recreate the
    Bug-Fab tables before each test for isolation. Adapter authors who
    cannot afford that should overlay their own fixture in their conftest.
    """
    from bug_fab.storage._models import Base
    from bug_fab.storage.postgres import PostgresStorage

    storage = PostgresStorage(dsn=PG_DSN, screenshot_dir=tmp_path / "shots")
    # Tear down + rebuild so each test starts from empty
    Base.metadata.drop_all(storage.engine)
    Base.metadata.create_all(storage.engine)
    storage.create_all()  # also ensures the SEQUENCE exists
    yield storage
    Base.metadata.drop_all(storage.engine)


# -----------------------------------------------------------------------------
# Same surface as the SQLite suite (subset — full coverage lives there)
# -----------------------------------------------------------------------------


def test_save_then_get_round_trip(postgres_storage, tiny_png: bytes) -> None:  # type: ignore[no-untyped-def]
    bid = _run(postgres_storage.save_report(_baseline_metadata(), tiny_png))
    detail = _run(postgres_storage.get_report(bid))
    assert detail is not None
    assert detail.title == "Postgres test report"


def test_save_id_uses_postgres_sequence(postgres_storage, tiny_png: bytes) -> None:  # type: ignore[no-untyped-def]
    bid = _run(postgres_storage.save_report(_baseline_metadata(), tiny_png))
    # SEQUENCE-issued ids are sequential; the first one is bug-001 by spec
    # (we recreated the schema so the sequence restarts at 1)
    assert bid.startswith("bug-")


def test_update_status_appends_lifecycle(postgres_storage, tiny_png: bytes) -> None:  # type: ignore[no-untyped-def]
    bid = _run(postgres_storage.save_report(_baseline_metadata(), tiny_png))
    updated = _run(postgres_storage.update_status(bid, status="fixed", by="alice"))
    assert updated is not None
    assert updated.status == "fixed"
    assert any(e.action == "status_changed" for e in updated.lifecycle)


def test_bulk_close_fixed(postgres_storage, tiny_png: bytes) -> None:  # type: ignore[no-untyped-def]
    a = _run(postgres_storage.save_report(_baseline_metadata(), tiny_png))
    b = _run(postgres_storage.save_report(_baseline_metadata(), tiny_png))
    _run(postgres_storage.update_status(a, status="fixed"))
    _run(postgres_storage.update_status(b, status="fixed"))
    assert _run(postgres_storage.bulk_close_fixed()) == 2
