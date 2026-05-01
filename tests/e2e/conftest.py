"""E2E test fixtures: subprocess-boot uvicorn against ``_app:app``.

The fixtures are scoped to the module so each test gets a fresh storage
directory but multiple smoke checks within one module share a single
server. Playwright's ``page`` / ``browser`` fixtures come from
``pytest-playwright`` and don't need re-declaring here.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def app_server(tmp_path_factory):
    here = Path(__file__).resolve().parent
    storage_dir = tmp_path_factory.mktemp("e2e-storage")
    port = _free_port()

    env = os.environ.copy()
    env["BUG_FAB_E2E_STORAGE_DIR"] = str(storage_dir)
    # PYTHONPATH so uvicorn can import _app from this dir.
    env["PYTHONPATH"] = str(here) + os.pathsep + env.get("PYTHONPATH", "")

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "_app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=str(here),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 20
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            output = (proc.stdout.read() if proc.stdout else b"").decode(errors="replace")
            raise RuntimeError(f"uvicorn exited early (rc={proc.returncode}); output:\n{output}")
        try:
            r = httpx.get(base_url + "/", timeout=1.0)
            if r.status_code == 200:
                break
        except Exception as exc:
            last_err = exc
        time.sleep(0.2)
    else:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        raise RuntimeError(f"server did not come up within 20s; last error: {last_err!r}")

    yield {"base_url": base_url, "storage_dir": storage_dir}

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
