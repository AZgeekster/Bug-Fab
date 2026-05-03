"""Shared pytest fixtures for the Bug-Fab unit + integration suites.

These fixtures cover the three things every Bug-Fab test needs:

1. A clean temporary storage area (``tmp_storage_dir``) so file/SQLite
   backends never collide across tests.
2. Backend factories (``file_storage``, ``sqlite_storage``) so a single
   test can choose its persistence layer without re-doing setup.
3. A sample valid metadata payload + tiny valid PNG so submit-router
   integration tests stay readable.
4. An ``app_factory`` that wires the submit + viewer routers onto a
   FastAPI ``TestClient`` with overridable dependencies.

The conformance plugin's existing ``--bug-fab-conformance`` flag scopes
its tests separately, so these fixtures can coexist with the conformance
suite without collection-time conflicts.
"""

from __future__ import annotations

import json
import struct
import zlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bug_fab.config import Settings
from bug_fab.routers import submit as submit_module
from bug_fab.routers.submit import (
    get_github_sync,
    get_rate_limiter,
    get_settings,
    get_storage,
    get_webhook_sync,
    submit_router,
)
from bug_fab.routers.viewer import viewer_router
from bug_fab.storage.base import Storage
from bug_fab.storage.files import FileStorage
from bug_fab.storage.sqlite import SQLiteStorage


def _make_test_png(width: int = 4, height: int = 4) -> bytes:
    """Hand-rolled minimal valid PNG (solid red).

    We do not import the conformance fixture helper here because pytest
    plugin loading would pick it up as a conformance test on collection.
    Re-implementing the few-byte PNG is cheaper than wiring around that.
    """
    if width < 1 or height < 1:
        raise ValueError("width and height must be >= 1")
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    ihdr_crc = zlib.crc32(b"IHDR" + ihdr_data)
    ihdr = struct.pack(">I", len(ihdr_data)) + b"IHDR" + ihdr_data + struct.pack(">I", ihdr_crc)
    raw = b""
    for _ in range(height):
        raw += b"\x00" + (b"\xff\x00\x00" * width)
    idat_data = zlib.compress(raw)
    idat_crc = zlib.crc32(b"IDAT" + idat_data)
    idat = struct.pack(">I", len(idat_data)) + b"IDAT" + idat_data + struct.pack(">I", idat_crc)
    iend_crc = zlib.crc32(b"IEND")
    iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", iend_crc)
    return signature + ihdr + idat + iend


@pytest.fixture
def tiny_png() -> bytes:
    """Few-byte valid PNG used as a placeholder screenshot."""
    return _make_test_png()


@pytest.fixture
def make_png() -> Callable[..., bytes]:
    """Factory for tests that need oversized or differently-shaped PNGs."""
    return _make_test_png


@pytest.fixture
def tmp_storage_dir(tmp_path: Path) -> Path:
    """A fresh temporary directory dedicated to one test's storage layout."""
    target = tmp_path / "bug_fab_storage"
    target.mkdir()
    return target


@pytest.fixture
def file_storage(tmp_storage_dir: Path) -> FileStorage:
    """Default-configured FileStorage rooted at the temp dir."""
    return FileStorage(tmp_storage_dir)


@pytest.fixture
def file_storage_factory(tmp_storage_dir: Path) -> Callable[..., FileStorage]:
    """Factory variant — tests pass a custom ``id_prefix`` or subdir."""

    def _factory(*, id_prefix: str = "", subdir: str | None = None) -> FileStorage:
        target = tmp_storage_dir if subdir is None else tmp_storage_dir / subdir
        target.mkdir(parents=True, exist_ok=True)
        return FileStorage(target, id_prefix=id_prefix)

    return _factory


@pytest.fixture
def sqlite_storage(tmp_storage_dir: Path) -> SQLiteStorage:
    """SQLiteStorage with a fresh on-disk database + schema applied.

    We use a real on-disk file (not ``:memory:``) because the engine pool
    treats memory URLs as per-connection — independent connections see
    empty schemas, which makes assertions flaky. ``tmp_path`` is wiped
    after every test so isolation is preserved.
    """
    db_path = tmp_storage_dir / "bug_reports.db"
    storage = SQLiteStorage(db_path=db_path, screenshot_dir=tmp_storage_dir / "screenshots")
    storage.create_all()
    return storage


@pytest.fixture
def sqlite_storage_factory(
    tmp_storage_dir: Path,
) -> Callable[..., SQLiteStorage]:
    """Factory variant — used when tests need multiple SQLite stores."""

    def _factory(name: str = "bug_reports.db") -> SQLiteStorage:
        db_path = tmp_storage_dir / name
        storage = SQLiteStorage(db_path=db_path, screenshot_dir=tmp_storage_dir / f"{name}.shots")
        storage.create_all()
        return storage

    return _factory


@pytest.fixture
def valid_metadata_dict() -> dict[str, Any]:
    """A canonical, all-fields-populated metadata dict (Python dict form)."""
    return {
        "protocol_version": "0.1",
        "title": "Submit form does not clear after success",
        "client_ts": "2026-04-29T12:00:00+00:00",
        "report_type": "bug",
        "description": "Steps: open page; submit; observe stale form fields.",
        "expected_behavior": "Form clears on successful submission.",
        "severity": "medium",
        "tags": ["regression", "ui"],
        "reporter": {"name": "", "email": "", "user_id": ""},
        "context": {
            "url": "http://localhost/sample/path",
            "module": "sample",
            "user_agent": "bug-fab-tests/0.1",
            "viewport_width": 1280,
            "viewport_height": 720,
            "console_errors": [],
            "network_log": [],
            "source_mapping": {},
            "app_version": "0.1.0",
            "environment": "dev",
        },
    }


@pytest.fixture
def valid_metadata(valid_metadata_dict: dict[str, Any]) -> str:
    """JSON-stringified valid metadata, ready for the multipart form field."""
    return json.dumps(valid_metadata_dict)


@pytest.fixture
def settings_factory() -> Callable[..., Settings]:
    """Build a ``Settings`` with arbitrary overrides.

    Calls ``Settings()`` directly (NOT ``Settings.from_env()``) so the
    returned object is hermetic to env vars set by the test runner.
    """

    def _factory(**overrides: Any) -> Settings:
        defaults = {
            "storage_dir": Path("./bug_reports"),
            "id_prefix": "",
            "max_upload_mb": 10,
            "rate_limit_enabled": False,
            "rate_limit_max": 50,
            "rate_limit_window_seconds": 3600,
            "viewer_enabled": True,
            "viewer_page_size": 20,
            "github_enabled": False,
            "github_pat": "",
            "github_repo": "",
            "github_api_base": "https://api.github.com",
        }
        defaults.update(overrides)
        return Settings(**defaults)

    return _factory


@pytest.fixture
def reset_router_module_state() -> Callable[[], None]:
    """Restore the submit-router's module-level singletons after a test.

    The router stores ``_STORAGE``, ``_SETTINGS``, ``_GITHUB_SYNC``,
    ``_WEBHOOK_SYNC``, and ``_RATE_LIMITER`` at module scope. Tests that
    call ``configure(...)`` must reset them so unrelated tests do not
    inherit a leftover state.
    """

    def _reset() -> None:
        submit_module._STORAGE = None
        submit_module._SETTINGS = None
        submit_module._GITHUB_SYNC = None
        submit_module._WEBHOOK_SYNC = None
        submit_module._RATE_LIMITER = None

    return _reset


@pytest.fixture
def app_factory(
    file_storage: FileStorage,
    settings_factory: Callable[..., Settings],
    reset_router_module_state: Callable[[], None],
) -> Callable[..., TestClient]:
    """Build a configured FastAPI TestClient.

    The factory mounts both routers, applies the requested ``Settings``,
    and wires ``app.dependency_overrides`` for storage / settings / rate
    limiter / github_sync. Reset of the router-module singletons is
    deferred to a finalizer so back-to-back factory invocations within a
    single test still see a clean slate.
    """
    clients: list[TestClient] = []

    def _factory(
        *,
        storage: Storage | None = None,
        settings: Settings | None = None,
        github_sync: Any = None,
        webhook_sync: Any = None,
        rate_limiter: Any = None,
        viewer_prefix: str = "/viewer",
    ) -> TestClient:
        chosen_storage = storage or file_storage
        chosen_settings = settings or settings_factory()
        app = FastAPI()
        app.include_router(submit_router)
        # The viewer router defines an empty-path route at root (the HTML
        # list view), which FastAPI rejects when mounted without a prefix.
        # Real consumers always pass ``prefix="/admin/bug-reports"`` (or
        # similar); tests follow the same pattern by default and re-export
        # the chosen prefix on the client for path-building convenience.
        app.include_router(viewer_router, prefix=viewer_prefix)
        app.dependency_overrides[get_storage] = lambda: chosen_storage
        app.dependency_overrides[get_settings] = lambda: chosen_settings
        app.dependency_overrides[get_github_sync] = lambda: github_sync
        app.dependency_overrides[get_webhook_sync] = lambda: webhook_sync
        app.dependency_overrides[get_rate_limiter] = lambda: rate_limiter
        client = TestClient(app)
        # Stash the prefix so tests can read it back without threading the
        # value through every helper signature.
        client.viewer_prefix = viewer_prefix  # type: ignore[attr-defined]
        clients.append(client)
        return client

    yield _factory

    # Tear down every client opened during the test, then reset router
    # singletons so they do not leak to the next test.
    for client in clients:
        client.close()
    reset_router_module_state()
