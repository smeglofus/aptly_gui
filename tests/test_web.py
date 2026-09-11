from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest

from aptly_gui.aptly import Mirror, Published, Snapshot, Storage
from aptly_gui.config import Settings
from aptly_gui.web.app import create_app
from aptly_gui.web.state import AptlyState, CachedState

NOW = datetime.now(UTC)


def _state(**overrides: object) -> AptlyState:
    base = {
        "fetched_at": NOW,
        "version": "1.6.1",
        "storage": Storage(total_mb=59360, free_mb=23383, percent_full=60.6),
        "mirrors": [
            Mirror(
                name="ubuntu-noble-main",
                archive_url="http://archive.ubuntu.com/ubuntu",
                distribution="noble",
                components=["main"],
                architectures=["amd64"],
                filter=None,
                last_download=NOW,
            )
        ],
        "snapshots": [
            Snapshot(name="ubuntu-noble-main-20260911T0800Z", created_at=NOW, description="")
        ],
        "published": [
            Published.parse(
                {
                    "Prefix": "ubuntu",
                    "Distribution": "noble",
                    "Architectures": ["amd64"],
                    "Sources": [{"Component": "main", "Name": "ubuntu-noble-main-20260911T0800Z"}],
                }
            )
        ],
    }
    return AptlyState(**{**base, **overrides})  # type: ignore[arg-type]


class FakeCache:
    def __init__(self, cached: CachedState) -> None:
        self.cached = cached

    async def get(self, *, force: bool = False) -> CachedState:
        return self.cached


@pytest.fixture
async def client_factory():
    async def build(cached: CachedState) -> AsyncIterator[httpx.AsyncClient]:
        app = create_app(Settings())
        app.state.cache = FakeCache(cached)
        transport = httpx.ASGITransport(app=app)
        return httpx.AsyncClient(transport=transport, base_url="http://test")

    return build


async def test_dashboard_lists_published_snapshots(client_factory) -> None:
    cached = CachedState(state=_state(), stale=False, error=None)
    async with await client_factory(cached) as http:
        response = await http.get("/")
    assert response.status_code == 200
    assert "ubuntu-noble-main-20260911T0800Z" in response.text
    assert "1.6.1" in response.text


async def test_unreachable_aptly_shows_stale_banner_not_empty_repo(client_factory) -> None:
    """An outage must not read as 'there is nothing published'."""
    cached = CachedState(state=_state(), stale=True, error="Connection refused")
    async with await client_factory(cached) as http:
        response = await http.get("/")
    assert "nedostupné" in response.text
    assert "Connection refused" in response.text
    # The last good data is still on the page.
    assert "ubuntu-noble-main-20260911T0800Z" in response.text


async def test_missing_snapshot_is_reported_as_drift(client_factory) -> None:
    cached = CachedState(state=_state(snapshots=[]), stale=False, error=None)
    async with await client_factory(cached) as http:
        response = await http.get("/")
    assert "Drift" in response.text
    assert "chybí" in response.text


async def test_healthz_reports_503_when_stale(client_factory) -> None:
    cached = CachedState(state=_state(), stale=True, error="boom")
    async with await client_factory(cached) as http:
        response = await http.get("/healthz")
    assert response.status_code == 503
    assert response.json()["aptly_reachable"] is False


@pytest.mark.parametrize("path", ["/", "/mirrors", "/snapshots", "/publications"])
async def test_pages_render(client_factory, path: str) -> None:
    cached = CachedState(state=_state(), stale=False, error=None)
    async with await client_factory(cached) as http:
        response = await http.get(path)
    assert response.status_code == 200
    assert "<title>" in response.text
