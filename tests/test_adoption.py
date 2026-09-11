from __future__ import annotations

from datetime import UTC, datetime

from aptly_gui.aptly import Mirror, Published
from aptly_gui.services import propose_sets, split_mirror_name

NOW = datetime.now(UTC)


def mirror(name: str, distribution: str, components: list[str], **kwargs: object) -> Mirror:
    return Mirror(
        name=name,
        archive_url=kwargs.get("archive_url", "http://archive.ubuntu.com/ubuntu"),  # type: ignore[arg-type]
        distribution=distribution,
        components=components,
        architectures=kwargs.get("architectures", ["amd64"]),  # type: ignore[arg-type]
        filter=kwargs.get("filter"),  # type: ignore[arg-type]
        last_download=NOW,
    )


def test_split_uses_metadata_not_dash_counting() -> None:
    """Both set names and suites contain dashes, so the split cannot be positional."""
    result = split_mirror_name(mirror("ubuntu-noble-noble-updates-main", "noble-updates", ["main"]))
    assert result is not None
    assert (result.set_name, result.suite, result.component) == (
        "ubuntu-noble",
        "noble-updates",
        "main",
    )


def test_split_rejects_multi_component_mirror() -> None:
    assert split_mirror_name(mirror("x-noble-main", "noble", ["main", "universe"])) is None


def test_split_rejects_name_not_matching_convention() -> None:
    assert split_mirror_name(mirror("something-else", "noble", ["main"])) is None


def test_groups_ubuntu_style_set() -> None:
    mirrors = [
        mirror(f"ubuntu-noble-{suite}-{component}", suite, [component])
        for suite in ("noble", "noble-updates")
        for component in ("main", "universe")
    ]
    proposals, leftovers = propose_sets(mirrors, [])
    assert leftovers == []
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.name == "ubuntu-noble"
    assert proposal.suites == ["noble", "noble-updates"]
    assert proposal.components == ["main", "universe"]
    assert proposal.complete


def test_reports_missing_combinations() -> None:
    mirrors = [
        mirror("ubuntu-noble-noble-main", "noble", ["main"]),
        mirror("ubuntu-noble-noble-universe", "noble", ["universe"]),
        mirror("ubuntu-noble-noble-updates-main", "noble-updates", ["main"]),
    ]
    proposal = propose_sets(mirrors, [])[0][0]
    assert not proposal.complete
    assert proposal.missing == ["ubuntu-noble-noble-updates-universe"]


def test_already_managed_mirrors_are_skipped() -> None:
    mirrors = [mirror("ubuntu-noble-noble-main", "noble", ["main"])]
    proposals, leftovers = propose_sets(mirrors, [], managed={"ubuntu-noble-noble-main"})
    assert proposals == []
    assert leftovers == []


def test_different_upstreams_do_not_merge() -> None:
    mirrors = [
        mirror("x-noble-main", "noble", ["main"], archive_url="http://a.example/ubuntu"),
        mirror("x-noble-universe", "noble", ["universe"], archive_url="http://b.example/ubuntu"),
    ]
    proposals, _ = propose_sets(mirrors, [])
    assert len(proposals) == 2


def test_prefix_guessed_from_matching_publication() -> None:
    mirrors = [mirror("ubuntu-noble-noble-main", "noble", ["main"])]
    published = [
        Published.parse(
            {
                "Prefix": "ubuntu",
                "Distribution": "noble",
                "Architectures": ["amd64"],
                "Sources": [{"Component": "main", "Name": "whatever"}],
            }
        )
    ]
    assert propose_sets(mirrors, published)[0][0].publish_prefix == "ubuntu"


def test_ambiguous_prefix_is_left_blank() -> None:
    mirrors = [mirror("ubuntu-noble-noble-main", "noble", ["main"])]
    published = [
        Published.parse(
            {
                "Prefix": prefix,
                "Distribution": "noble",
                "Architectures": ["amd64"],
                "Sources": [{"Component": "main", "Name": "whatever"}],
            }
        )
        for prefix in ("ubuntu", "ubuntu-mirror")
    ]
    assert propose_sets(mirrors, published)[0][0].publish_prefix is None


def test_keyring_guessed_from_archive_url() -> None:
    mirrors = [mirror("ubuntu-noble-noble-main", "noble", ["main"])]
    proposal = propose_sets(mirrors, [])[0][0]
    assert proposal.keyring_guess == "/usr/share/keyrings/ubuntu-archive-keyring.gpg"


def test_inconsistent_filter_is_flagged_not_guessed() -> None:
    mirrors = [
        mirror("x-noble-main", "noble", ["main"], filter="nginx"),
        mirror("x-noble-universe", "noble", ["universe"], filter="apache2"),
    ]
    proposal = propose_sets(mirrors, [])[0][0]
    assert proposal.filter is None
    assert "filter" in proposal.inconsistent
