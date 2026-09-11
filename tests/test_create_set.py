from __future__ import annotations

from aptly_gui.db import JobType, MirrorSet
from aptly_gui.services.jobs import _expected_steps


def _mirror_set(**overrides: object) -> MirrorSet:
    defaults: dict[str, object] = {
        "name": "ubuntu-noble",
        "archive_url": "http://archive.ubuntu.com/ubuntu",
        "suites": ["noble", "noble-updates"],
        "components": ["main", "universe"],
        "architectures": ["amd64"],
        "publish_prefix": "ubuntu",
        "mirror_pattern": "{set}-{suite}-{component}",
    }
    return MirrorSet(**{**defaults, **overrides})


def test_create_makes_one_mirror_per_suite_and_component() -> None:
    """Every component needs its own mirror, or aptly merges them on publish."""
    assert _expected_steps(JobType.CREATE, _mirror_set()) == 4


def test_expected_mirror_names_follow_the_convention() -> None:
    assert _mirror_set().expected_mirrors == [
        "ubuntu-noble-noble-main",
        "ubuntu-noble-noble-universe",
        "ubuntu-noble-noble-updates-main",
        "ubuntu-noble-noble-updates-universe",
    ]


def test_a_created_set_is_not_marked_as_adopted() -> None:
    assert _mirror_set().adopted is not True


async def test_a_set_whose_mirrors_are_missing_offers_to_make_them(app_client) -> None:
    """A create job that failed part way leaves a set describing nothing.

    Without a way back the set is stuck: sync refuses because the mirrors are absent,
    and nothing else creates them.
    """
    from sqlalchemy import select
    from tests.conftest import CSRF

    from aptly_gui.db import MirrorSet

    http, app = await app_client()
    async with app.state.sessions() as session:
        session.add(
            MirrorSet(
                name="ubuntu-jammy",
                archive_url="http://archive.ubuntu.com/ubuntu",
                suites=["jammy"],
                components=["main"],
                architectures=["amd64"],
                publish_prefix="ubuntu",
                mirror_pattern="{set}-{suite}-{component}",
            )
        )
        await session.commit()
        stored = (await session.execute(select(MirrorSet))).scalars().all()
        set_id = [item.id for item in stored if item.name == "ubuntu-jammy"][0]

    async with http:
        page = await http.get(f"/sets/{set_id}")
        queued = await http.post(
            f"/sets/{set_id}/create", data={"csrf_token": CSRF}, follow_redirects=False
        )

    # The fixture's aptly holds no ubuntu-jammy mirror, so the gap is reported...
    assert "does not exist in aptly" in page.text or "do not exist in aptly" in page.text
    assert "Create missing mirrors" in page.text
    # ...and the button queues the job that fills it.
    assert queued.status_code == 303
    assert [call[0] for call in app.state.runner.calls] == ["create"]
