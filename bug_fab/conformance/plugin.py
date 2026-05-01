"""Pytest plugin entry-point for `bug-fab-conformance`.

Registered via `pyproject.toml`::

    [project.entry-points.pytest11]
    bug-fab-conformance = "bug_fab.conformance.plugin"

When the user passes `--bug-fab-conformance` on the pytest command line, the
plugin:

1. Adds the bundled `bug_fab/conformance/tests/` directory to test discovery
   so adapter authors do not need to copy or re-author the suite.
2. Restricts collection to the conformance tests (and any `tests/conformance/
   test_*.py` files in the consumer repo) — adapter-internal unit tests are
   not collected when conformance mode is active.
3. Exposes a `conformance_client` fixture: an `httpx.Client` already
   pre-pointed at the URL passed via `--base-url`, with the optional
   `--auth-header` applied as a default header.

Usage from an adapter author's perspective::

    pip install bug-fab
    pytest --bug-fab-conformance --base-url=http://localhost:8000/bug-fab

A non-zero exit code means the adapter is non-conformant; the failing test
identifies the protocol clause that was violated.
"""

from __future__ import annotations

from importlib.util import find_spec
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator


CONFORMANCE_TESTS_DIR = Path(__file__).parent / "tests"

# `pytest-base-url` registers `--base-url` itself. When it is installed
# (transitively, via `pytest-playwright` for instance) we let it own the
# option — both plugins want the same string value, so deferring is safe
# and avoids an `argparse.ArgumentError` on duplicate registration.
_PYTEST_BASE_URL_INSTALLED = find_spec("pytest_base_url") is not None


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register the plugin's CLI flags.

    All flags are scoped under a `bug-fab` option group so `pytest --help`
    shows them together and they do not pollute the top-level help screen.
    """
    group = parser.getgroup("bug-fab", "Bug-Fab conformance tests")
    group.addoption(
        "--bug-fab-conformance",
        action="store_true",
        default=False,
        help="Run only the bundled bug-fab wire-protocol conformance tests.",
    )
    if not _PYTEST_BASE_URL_INSTALLED:
        group.addoption(
            "--base-url",
            action="store",
            default=None,
            help=(
                "Base URL of the adapter's INTAKE endpoint (e.g. http://localhost:8000/api). "
                "The conformance suite appends `/bug-reports` to this. "
                "Required when --bug-fab-conformance is set."
            ),
        )
    group.addoption(
        "--viewer-base-url",
        action="store",
        default=None,
        help=(
            "Base URL of the adapter's VIEWER endpoints (e.g. http://localhost:8000/admin/bug-reports). "
            "The conformance suite appends `/reports`, `/reports/{id}`, `/bulk-close-fixed`, etc. "
            "Defaults to --base-url when not set (for adapters that mount intake + viewer "
            "under one prefix). Set explicitly for split-mount adapters where intake is open "
            "and viewer is auth-gated under different URL prefixes — the documented best practice."
        ),
    )
    group.addoption(
        "--auth-header",
        action="store",
        default=None,
        help=(
            "Optional HTTP header (format: 'Name: value') sent with every conformance "
            "request. Use for adapters that protect intake/viewer routes behind auth."
        ),
    )


def pytest_configure(config: pytest.Config) -> None:
    """Register the `conformance` marker so `--strict-markers` does not error.

    WHY register here (not just in pyproject.toml): adapter consumers that
    invoke this plugin from a project without the marker pre-registered
    would fail collection under strict-markers mode otherwise.
    """
    config.addinivalue_line(
        "markers",
        "conformance: bug-fab wire-protocol conformance test (auto-applied to bundled suite)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Apply the `conformance` marker to bundled tests and gate non-conformance.

    When `--bug-fab-conformance` is active, deselect everything outside the
    bundled `bug_fab/conformance/tests/` tree so adapter-internal unit tests
    do not run alongside the protocol checks. When the flag is inactive,
    deselect the bundled conformance tests so a normal `pytest` invocation
    in an adapter's repo does not blow up trying to reach an unset
    `--base-url`.
    """
    conformance_active = config.getoption("--bug-fab-conformance")
    bundled_tests_str = str(CONFORMANCE_TESTS_DIR.resolve())

    selected: list[pytest.Item] = []
    deselected: list[pytest.Item] = []

    for item in items:
        item_path = str(Path(str(item.fspath)).resolve())
        is_bundled_conformance = item_path.startswith(bundled_tests_str)

        if is_bundled_conformance:
            item.add_marker(pytest.mark.conformance)

        if conformance_active:
            if is_bundled_conformance:
                selected.append(item)
            else:
                deselected.append(item)
        else:
            if is_bundled_conformance:
                deselected.append(item)
            else:
                selected.append(item)

    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = selected


def pytest_collect_file(parent: pytest.Collector, file_path: Path) -> pytest.Collector | None:
    """Hook for future file-based collection extension.

    Currently a no-op — pytest's default collection of `tests/conformance/
    test_*.py` files in the consumer repo plus this plugin's bundled tests
    covers all needed paths. Reserved for v0.2 when language-neutral fixture
    files (e.g., `*.json` payloads) become first-class.
    """
    return None


def pytest_collection(session: pytest.Session) -> None:
    """Inject the bundled conformance-test directory into the collection set.

    Without this, pytest would only collect the consumer's own `tests/`
    directory and would never find the conformance suite shipped inside the
    installed `bug_fab` package.
    """
    if not session.config.getoption("--bug-fab-conformance"):
        return

    bundled = str(CONFORMANCE_TESTS_DIR)
    if bundled not in session.config.args:
        session.config.args.append(bundled)


def _parse_auth_header(raw: str | None) -> dict[str, str]:
    """Split a `Name: value` auth-header argument into an httpx-friendly dict.

    Returns an empty dict on `None` so callers can spread it unconditionally.
    Whitespace around the colon and value is tolerated; missing colon raises
    a clear error rather than silently dropping the header.
    """
    if raw is None:
        return {}
    if ":" not in raw:
        raise pytest.UsageError(f"--auth-header must be 'Name: value' (got: {raw!r})")
    name, _, value = raw.partition(":")
    return {name.strip(): value.strip()}


@pytest.fixture(scope="session")
def conformance_base_url(request: pytest.FixtureRequest) -> str:
    """Yield the validated `--base-url`, failing fast if it is missing.

    This is the INTAKE base URL — `/bug-reports` paths resolve against it.
    Viewer tests use `conformance_viewer_base_url` instead, which defaults
    to this value when `--viewer-base-url` is not set.
    """
    base_url = request.config.getoption("--base-url")
    if not base_url:
        pytest.fail(
            "--base-url is required when running conformance tests. "
            "Example: pytest --bug-fab-conformance --base-url=http://localhost:8000/api"
        )
    return base_url.rstrip("/")


@pytest.fixture(scope="session")
def conformance_viewer_base_url(
    request: pytest.FixtureRequest,
    conformance_base_url: str,
) -> str:
    """Yield the validated `--viewer-base-url`, defaulting to `--base-url`.

    Real adapters typically split intake (open submit, mounted at `/api`)
    from viewer (auth-gated, mounted at `/admin/bug-reports` or similar).
    This fixture lets the suite address each independently. When the
    adapter mounts both at the same prefix, `--viewer-base-url` can be
    omitted.
    """
    viewer_url = request.config.getoption("--viewer-base-url")
    return (viewer_url or conformance_base_url).rstrip("/")


@pytest.fixture(scope="session")
def conformance_auth_headers(
    request: pytest.FixtureRequest,
) -> dict[str, str]:
    """Yield the parsed `--auth-header` as a dict (empty when not provided)."""
    return _parse_auth_header(request.config.getoption("--auth-header"))


@pytest.fixture(scope="session")
def conformance_client(
    conformance_base_url: str,
    conformance_auth_headers: dict[str, str],
) -> Iterator[httpx.Client]:
    """Yield an `httpx.Client` pointed at the adapter's INTAKE base URL.

    Use for `POST /bug-reports` calls. Use `conformance_viewer_client`
    for any `/reports`, `/reports/{id}`, or `/bulk-*` path.

    Session-scoped so each test reuses the same TCP connection pool —
    keeps the suite fast against a slow adapter and avoids per-test
    connection-tear-down churn.
    """
    with httpx.Client(
        base_url=conformance_base_url,
        headers=conformance_auth_headers,
        timeout=30.0,
        follow_redirects=False,
    ) as client:
        yield client


@pytest.fixture(scope="session")
def conformance_viewer_client(
    conformance_viewer_base_url: str,
    conformance_auth_headers: dict[str, str],
) -> Iterator[httpx.Client]:
    """Yield an `httpx.Client` pointed at the adapter's VIEWER base URL.

    Use for `GET /reports`, `GET /reports/{id}`, `PUT /reports/{id}/status`,
    `DELETE /reports/{id}`, `GET /reports/{id}/screenshot`, and the bulk
    operation paths.
    """
    with httpx.Client(
        base_url=conformance_viewer_base_url,
        headers=conformance_auth_headers,
        timeout=30.0,
        follow_redirects=False,
    ) as client:
        yield client
