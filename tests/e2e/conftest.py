"""E2E test fixtures: subprocess-boot uvicorn servers for the smoke tests.

One shared boot helper (:func:`_uvicorn_server`) backs both fixtures — the
harness app used by ``test_smoke.py`` and the real error-playground example
used by ``test_error_playground.py``. The playground module used to carry
its own copy of the boot loop, and the copy had dropped the ``last_err``
capture: a failed boot there reported a bare "did not come up within 20s"
with no diagnostic while the original reported the actual exception.

Playwright's ``page`` / ``browser`` fixtures come from ``pytest-playwright``
and don't need re-declaring here.
"""

from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def _uvicorn_server(
    app_spec: str,
    *,
    cwd: Path,
    env_overrides: dict[str, str],
) -> Iterator[str]:
    """Boot ``uvicorn <app_spec>`` on a free loopback port; yield the base URL.

    Polls ``GET /`` until 200 with a 20 s deadline. A server that exits
    early raises with its captured output; a server that never answers
    raises with the last connection error (``last_err``) so a boot failure
    is diagnosable instead of a bare timeout.
    """
    port = _free_port()
    env = os.environ.copy()
    env.update(env_overrides)

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            app_spec,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    base_url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 20
        last_err: Exception | None = None
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                output = (proc.stdout.read() if proc.stdout else b"").decode(errors="replace")
                raise RuntimeError(
                    f"uvicorn exited early (rc={proc.returncode}); output:\n{output}"
                )
            try:
                r = httpx.get(base_url + "/", timeout=1.0)
                if r.status_code == 200:
                    break
            except Exception as exc:
                last_err = exc
            time.sleep(0.2)
        else:
            raise RuntimeError(f"server did not come up within 20s; last error: {last_err!r}")

        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="module")
def app_server(tmp_path_factory):
    """The module-scoped smoke-test harness app (``tests/e2e/_app.py``)."""
    here = Path(__file__).resolve().parent
    storage_dir = tmp_path_factory.mktemp("e2e-storage")
    with _uvicorn_server(
        "_app:app",
        cwd=here,
        env_overrides={
            "BUG_FAB_E2E_STORAGE_DIR": str(storage_dir),
            # PYTHONPATH so uvicorn can import _app from this dir.
            "PYTHONPATH": str(here) + os.pathsep + os.environ.get("PYTHONPATH", ""),
        },
    ) as base_url:
        yield {"base_url": base_url, "storage_dir": storage_dir}


@pytest.fixture(scope="function")
def playground_server(tmp_path_factory):
    """Boot the real ``examples/error-playground/main:app`` per test."""
    repo_root = Path(__file__).resolve().parents[2]
    example_dir = repo_root / "examples" / "error-playground"
    storage_dir = tmp_path_factory.mktemp("ep-storage")
    with _uvicorn_server(
        "main:app",
        cwd=example_dir,
        env_overrides={
            "BUG_FAB_STORAGE_DIR": str(storage_dir),
            "PYTHONPATH": str(repo_root) + os.pathsep + os.environ.get("PYTHONPATH", ""),
        },
    ) as base_url:
        yield {"base_url": base_url, "storage_dir": storage_dir}
