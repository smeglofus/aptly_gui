from __future__ import annotations

import re

# aptly reports several common misconfigurations as the bare exit status of the tool
# it shelled out to, which tells the operator nothing about what to change.
_EXPLANATIONS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"verification of detached signature failed", re.I),
        "The keyring does not contain the key that signed the upstream Release. "
        "Check the path exists on the aptly host and holds the right archive keyring "
        "— mirroring Ubuntu from a Debian host needs the ubuntu-keyring package, and "
        "per-release keyrings often do not verify.",
    ),
    (
        re.compile(r"no such file or directory.*keyring|keyring.*no such file", re.I),
        "That keyring file does not exist on the aptly host. The path is read by aptly, "
        "not by this service, so it has to exist there.",
    ),
    (
        re.compile(r"unable to fetch mirror.*404|HTTP 404", re.I),
        "The upstream archive has no such suite. Check the suite name and that the "
        "release still exists — security suites in particular live on a separate host.",
    ),
    (
        re.compile(r"invalid cross-device link", re.I),
        "The publish endpoint is on a different filesystem from aptly's pool, so it "
        "cannot hardlink. Put them on one filesystem, or set the endpoint's linkMethod "
        "to copy and expect it to use the space twice.",
    ),
    (
        re.compile(r"ReadTimeout|ConnectTimeout|timed out", re.I),
        "aptly did not answer in time. Creating a mirror makes aptly fetch and verify "
        "the upstream Release, which can take a minute against a slow archive; raise "
        "APTLY_GUI_CREATE_TIMEOUT if this keeps happening.",
    ),
]


def explain(message: str) -> str | None:
    """A sentence saying what to change, or None when the message already says it."""
    for pattern, explanation in _EXPLANATIONS:
        if pattern.search(message):
            return explanation
    return None


def with_explanation(message: str) -> str:
    explanation = explain(message)
    return f"{message}\n\n{explanation}" if explanation else message
