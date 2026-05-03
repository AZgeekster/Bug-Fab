"""Regression test for storage backend `__init__` signatures.

A 2026-05-03 consumer-integration audit flagged a doc-vs-code drift in
`docs/DEPLOYMENT_OPTIONS.md`: the `SQLiteStorage` example claimed the
parameter was `db_url` but the shipped code's parameter is `db_path`.
The same kind of drift is easy to reintroduce — anyone refactoring a
backend's `__init__` (e.g., to support a connection-string union type)
could silently break the documented examples again.

This module fails fast on any such drift by inspecting the actual
parameter names of every shipped storage backend's `__init__`. If the
audit-confirmed names ever change, the tests fail loudly and the doc
update becomes part of the same PR as the code change.

The audit-confirmed parameter sets are pinned here as canonical:

- `FileStorage(storage_dir, id_prefix="")` — both documented in
  `docs/PROTOCOL.md` examples and `docs/INSTALLATION.md`.
- `SQLiteStorage(db_path, screenshot_dir)` — `docs/DEPLOYMENT_OPTIONS.md`
  was wrong about this until 2026-05-03; pinned here so it stays right.
- `PostgresStorage(dsn, screenshot_dir)` — same, was `db_url=` in the
  pre-2026-05-03 doc; pinned here as `dsn`.
"""

from __future__ import annotations

import inspect

import pytest


@pytest.mark.parametrize(
    "import_path, expected_params",
    [
        ("bug_fab.storage.files.FileStorage", ("storage_dir", "id_prefix")),
        ("bug_fab.storage.sqlite.SQLiteStorage", ("db_path", "screenshot_dir")),
        ("bug_fab.storage.postgres.PostgresStorage", ("dsn", "screenshot_dir")),
    ],
)
def test_storage_init_param_names_match_documented_signature(
    import_path: str, expected_params: tuple[str, ...]
) -> None:
    """The storage backend's __init__ parameters must match the documented names.

    A failing test here means either:
      (a) the code was refactored without updating the docs/examples that
          name these parameters, or
      (b) the docs were rewritten in a way that no longer matches the code.

    Either way, the fix is to bring docs and code back into agreement —
    NOT to change this test. If you're tempted to flip the expected names,
    sweep `docs/DEPLOYMENT_OPTIONS.md`, `docs/INSTALLATION.md`,
    `INTEGRATION_AGENTS.md`, and `examples/**/*.py` for the old names
    first.
    """
    module_path, _, class_name = import_path.rpartition(".")
    try:
        module = __import__(module_path, fromlist=[class_name])
    except ImportError as exc:
        pytest.skip(f"{import_path} not importable in this env: {exc}")
    cls = getattr(module, class_name)

    sig = inspect.signature(cls.__init__)
    actual_params = tuple(p for p in sig.parameters if p != "self")

    assert actual_params == expected_params, (
        f"{import_path}.__init__ signature drift: docs claim "
        f"{expected_params}, code has {actual_params}. "
        "Update docs OR test (NOT both at once) and review every example."
    )
