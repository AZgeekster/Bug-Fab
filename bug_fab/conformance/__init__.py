"""Bug-Fab wire-protocol conformance test plugin.

This package ships a pytest plugin (`bug-fab-conformance`) plus a battery of
HTTP-level tests that any adapter claiming to implement Bug-Fab v0.1 must
pass. It is registered as a `pytest11` entry-point in `pyproject.toml`, so
adapter authors can simply `pip install bug-fab` and then run::

    pytest --bug-fab-conformance --base-url=https://my-app.example.com/bug-fab

against their adapter's live URL.

The `fixtures` module exposes payload helpers (`make_test_png`,
`make_test_metadata`, etc.) for use both inside this package's own tests and
by adapter authors who want to write their own protocol-edge tests.

See `bug_fab/conformance/README.md` for the user-facing usage doc.
"""

from bug_fab.conformance.fixtures import (
    make_invalid_severity_metadata,
    make_legacy_status_payload,
    make_test_metadata,
    make_test_png,
)

__all__ = [
    "make_invalid_severity_metadata",
    "make_legacy_status_payload",
    "make_test_metadata",
    "make_test_png",
]
