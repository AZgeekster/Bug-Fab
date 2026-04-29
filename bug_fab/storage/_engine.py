"""SQLAlchemy engine + sessionmaker factory for the SQL storage backends.

Centralizes engine construction so every backend gets the same pragmas,
connect args, and pool tuning. SQLite specifically needs ``foreign_keys=ON``
(off by default) and benefits from WAL journal mode for read concurrency.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker


def _is_sqlite_url(url: str) -> bool:
    return url.startswith("sqlite:") or url.startswith("sqlite+")


def make_engine(url: str, **engine_kwargs: Any) -> Engine:
    """Create a SQLAlchemy ``Engine`` configured for Bug-Fab.

    For SQLite URLs, registers a ``connect`` listener that sets
    ``PRAGMA foreign_keys=ON`` and ``PRAGMA journal_mode=WAL`` on every new
    connection. Postgres URLs get no extra pragma handling — defaults are fine.

    Parameters
    ----------
    url:
        SQLAlchemy URL, e.g. ``sqlite:///path/to.db`` or
        ``postgresql+psycopg://user:pass@host/db``.
    engine_kwargs:
        Forwarded to ``create_engine``. Bug-Fab sets sensible defaults but
        callers can override (e.g., ``echo=True`` for debugging).
    """
    is_sqlite = _is_sqlite_url(url)

    # SQLite needs ``check_same_thread=False`` so we can use the connection
    # across the request handler thread and any background work. Concurrent
    # access is still serialized by the SQLAlchemy connection pool.
    if is_sqlite:
        engine_kwargs.setdefault("connect_args", {"check_same_thread": False})
        # ``future`` is the SQLAlchemy 2.0 default but pin it here so older
        # SQLAlchemy versions still get 2.0-style behavior.
        engine_kwargs.setdefault("future", True)

    engine = create_engine(url, **engine_kwargs)

    if is_sqlite:

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, connection_record):  # noqa: ARG001
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys = ON")
                cursor.execute("PRAGMA journal_mode = WAL")
                cursor.execute("PRAGMA synchronous = NORMAL")
            finally:
                cursor.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a ``sessionmaker`` bound to ``engine``.

    Returns a configured ``sessionmaker`` ready for ``with Session() as s:``
    usage. ``expire_on_commit=False`` keeps loaded ORM instances usable after
    commit, which is convenient for returning hydrated objects from storage
    methods.
    """
    return sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )
