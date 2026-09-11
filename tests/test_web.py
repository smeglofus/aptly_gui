from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from aptly_gui import i18n
from aptly_gui.aptly import Mirror, Published, Snapshot, Storage
from aptly_gui.config import Settings
from aptly_gui.db import MirrorSet, create_all, create_engine, create_session_factory
from aptly_gui.web.app import create_app
from aptly_gui.web.state import AptlyState, CachedState

NOW = datetime.now(UTC)


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

    async def build(cached: CachedState | None = None) -> tuple[httpx.AsyncClient, Any]:
        engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
        engines.append(engine)
        await create_all(engine)
        sessions = create_session_factory(engine)

        app = create_app(Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"))
        app.state.cache = FakeCache(cached or CachedState(state=_state(), stale=False, error=None))
        app.state.sessions = sessions
        app.state.language = "en"
        app.state.runner = FakeRunner()
        transport = httpx.ASGITransport(app=app)
        return httpx.AsyncClient(transport=transport, base_url="http://test"), app

    yield build
    for engine in engines:
        await engine.dispose()
    i18n.activate("en")


@pytest.mark.parametrize(
    "path",
    ["/", "/sets", "/sets/adopt", "/mirrors", "/snapshots", "/publications", "/jobs", "/settings"],
)
async def test_pages_render(app_client, path: str) -> None:
    http, _ = await app_client()
    async with http:
        response = await http.get(path)
    assert response.status_code == 200
    assert "<title>" in response.text


async def test_unreachable_aptly_shows_stale_banner_not_empty_repo(app_client) -> None:
    """An outage must not read as 'there is nothing published'."""
    cached = CachedState(state=_state(), stale=True, error="Connection refused")
    http, _ = await app_client(cached)
    async with http:
        response = await http.get("/")
    assert "unreachable" in response.text
    assert "Connection refused" in response.text
    assert "ubuntu-noble-noble-main-20260911T0800Z" in response.text


async def test_missing_snapshot_is_reported_as_drift(app_client) -> None:
    cached = CachedState(state=_state(snapshots=[]), stale=False, error=None)
    http, _ = await app_client(cached)
    async with http:
        response = await http.get("/")
    assert "Drift" in response.text


async def test_healthz_reports_503_when_stale(app_client) -> None:
    cached = CachedState(state=_state(), stale=True, error="boom")
    http, _ = await app_client(cached)
    async with http:
        response = await http.get("/healthz")
    assert response.status_code == 503
    assert response.json()["aptly_reachable"] is False


async def test_adopt_page_proposes_the_existing_mirror(app_client) -> None:
    http, _ = await app_client()
    async with http:
        response = await http.get("/sets/adopt")
    assert 'value="ubuntu-noble"' in response.text
    assert "ubuntu-archive-keyring.gpg" in response.text


async def test_adopting_records_the_set_without_touching_aptly(app_client) -> None:
    http, app = await app_client()
    async with http:
        response = await http.post(
            "/sets/adopt",
            data={
                "name": "ubuntu-noble",
                "archive_url": "http://archive.ubuntu.com/ubuntu",
                "suites": "noble,noble-updates",
                "components": "main,universe",
                "architectures": "amd64",
                "publish_prefix": "ubuntu",
                "keyrings": "/usr/share/keyrings/ubuntu-archive-keyring.gpg",
                "signing_key": "key@example.invalid",
                "filter": "",
            },
            follow_redirects=False,
        )
    assert response.status_code == 303
    async with app.state.sessions() as session:
        stored = (await session.execute(select(MirrorSet))).scalars().all()
    assert len(stored) == 1
    assert stored[0].suites == ["noble", "noble-updates"]
    assert stored[0].adopted is True
    # No job was queued: adoption is bookkeeping, not an aptly operation.
    assert app.state.runner.calls == []


async def test_saving_language_switches_the_interface(app_client) -> None:
    http, app = await app_client()
    async with http:
        await http.post("/settings", data={"language": "cs"}, follow_redirects=False)
        response = await http.get("/")
    assert app.state.language == "cs"
    assert "Přehled" in response.text


async def test_unknown_language_is_rejected(app_client) -> None:
    http, app = await app_client()
    async with http:
        await http.post("/settings", data={"language": "klingon"}, follow_redirects=False)
    assert app.state.language == "en"
