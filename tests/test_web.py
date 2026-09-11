from __future__ import annotations

import pytest
from sqlalchemy import select
from tests.conftest import CSRF, _state

from aptly_gui.db import MirrorSet
from aptly_gui.web.state import CachedState


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
                "csrf_token": CSRF,
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
        await http.post(
            "/settings", data={"language": "cs", "csrf_token": CSRF}, follow_redirects=False
        )
        response = await http.get("/")
    assert app.state.language == "cs"
    assert "Přehled" in response.text


async def test_unknown_language_is_rejected(app_client) -> None:
    http, app = await app_client()
    async with http:
        await http.post(
            "/settings", data={"language": "klingon", "csrf_token": CSRF}, follow_redirects=False
        )
    assert app.state.language == "en"


async def test_new_set_form_renders(app_client) -> None:
    http, _ = await app_client()
    async with http:
        response = await http.get("/sets/new")
    assert response.status_code == 200
    assert 'name="archive_url"' in response.text


async def test_creating_a_set_queues_a_job_to_make_the_mirrors(app_client) -> None:
    http, app = await app_client()
    async with http:
        response = await http.post(
            "/sets/new",
            data={
                "csrf_token": CSRF,
                "name": "debian-trixie",
                "archive_url": "http://deb.debian.org/debian",
                "suites": "trixie",
                "components": "main,contrib",
                "architectures": "amd64",
                "publish_prefix": "debian",
                "keyrings": "/usr/share/keyrings/debian-archive-keyring.gpg",
                "signing_key": "",
                "filter": "",
            },
            follow_redirects=False,
        )
    assert response.status_code == 303
    # Unlike adoption, creating a set does reach aptly — through a job.
    assert [call[0] for call in app.state.runner.calls] == ["create"]


async def test_duplicate_set_name_is_refused_without_queueing_anything(app_client) -> None:
    http, app = await app_client()
    payload = {
        "name": "debian-trixie",
        "archive_url": "http://deb.debian.org/debian",
        "suites": "trixie",
        "components": "main",
        "architectures": "amd64",
        "publish_prefix": "debian",
        "keyrings": "",
        "signing_key": "",
        "filter": "",
    }
    async with http:
        await http.post("/sets/new", data={**payload, "csrf_token": CSRF}, follow_redirects=False)
        second = await http.post(
            "/sets/new", data={**payload, "csrf_token": CSRF}, follow_redirects=False
        )
    assert second.status_code == 200
    assert "already exists" in second.text
    assert len(app.state.runner.calls) == 1


async def test_snapshots_page_says_where_a_snapshot_belongs(app_client) -> None:
    """Without context the screen is a list of names and a dead end."""
    http, _ = await app_client()
    async with http:
        response = await http.get("/snapshots")
    assert response.status_code == 200
    assert "ubuntu-noble-noble-main-20260911T0800Z" in response.text
    # It is published, so it is shown as in use rather than offered for deletion.
    assert "published" in response.text


async def test_published_snapshot_cannot_be_deleted(app_client) -> None:
    http, app = await app_client()
    async with http:
        page = await http.get("/snapshots/discard?name=ubuntu-noble-noble-main-20260911T0800Z")
        posted = await http.post(
            "/snapshots/discard",
            data={"name": "ubuntu-noble-noble-main-20260911T0800Z", "csrf_token": CSRF},
            follow_redirects=False,
        )
    assert "is published" in page.text
    # Refused, and nothing was queued against aptly.
    assert posted.status_code == 303
    assert app.state.runner.calls == []


async def test_orphan_snapshot_can_be_deleted(app_client) -> None:
    state = _state(published=[])
    cached = CachedState(state=state, stale=False, error=None)
    http, app = await app_client(cached)
    async with http:
        page = await http.get("/snapshots/discard?name=ubuntu-noble-noble-main-20260911T0800Z")
        posted = await http.post(
            "/snapshots/discard",
            data={"name": "ubuntu-noble-noble-main-20260911T0800Z", "csrf_token": CSRF},
            follow_redirects=False,
        )
    assert "Delete it" in page.text
    assert posted.status_code == 303
    assert [call[0] for call in app.state.runner.calls] == ["discard"]
    assert app.state.runner.calls[0][2]["snapshots"] == ["ubuntu-noble-noble-main-20260911T0800Z"]


async def test_deleting_an_unknown_snapshot_does_nothing(app_client) -> None:
    http, app = await app_client()
    async with http:
        posted = await http.post(
            "/snapshots/discard",
            data={"name": "does-not-exist", "csrf_token": CSRF},
            follow_redirects=False,
        )
    assert posted.status_code == 303
    assert app.state.runner.calls == []
