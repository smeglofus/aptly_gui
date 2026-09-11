"""Upgrading an existing database must not need a wipe.

Creating tables from the models silently skips columns added to a table that already
exists, which is how a deployment started failing with "no such column".
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select, text

from aptly_gui.db import (
    Job,
    MirrorSet,
    create_engine,
    create_session_factory,
    current_revision,
    upgrade_database,
)

FIRST_REVISION = "acaffe887f68"


async def _columns(engine, table: str) -> set[str]:
    async with engine.connect() as connection:
        rows = await connection.execute(text(f"PRAGMA table_info({table})"))
        return {row[1] for row in rows}


@pytest.fixture
async def engine(tmp_path: Path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'migrate.db'}")
    yield engine
    await engine.dispose()


async def test_fresh_database_reaches_head(engine) -> None:
    await upgrade_database(engine)
    assert await current_revision(engine) is not None
    assert "expected_steps" in await _columns(engine, "jobs")
    assert "total_bytes" in await _columns(engine, "job_steps")


async def test_upgrade_adds_columns_to_an_existing_database(engine, tmp_path: Path) -> None:
    from alembic import command
    from alembic.config import Config

    from aptly_gui.db.migrate import MIGRATIONS_DIR

    # Start at the schema as it shipped before progress tracking existed.
    def stamp_old(sync_conn):
        config = Config()
        config.set_main_option("script_location", str(MIGRATIONS_DIR))
        config.attributes["connection"] = sync_conn
        command.upgrade(config, FIRST_REVISION)

    async with engine.begin() as connection:
        await connection.run_sync(stamp_old)

    assert "expected_steps" not in await _columns(engine, "jobs")

    await upgrade_database(engine)

    assert "expected_steps" in await _columns(engine, "jobs")
    assert {"total_bytes", "remaining_bytes", "total_packages", "remaining_packages"} <= (
        await _columns(engine, "job_steps")
    )


async def test_existing_rows_survive_the_upgrade(engine) -> None:
    from alembic import command

    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_conn: command.upgrade(_old_config(sync_conn), FIRST_REVISION)
        )

    # Written as raw SQL: the ORM models already know about the new columns, so they
    # could not have produced the rows a real old deployment left behind.
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO mirror_sets "
                "(id, name, archive_url, suites, components, architectures, keyrings, "
                " publish_prefix, adopted, revision, created_at, updated_at) "
                "VALUES (1, 'ubuntu-noble', 'http://archive.ubuntu.com/ubuntu', "
                " '[\"noble\"]', '[\"main\"]', '[\"amd64\"]', '[]', 'ubuntu', 1, 1, "
                " '2026-09-01 10:00:00', '2026-09-01 10:00:00')"
            )
        )
        await connection.execute(
            text(
                "INSERT INTO jobs (id, type, mirror_set_id, state, author, created_at, params) "
                "VALUES (1, 'update', 1, 'succeeded', 'someone', '2026-09-01 10:00:00', '{}')"
            )
        )

    await upgrade_database(engine)

    sessions = create_session_factory(engine)
    async with sessions() as session:
        jobs = (await session.execute(select(Job))).scalars().all()
        sets = (await session.execute(select(MirrorSet))).scalars().all()

    assert [job.author for job in jobs] == ["someone"]
    assert [item.name for item in sets] == ["ubuntu-noble"]
    # The new column exists and is simply empty for rows that predate it.
    assert jobs[0].expected_steps is None


def _old_config(sync_conn):
    from alembic.config import Config

    from aptly_gui.db.migrate import MIGRATIONS_DIR

    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.attributes["connection"] = sync_conn
    return config


async def test_database_built_before_migrations_is_adopted(engine) -> None:
    """The first deployments created tables from the models and recorded no revision.

    Upgrading those must adopt them at the baseline instead of replaying a create
    that would collide with the tables already there.
    """
    from sqlalchemy import inspect

    from aptly_gui.db import create_all

    await create_all(engine)

    async def table_names() -> set[str]:
        async with engine.connect() as connection:
            return await connection.run_sync(
                lambda sync_conn: set(inspect(sync_conn).get_table_names())
            )

    assert "mirror_sets" in await table_names()
    assert "alembic_version" not in await table_names()

    # Without adoption this raises "table mirror_sets already exists".
    await upgrade_database(engine)

    assert await current_revision(engine) is not None
    assert "expected_steps" in await _columns(engine, "jobs")


async def test_head_is_detectable_from_the_schema_alone(engine) -> None:
    """Guards the adoption marker list against going stale.

    A migration that adds a table or column without a marker would make an unstamped
    database adopt at the wrong revision and then collide on upgrade.
    """
    from sqlalchemy import inspect

    from aptly_gui.db import create_all
    from aptly_gui.db.migrate import MIGRATIONS_DIR, detect_revision

    await create_all(engine)
    async with engine.connect() as connection:
        detected = await connection.run_sync(detect_revision)

    from alembic.script import ScriptDirectory

    script = ScriptDirectory(str(MIGRATIONS_DIR))
    assert detected == script.get_current_head(), (
        "add an entry to ADOPTION_MARKERS for the newest migration"
    )
    async with engine.connect() as connection:
        tables = await connection.run_sync(
            lambda sync_conn: set(inspect(sync_conn).get_table_names())
        )
    assert "users" in tables
