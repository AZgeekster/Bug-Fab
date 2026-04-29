"""PostgreSQL storage backend.

Use this when the consumer already operates a Postgres cluster and wants
production-grade queryable metadata. Screenshots remain on disk at
``screenshot_dir``; only the path is indexed by the database.

Install via ``pip install bug-fab[postgres]``.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Sequence, text
from sqlalchemy.orm import Session

from ._engine import make_engine
from ._sql_base import SqlStorageBase

#: Name of the Postgres SEQUENCE used for ``bug-NNN`` ID generation. The
#: initial Alembic migration creates this; ``create_all`` (the test path)
#: also ensures it exists.
BUG_REPORT_ID_SEQUENCE = Sequence("bug_report_id_seq", start=1, increment=1)


class PostgresStorage(SqlStorageBase):
    """Postgres-backed storage.

    Parameters
    ----------
    dsn:
        SQLAlchemy URL for the target Postgres database, e.g.
        ``postgresql+psycopg://user:pass@host:5432/dbname``. Both ``psycopg``
        (v3) and ``psycopg2`` drivers work; ``[postgres]`` extra installs
        ``psycopg``.
    screenshot_dir:
        Directory where screenshot PNG files are written. Created if missing.
    """

    supports_returning = True

    def __init__(self, dsn: str, screenshot_dir: Path | str) -> None:
        self.dsn = dsn
        engine = make_engine(dsn)
        super().__init__(engine=engine, screenshot_dir=Path(screenshot_dir))

    def create_all(self) -> None:
        """Create tables AND the bug-report ID sequence in one shot."""
        super().create_all()
        with self.engine.begin() as conn:
            conn.execute(
                text("CREATE SEQUENCE IF NOT EXISTS bug_report_id_seq START 1 INCREMENT 1")
            )

    def _next_id_value(self, session: Session) -> int:
        """Pop the next value from the Postgres sequence.

        Postgres sequences are non-transactional — gaps are possible if a
        transaction rolls back, which is fine for our human-readable IDs.
        """
        return int(session.execute(BUG_REPORT_ID_SEQUENCE.next_value()).scalar_one())
