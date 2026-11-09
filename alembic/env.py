"""Alembic environment for AllMemoryMCP.

Imports the ORM models (via db/memory_service) so the metadata is populated,
then runs online migrations against the DATABASE_URL configured for the app.
SQLite dev databases are created by db.initialize(); Alembic is the managed
path for PostgreSQL.
"""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Importing db registers every model on Base.metadata.
from db import Base, database_url  # noqa: E402
import memory_service  # noqa: F401  (ensures models + auth are loaded)

config = context.config
config.set_main_option("sqlalchemy.url", database_url())

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = database_url()
    connectable = engine_from_config(
        section, prefix="sqlalchemy.", poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
