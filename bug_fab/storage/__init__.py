"""Storage backends for Bug-Fab.

`Storage` (the ABC) and `FileStorage` (the zero-dep default) are eagerly
exported. SQL backends import lazily inside `__getattr__` so SQLAlchemy
stays an optional install (`pip install bug-fab[sqlite]` or `[postgres]`).
"""

from __future__ import annotations

from typing import Any

from bug_fab.storage.base import Storage
from bug_fab.storage.files import FileStorage

__all__ = ["FileStorage", "Storage", "SQLiteStorage", "PostgresStorage"]


def __getattr__(name: str) -> Any:
    """Lazy-import SQL backends so SQLAlchemy remains optional."""
    if name == "SQLiteStorage":
        from bug_fab.storage.sqlite import SQLiteStorage

        return SQLiteStorage
    if name == "PostgresStorage":
        from bug_fab.storage.postgres import PostgresStorage

        return PostgresStorage
    raise AttributeError(f"module 'bug_fab.storage' has no attribute {name!r}")
