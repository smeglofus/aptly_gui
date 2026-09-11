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
