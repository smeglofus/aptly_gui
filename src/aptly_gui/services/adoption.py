from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from ..aptly import Mirror, Published
from .naming import DEFAULT_PATTERN, Split, split_mirror_name

# Best guess so the operator confirms a keyring instead of typing a path from memory.
KEYRING_GUESSES = {
    "ubuntu": "/usr/share/keyrings/ubuntu-archive-keyring.gpg",
    "debian": "/usr/share/keyrings/debian-archive-keyring.gpg",
}


@dataclass
class Proposal:
    """A guess that some existing mirrors belong together as one set."""

    name: str
    archive_url: str
    suites: list[str] = field(default_factory=list)
    components: list[str] = field(default_factory=list)
    architectures: list[str] = field(default_factory=list)
    mirrors: list[str] = field(default_factory=list)
    filter: str | None = None
    publish_prefix: str | None = None
    keyring_guess: str | None = None
    pattern: str = DEFAULT_PATTERN
    inconsistent: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """Whether every suite x component combination actually exists as a mirror."""
        return len(self.mirrors) == len(self.suites) * len(self.components)

    @property
    def missing(self) -> list[str]:
        expected = {
            self.pattern.format(set=self.name, suite=suite, component=component)
            for suite in self.suites
            for component in self.components
        }
        return sorted(expected - set(self.mirrors))


def propose_sets(
    mirrors: list[Mirror],
    published: list[Published],
    *,
    managed: set[str] | None = None,
) -> tuple[list[Proposal], list[Mirror]]:
    """Group unmanaged mirrors into candidate sets.

    Returns the proposals plus the mirrors that fit no convention and need manual work.
    """
    managed = managed or set()
    grouped: dict[tuple[str, str, str], list[tuple[Mirror, Split]]] = defaultdict(list)
    leftovers: list[Mirror] = []

    for mirror in mirrors:
        if mirror.name in managed:
            continue
        split = split_mirror_name(mirror)
        if split is None:
            leftovers.append(mirror)
            continue
        grouped[(split.set_name, mirror.archive_url, split.pattern)].append((mirror, split))

    proposals = [
        _build(name, archive_url, pattern, members, published)
        for (name, archive_url, pattern), members in sorted(grouped.items())
    ]
    return proposals, leftovers


def _build(
    name: str,
    archive_url: str,
    pattern: str,
    members: list[tuple[Mirror, Split]],
    published: list[Published],
) -> Proposal:
    suites = sorted({split.suite for _, split in members})
    components = sorted({split.component for _, split in members})
    architectures = sorted({arch for mirror, _ in members for arch in mirror.architectures})

    filters = {mirror.filter for mirror, _ in members}
    inconsistent = []
    if len(filters) > 1:
        inconsistent.append("filter")

    return Proposal(
        name=name,
        archive_url=archive_url,
        suites=suites,
        components=components,
        architectures=architectures,
        mirrors=sorted(mirror.name for mirror, _ in members),
        filter=next(iter(filters)) if len(filters) == 1 else None,
        publish_prefix=guess_prefix(suites, components, published),
        keyring_guess=guess_keyring(archive_url),
        pattern=pattern,
        inconsistent=inconsistent,
    )


def guess_prefix(
    suites: list[str], components: list[str], published: list[Published]
) -> str | None:
    """Propose the publish prefix whose publications line up with this set's suites."""
    wanted_components = set(components)
    candidates = {
        pub.prefix
        for pub in published
        if pub.distribution in suites and {s.component for s in pub.sources} & wanted_components
    }
    return candidates.pop() if len(candidates) == 1 else None


def guess_keyring(archive_url: str) -> str | None:
    lowered = archive_url.lower()
    for needle, keyring in KEYRING_GUESSES.items():
        if needle in lowered:
            return keyring
    return None
