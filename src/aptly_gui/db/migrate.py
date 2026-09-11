from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Connection, inspect
from sqlalchemy.ext.asyncio import AsyncEngine

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

# Databases that carry no revision were built from the models rather than migrated.
# Each entry says: if this table, or this column in it, is present then the schema has
# already reached that revision. Oldest first.
#
# Extend this whenever a migration adds a table or column — test_migrations.py fails
# if head stops being detectable, which is what stops the list going stale.
ADOPTION_MARKERS: list[tuple[str, str, str | None]] = [
    ("acaffe887f68", "mirror_sets", None),
    ("1d98f4a90784", "job_steps", "total_bytes"),
    ("2595a4d32cad", "jobs", "expected_steps"),
    ("f8cd439d47f7", "users", None),
    ("a2df56007c76", "mirror_sets", "mirror_pattern"),
]


def _config(connection: Connection) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    # env.py reuses this connection instead of opening its own, which is what lets
    # the upgrade run inside the application's own async engine.
    config.attributes["connection"] = connection
    return config


def detect_revision(connection: Connection) -> str | None:
    """Work out which revision an unstamped schema already matches."""
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    matched: str | None = None
    for revision, table, column in ADOPTION_MARKERS:
        if table not in tables:
            break
        if column is not None:
            columns = {info["name"] for info in inspector.get_columns(table)}
            if column not in columns:
                break
        matched = revision
    return matched


def _prepare_and_upgrade(connection: Connection) -> None:
    if MigrationContext.configure(connection).get_current_revision() is None:
        adopted = detect_revision(connection)
        if adopted is not None:
            command.stamp(_config(connection), adopted)
    command.upgrade(_config(connection), "head")


async def upgrade_database(engine: AsyncEngine) -> None:
    """Bring the schema to head on startup.

    Creating tables from the models would leave an existing database missing any
    column added since it was created, so upgrades have to go through Alembic.
    """
    async with engine.begin() as connection:
        await connection.run_sync(_prepare_and_upgrade)


async def current_revision(engine: AsyncEngine) -> str | None:
    async with engine.connect() as connection:
        return await connection.run_sync(
            lambda sync_conn: MigrationContext.configure(sync_conn).get_current_revision()
        )
