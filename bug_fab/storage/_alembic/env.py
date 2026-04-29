"""Alembic environment for the Bug-Fab SQL storage backends.

Supports both online and offline migration runs. The target metadata is
``bug_fab.storage._models.Base.metadata`` so ``alembic revision --autogenerate``
picks up model changes.

The database URL is read from (in priority order):
1. The ``-x url=...`` command-line argument (``alembic -x url=sqlite:///foo.db ...``).
2. The ``BUG_FAB_DATABASE_URL`` environment variable.
3. The ``sqlalchemy.url`` value from ``alembic.ini``.
"""

from __future__ import annotations

import contextlib
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from bug_fab.storage._models import Base

# Alembic Config object — reads alembic.ini.
config = context.config

# Allow url override via -x or env var.
x_args = context.get_x_argument(as_dictionary=True)
override_url = x_args.get("url") or os.environ.get("BUG_FAB_DATABASE_URL")
if override_url:
    config.set_main_option("sqlalchemy.url", override_url)

# Configure Python logging from the .ini if a [loggers] section is present.
# Logging config is optional; skip silently if absent.
if config.config_file_name is not None:
    with contextlib.suppress(KeyError):
        fileConfig(config.config_file_name)

# Target metadata for autogenerate.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Generate SQL to stdout without connecting to a database."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=url.startswith("sqlite") if url else False,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Connect to the database and apply migrations."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        is_sqlite = connection.dialect.name == "sqlite"
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # ``render_as_batch`` enables ALTER TABLE rewrites for SQLite,
            # which lacks native support for many DDL operations.
            render_as_batch=is_sqlite,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
