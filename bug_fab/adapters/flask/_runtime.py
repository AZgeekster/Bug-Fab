"""Sync-to-async bridge plus static-bundle path resolution for the Flask adapter.

Flask is sync-by-default and the :class:`bug_fab.storage.Storage` ABC is
async. The adapter wraps every storage call in :func:`asyncio.run` per
request — a fresh event loop per call. That trade-off is intentional for
v0.1:

* Simple — no global loop lifecycle to manage, no thread-affinity
  surprises in WSGI workers (gunicorn pre-forks, gevent monkey-patches,
  uWSGI's many flavors of concurrency, etc.).
* Hot path is local file I/O for the default :class:`FileStorage`, so
  the per-request loop overhead is dwarfed by disk latency.
* Trivial to swap out — a future ``BUG_FAB_FLASK_RUNTIME=loop`` knob
  could pin a long-lived loop in a worker thread without changing the
  call sites here.

A consumer measuring real load that exposes loop-creation cost on the
flame graph should switch to a sync :class:`Storage` shim or to an
ASGI-native framework (FastAPI / Starlette).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from importlib import resources
from pathlib import Path
from typing import TypeVar

T = TypeVar("T")


def run_sync(awaitable: Awaitable[T]) -> T:
    """Drive an awaitable to completion on a fresh event loop.

    Thin wrapper around :func:`asyncio.run` that keeps every storage
    call site in :mod:`bug_fab.adapters.flask.blueprint` short and uniform.
    Each invocation creates and tears down its own loop — see this
    module's docstring for the rationale.
    """
    return asyncio.run(awaitable)


def resolve_static_dir() -> Path:
    """Locate the bundled ``bug_fab/static/`` directory on disk.

    Wheel installs ship the bundle at ``<site-packages>/bug_fab/static/``
    via ``[tool.hatch.build.targets.wheel.force-include]``. Editable
    installs (``pip install -e``) place it one directory above the
    package root at ``<repo>/static/``. This helper probes both layouts
    so the same blueprint runs in either.

    Falls back to :func:`importlib.resources.files` for the wheel case —
    that path also works with namespace packages and zip-imported
    distributions.
    """
    package_root = Path(resources.files("bug_fab"))
    candidates = [package_root / "static", package_root.parent / "static"]
    for candidate in candidates:
        if (candidate / "bug-fab.js").is_file():
            return candidate
    raise FileNotFoundError(
        "Could not locate the Bug-Fab static bundle. Tried: "
        + ", ".join(str(c) for c in candidates)
    )


def resolve_template_dir() -> Path:
    """Return the on-disk path of ``bug_fab/templates/``.

    Flask's Jinja loader differs from the FastAPI reference's; rather
    than duplicate the templates, the blueprint points
    ``template_folder=`` at the package's existing template directory.
    Works for both wheel and editable installs because the templates
    ship inside the package itself (no force-include shenanigans).
    """
    return Path(resources.files("bug_fab")) / "templates"
