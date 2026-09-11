from __future__ import annotations

from dataclasses import dataclass

from ..aptly import Mirror

# How a set's mirrors are named in aptly. Both are in real use: a set prefix
# disambiguates when one aptly holds several upstreams, and leaving it out is
# shorter when the suite already identifies the release.
SET_SUITE_COMPONENT = "{set}-{suite}-{component}"
SUITE_COMPONENT = "{suite}-{component}"

PATTERNS = (SET_SUITE_COMPONENT, SUITE_COMPONENT)
DEFAULT_PATTERN = SET_SUITE_COMPONENT


def mirror_name(pattern: str, set_name: str, suite: str, component: str) -> str:
    return pattern.format(set=set_name, suite=suite, component=component)


@dataclass(frozen=True)
class Split:
    set_name: str
    suite: str
    component: str
    pattern: str


def split_mirror_name(mirror: Mirror, set_name: str | None = None) -> Split | None:
    """Work out which set a mirror belongs to, and under which naming.

    The suite and component come from aptly's own fields rather than from counting
    dashes: both a set name (`ubuntu-noble`) and a suite (`noble-updates`) contain
    them, so a positional split is ambiguous.
    """
    if len(mirror.components) != 1:
        return None
    suite, component = mirror.distribution, mirror.components[0]

    suffix = f"-{suite}-{component}"
    if mirror.name.endswith(suffix):
        prefix = mirror.name[: -len(suffix)]
        if prefix:
            return Split(prefix, suite, component, SET_SUITE_COMPONENT)

    # No set prefix: the name is exactly suite-component, so the set is named after
    # the stem the suites share (noble, noble-updates -> noble).
    if mirror.name == f"{suite}-{component}":
        return Split(set_name or suite_stem(suite), suite, component, SUITE_COMPONENT)

    return None


def suite_stem(suite: str) -> str:
    """`noble-updates` and `noble-security` both belong to `noble`."""
    for marker in ("-updates", "-security", "-backports", "-proposed"):
        if suite.endswith(marker):
            return suite[: -len(marker)]
    return suite
