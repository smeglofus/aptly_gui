"""The single local account is configuration, not data.

It is reconciled on every start so a password is rotated by redeploying, and so no
local credential is ever written into the image.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from aptly_gui.auth import verify_password
from aptly_gui.config import Settings
from aptly_gui.db import User, UserRole, create_all, create_engine, create_session_factory
from aptly_gui.web.app import _reconcile_admin


@pytest.fixture
async def sessions(tmp_path: Path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'admin.db'}")
    await create_all(engine)
    yield create_session_factory(engine)
    await engine.dispose()


def _settings(username: str = "admin", password: str = "heslo123") -> Settings:
    return Settings(admin_username=username, admin_password=password)


async def _load(sessions, username: str) -> User | None:
    async with sessions() as session:
        return (
            await session.execute(select(User).where(User.username == username))
        ).scalar_one_or_none()


async def test_creates_the_account_on_first_start(sessions) -> None:
    await _reconcile_admin(sessions, _settings())
    user = await _load(sessions, "admin")
    assert user is not None
    assert user.role == UserRole.ADMIN
    assert user.is_local
    assert verify_password(user.password_hash, "heslo123")


async def test_configured_password_may_be_shorter_than_the_ui_minimum(sessions) -> None:
    """The length rule guards what people type; configuration is a deliberate choice."""
    await _reconcile_admin(sessions, _settings(password="heslo123"))
    user = await _load(sessions, "admin")
    assert user is not None
    assert verify_password(user.password_hash, "heslo123")


async def test_rotating_the_password_takes_effect_on_restart(sessions) -> None:
    await _reconcile_admin(sessions, _settings())
    await _reconcile_admin(sessions, _settings(password="a-different-one"))
    user = await _load(sessions, "admin")
    assert user is not None
    assert verify_password(user.password_hash, "a-different-one")
    assert not verify_password(user.password_hash, "heslo123")


async def test_configuration_wins_over_edits_made_by_hand(sessions) -> None:
    await _reconcile_admin(sessions, _settings())
    async with sessions() as session:
        user = (
            await session.execute(select(User).where(User.username == "admin"))
        ).scalar_one_or_none()
        assert user is not None
        user.role = UserRole.VIEWER
        user.active = False
        await session.commit()

    await _reconcile_admin(sessions, _settings())

    user = await _load(sessions, "admin")
    assert user is not None
    assert user.role == UserRole.ADMIN
    assert user.active is True


async def test_without_configuration_no_account_is_invented(sessions) -> None:
    await _reconcile_admin(sessions, Settings())
    async with sessions() as session:
        assert (await session.execute(select(User))).scalars().all() == []


async def test_half_configured_is_ignored(sessions) -> None:
    """A username with no password must not create an account nobody can sign in to."""
    await _reconcile_admin(sessions, Settings(admin_username="admin"))
    async with sessions() as session:
        assert (await session.execute(select(User))).scalars().all() == []


async def test_oidc_accounts_are_left_alone(sessions) -> None:
    async with sessions() as session:
        session.add(User(username="jana", role=UserRole.OPERATOR, source="oidc"))
        await session.commit()

    await _reconcile_admin(sessions, _settings())

    jana = await _load(sessions, "jana")
    assert jana is not None
    assert jana.source == "oidc"
    assert jana.role == UserRole.OPERATOR
    assert jana.password_hash is None
