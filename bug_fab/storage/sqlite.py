"""SQLite storage backend.

Use this when the consumer wants queryable metadata without running a separate
DB server. Screenshots remain on disk at ``screenshot_dir``; only the path is
indexed by the database.

Install via ``pip install bug-fab[sqlite]``.
"""

from __future__ import annotations

from pathlib import Path

from ._engine import make_engine
from ._sql_base import SqlStorageBase


class SQLiteStorage(SqlStorageBase):
    """SQLite-backed storage.

    Parameters
    ----------
    db_path:
        Filesystem path to the SQLite database file. The parent directory is
        created if it does not exist. A relative path is resolved against the
        current working directory.
    screenshot_dir:
        Directory where screenshot PNG files are written. Created if missing.
    """

    supports_returning = True  # SQLite >= 3.35

    def __init__(self, db_path: Path | str, screenshot_dir: Path | str) -> None:
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path

        # SQLAlchemy URL. Use ``sqlite:///`` (three slashes) for an absolute
        # path on POSIX; on Windows the ``as_posix`` form keeps drive letters
        # intact (``sqlite:///C:/path/to.db``).
        url = f"sqlite:///{db_path.as_posix()}"
        engine = make_engine(url)

        super().__init__(engine=engine, screenshot_dir=Path(screenshot_dir))
