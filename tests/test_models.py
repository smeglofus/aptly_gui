from __future__ import annotations

import pytest

from aptly_gui.aptly import MirrorSpec, Published, Signing, TaskState, encode_prefix


def test_mirror_payload_always_carries_keyrings() -> None:
    """aptly drops Keyrings on update, so every request must restate them."""
    spec = MirrorSpec(
        name="ubuntu-noble-main",
        archive_url="http://archive.ubuntu.com/ubuntu",
        distribution="noble",
        components=["main"],
        architectures=["amd64"],
        keyrings=["/usr/share/keyrings/ubuntu-archive-keyring.gpg"],
    )
    assert spec.payload()["Keyrings"] == ["/usr/share/keyrings/ubuntu-archive-keyring.gpg"]


def test_mirror_payload_omits_empty_filter() -> None:
    spec = MirrorSpec(
        name="m",
        archive_url="http://example.invalid",
        distribution="noble",
        components=["main"],
        architectures=["amd64"],
    )
    assert "Filter" not in spec.payload()


@pytest.mark.parametrize(
    ("prefix", "expected"),
    [
        ("ubuntu", "ubuntu"),
        ("debian/test", "debian_test"),
        ("with_underscore", "with__underscore"),
        ("/leading", "leading"),
        (".", ":."),
        ("", ":."),
    ],
)
def test_encode_prefix(prefix: str, expected: str) -> None:
    assert encode_prefix(prefix) == expected


def test_task_state_finished() -> None:
    assert TaskState.SUCCEEDED.finished
    assert TaskState.FAILED.finished
    assert not TaskState.RUNNING.finished
    assert not TaskState.QUEUED.finished


def test_published_lookup_by_component() -> None:
    published = Published.parse(
        {
            "Prefix": "ubuntu",
            "Distribution": "noble",
            "Architectures": ["amd64"],
            "Sources": [
                {"Component": "main", "Name": "ubuntu-noble-main-20260911T0800Z"},
                {"Component": "universe", "Name": "ubuntu-noble-universe-20260911T0800Z"},
            ],
        }
    )
    assert published.snapshot_for("universe") == "ubuntu-noble-universe-20260911T0800Z"
    assert published.snapshot_for("restricted") is None


def test_signing_skip_wins_over_key() -> None:
    assert Signing(gpg_key="key", skip=True).payload() == {"Skip": True}
