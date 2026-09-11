from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from itsdangerous import URLSafeTimedSerializer

from aptly_gui import i18n
from aptly_gui.aptly import AptlyClient, Mirror, Published, Snapshot, Storage
from aptly_gui.auth import COOKIE_NAME, OidcConfig, hash_password
from aptly_gui.auth.session import SALT
from aptly_gui.config import Settings
from aptly_gui.db import (
    User,
    UserRole,
    create_all,
    create_engine,
    create_session_factory,
)
from aptly_gui.web.app import create_app
from aptly_gui.web.state import AptlyState, CachedState

NOW = datetime.now(UTC)
SECRET = "test-secret-key"
CSRF = "test-csrf-token"


def _state(**overrides: Any) -> AptlyState:
    base: dict[str, Any] = {
        "fetched_at": NOW,
        "version": "1.6.1",
        "storage": Storage(total_mb=59360, free_mb=23383, percent_full=60.6),
        "mirrors": [
            Mirror(
                name="ubuntu-noble-noble-main",
                archive_url="http://archive.ubuntu.com/ubuntu",
                distribution="noble",
                components=["main"],
                architectures=["amd64"],
                filter=None,
                last_download=NOW,
            )
        ],
        "snapshots": [
            Snapshot(name="ubuntu-noble-noble-main-20260911T0800Z", created_at=NOW, description="")
        ],
        "published": [
            Published.parse(
                {
                    "Prefix": "ubuntu",
                    "Distribution": "noble",
                    "Architectures": ["amd64"],
                    "Sources": [
                        {"Component": "main", "Name": "ubuntu-noble-noble-main-20260911T0800Z"}
                    ],
                }
            )
        ],
    }
    return AptlyState(**{**base, **overrides})


class FakeCache:
    def __init__(self, cached: CachedState) -> None:
        self.cached = cached

    async def get(self, *, force: bool = False) -> CachedState:
        return self.cached


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, dict[str, Any]]] = []

    async def enqueue(
        self, job_type: str, mirror_set_id: int, *, author: str, params: dict[str, Any]
    ) -> Any:
        self.calls.append((job_type, mirror_set_id, params))
        return type("Job", (), {"id": len(self.calls)})()


@pytest.fixture
async def app_client(tmp_path: Path):
    engines = []

    async def build(
        cached: CachedState | None = None, role: str = UserRole.ADMIN
    ) -> tuple[httpx.AsyncClient, Any]:
        engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
        engines.append(engine)
        await create_all(engine)
        sessions = create_session_factory(engine)

        app = create_app(Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"))
        app.state.cache = FakeCache(cached or CachedState(state=_state(), stale=False, error=None))
        app.state.sessions = sessions
        app.state.language = "en"
        app.state.runner = FakeRunner()
        app.state.secret_key = SECRET
        app.state.session_max_age = 3600
        app.state.secure_cookies = False
        app.state.oidc_config = OidcConfig(issuer="", client_id="", client_secret="")
        app.state.oidc = None

        async with sessions() as session:
            user = User(
                username=f"tester-{role}",
                password_hash=hash_password("a-long-enough-password"),
                role=role,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
        app.state.has_users = True
        app.state.test_csrf = CSRF

        cookie = URLSafeTimedSerializer(SECRET, salt=SALT).dumps({"uid": user.id, "csrf": CSRF})
        transport = httpx.ASGITransport(app=app)
        client = httpx.AsyncClient(
            transport=transport, base_url="http://test", cookies={COOKIE_NAME: cookie}
        )
        return client, app

    yield build
    for engine in engines:
        await engine.dispose()
    i18n.activate("en")


# --- aptly client fixture (integration tests) -------------------------------

DEFAULT_API_URL = "http://127.0.0.1:8079"


@pytest.fixture
async def aptly() -> AsyncIterator[AptlyClient]:
    url = os.environ.get("APTLY_API_URL", DEFAULT_API_URL)
    client = AptlyClient(url)
    try:
        if not await client.is_ready():
            pytest.skip(f"no aptly API at {url} (start it with: cd demo && docker compose up -d)")
        yield client
    finally:
        await client.aclose()
