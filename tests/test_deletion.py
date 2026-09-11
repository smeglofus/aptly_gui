"""Removing things has to distinguish forgetting from destroying.

Forgetting a set is the inverse of adopting it and touches nothing in aptly.
Deleting its mirrors does, and must not orphan the snapshots made from them.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from tests.conftest import CSRF, _state

from aptly_gui.aptly import Snapshot
from aptly_gui.db import MirrorSet
from aptly_gui.web.state import CachedState

NOW = datetime.now(UTC)
MIRROR = "ubuntu-noble-noble-main"


async def _managed_set(app) -> int:
    async with app.state.sessions() as session:
        session.add(
            MirrorSet(
                name="ubuntu-noble",
                archive_url="http://archive.ubuntu.com/ubuntu",
                suites=["noble"],
                components=["main"],
                architectures=["amd64"],
                publish_prefix="ubuntu",
                mirror_pattern="{set}-{suite}-{component}",
            )
        )
        await session.commit()
        stored = (await session.execute(select(MirrorSet))).scalars().all()
        return [item.id for item in stored if item.name == "ubuntu-noble"][0]


# --- forgetting --------------------------------------------------------------


async def test_forgetting_a_set_leaves_aptly_alone(app_client) -> None:
    http, app = await app_client()
    set_id = await _managed_set(app)
    async with http:
        response = await http.post(
            f"/sets/{set_id}/delete",
            data={"csrf_token": CSRF, "mirrors": "keep"},
            follow_redirects=False,
        )
    assert response.status_code == 303
    async with app.state.sessions() as session:
        assert (await session.execute(select(MirrorSet))).scalars().all() == []
    # Nothing was queued, so nothing in aptly was touched.
    assert app.state.runner.calls == []


async def test_forgetting_is_offered_as_always_safe(app_client) -> None:
    http, app = await app_client()
    set_id = await _managed_set(app)
    async with http:
        page = await http.get(f"/sets/{set_id}/delete")
    assert "Forget the set" in page.text
    assert "always safe" in page.text


# --- deleting mirrors --------------------------------------------------------


async def test_deleting_a_set_with_its_mirrors_queues_the_job(app_client) -> None:
    """The fixture's snapshot is named for a different mirror, so nothing blocks."""
    state = _state(snapshots=[Snapshot(name="unrelated-snap", created_at=NOW, description="")])
    http, app = await app_client(CachedState(state=state, stale=False, error=None))
    set_id = await _managed_set(app)
    async with http:
        response = await http.post(
            f"/sets/{set_id}/delete",
            data={"csrf_token": CSRF, "mirrors": "delete"},
            follow_redirects=False,
        )
    assert response.status_code == 303
    assert [call[0] for call in app.state.runner.calls] == ["remove"]
    assert app.state.runner.calls[0][2]["mirrors"] == [MIRROR]


async def test_a_mirror_with_snapshots_is_not_deleted(app_client) -> None:
    """aptly refuses this too; refusing first explains why instead of failing halfway."""
    http, app = await app_client()  # fixture snapshot is ubuntu-noble-noble-main-...
    set_id = await _managed_set(app)
    async with http:
        page = await http.get(f"/sets/{set_id}/delete")
        response = await http.post(
            f"/sets/{set_id}/delete",
            data={"csrf_token": CSRF, "mirrors": "delete"},
            follow_redirects=False,
        )
    assert "Snapshots were made from these mirrors" in page.text
    assert response.status_code == 303
    assert app.state.runner.calls == []
    async with app.state.sessions() as session:
        assert (await session.execute(select(MirrorSet))).scalars().all() != []


# --- single mirrors ----------------------------------------------------------


async def test_a_mirror_inside_a_set_is_not_deleted_on_its_own(app_client) -> None:
    http, app = await app_client()
    await _managed_set(app)
    async with http:
        page = await http.get(f"/mirrors/delete?name={MIRROR}")
        response = await http.post(
            "/mirrors/delete", data={"csrf_token": CSRF, "name": MIRROR}, follow_redirects=False
        )
    assert "belongs to the set" in page.text
    assert response.status_code == 303
    assert app.state.runner.calls == []


async def test_an_unmanaged_mirror_without_snapshots_can_go(app_client) -> None:
    state = _state(snapshots=[])
    http, app = await app_client(CachedState(state=state, stale=False, error=None))
    async with http:
        page = await http.get(f"/mirrors/delete?name={MIRROR}")
        response = await http.post(
            "/mirrors/delete", data={"csrf_token": CSRF, "name": MIRROR}, follow_redirects=False
        )
    assert "Delete it" in page.text
    assert response.status_code == 303
    assert [call[0] for call in app.state.runner.calls] == ["remove"]
    assert app.state.runner.calls[0][2]["mirrors"] == [MIRROR]


async def test_deleting_an_unknown_mirror_does_nothing(app_client) -> None:
    """A stale link, after someone else removed it."""
    http, app = await app_client()
    async with http:
        response = await http.post(
            "/mirrors/delete",
            data={"csrf_token": CSRF, "name": "does-not-exist"},
            follow_redirects=False,
        )
    assert response.status_code == 303
    assert app.state.runner.calls == []
