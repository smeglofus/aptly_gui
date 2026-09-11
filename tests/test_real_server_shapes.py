"""Shapes taken from a real aptly server, which the first design did not handle.

Its mirrors are named suite-component with no set prefix, and it publishes to a named
filesystem endpoint. Both broke the GUI: adoption found nothing, and a bare endpoint
name makes aptly panic and exit rather than return an error.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aptly_gui.aptly import Mirror
from aptly_gui.db import MirrorSet
from aptly_gui.services import SET_SUITE_COMPONENT, SUITE_COMPONENT, propose_sets, split_mirror_name

NOW = datetime.now(UTC)
UBUNTU = "https://archive.ubuntu.com/ubuntu"

REAL_NAMES = [
    ("resolute-main", "resolute", "main"),
    ("resolute-restricted", "resolute", "restricted"),
    ("resolute-universe", "resolute", "universe"),
    ("resolute-multiverse", "resolute", "multiverse"),
    ("resolute-updates-main", "resolute-updates", "main"),
    ("resolute-updates-restricted", "resolute-updates", "restricted"),
    ("resolute-updates-universe", "resolute-updates", "universe"),
    ("resolute-updates-multiverse", "resolute-updates", "multiverse"),
]


def _mirror(name: str, suite: str, component: str, archive: str = UBUNTU) -> Mirror:
    return Mirror(
        name=name,
        archive_url=archive,
        distribution=suite,
        components=[component],
        architectures=["amd64"],
        filter=None,
        last_download=NOW,
    )


def _real_mirrors() -> list[Mirror]:
    return [_mirror(*entry) for entry in REAL_NAMES]


# --- naming ------------------------------------------------------------------


@pytest.mark.parametrize(("name", "suite", "component"), REAL_NAMES)
def test_suite_component_names_are_understood(name: str, suite: str, component: str) -> None:
    split = split_mirror_name(_mirror(name, suite, component))
    assert split is not None
    assert split.suite == suite
    assert split.component == component
    assert split.pattern == SUITE_COMPONENT


def test_the_whole_real_set_is_adopted_as_one() -> None:
    proposals, leftovers = propose_sets(_real_mirrors(), [])
    assert leftovers == []
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.name == "resolute"
    assert proposal.pattern == SUITE_COMPONENT
    assert proposal.suites == ["resolute", "resolute-updates"]
    assert proposal.components == ["main", "multiverse", "restricted", "universe"]
    assert proposal.complete


def test_updates_suite_belongs_to_its_base_release() -> None:
    """resolute-updates is part of resolute, not a set of its own."""
    proposals, _ = propose_sets(_real_mirrors(), [])
    assert [item.name for item in proposals] == ["resolute"]


def test_the_prefixed_convention_still_works() -> None:
    mirrors = [
        _mirror("ubuntu-noble-noble-main", "noble", "main"),
        _mirror("ubuntu-noble-noble-updates-main", "noble-updates", "main"),
    ]
    proposals, leftovers = propose_sets(mirrors, [])
    assert leftovers == []
    assert proposals[0].name == "ubuntu-noble"
    assert proposals[0].pattern == SET_SUITE_COMPONENT


def test_the_two_conventions_do_not_merge() -> None:
    mirrors = [
        _mirror("noble-main", "noble", "main"),
        _mirror("ubuntu-noble-main", "noble", "main"),
    ]
    proposals, _ = propose_sets(mirrors, [])
    assert sorted(item.name for item in proposals) == ["noble", "ubuntu"]


def test_a_set_generates_names_in_its_own_convention() -> None:
    plain = MirrorSet(
        name="resolute",
        archive_url=UBUNTU,
        suites=["resolute", "resolute-updates"],
        components=["main"],
        architectures=["amd64"],
        publish_prefix="ubuntu",
        mirror_pattern=SUITE_COMPONENT,
    )
    assert plain.mirror_name("resolute-updates", "main") == "resolute-updates-main"
    assert plain.expected_mirrors == ["resolute-main", "resolute-updates-main"]


# --- publish target ----------------------------------------------------------


def _set(**overrides: object) -> MirrorSet:
    defaults: dict[str, object] = {
        "name": "resolute",
        "archive_url": UBUNTU,
        "suites": ["resolute"],
        "components": ["main"],
        "architectures": ["amd64"],
        "publish_prefix": "ubuntu",
        "mirror_pattern": SUITE_COMPONENT,
    }
    return MirrorSet(**{**defaults, **overrides})


def test_named_endpoint_carries_its_scheme() -> None:
    """A bare `prod:ubuntu` panics aptly and takes the whole API process down."""
    assert _set(publish_endpoint="prod").publish_target == "filesystem:prod:ubuntu"


def test_without_an_endpoint_the_prefix_stands_alone() -> None:
    assert _set().publish_target == "ubuntu"
    assert _set(publish_endpoint=None).publish_target == "ubuntu"
