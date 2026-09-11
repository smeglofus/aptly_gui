"""aptly reports several common misconfigurations as a bare exit status.

Trying to mirror Ubuntu from a Debian host without the ubuntu-keyring package took
50 seconds and then said `exit status 2`, which surfaced as a plain ReadTimeout and
told the operator nothing. With the keyring present the same call takes 0.7 seconds.
"""

from __future__ import annotations

import pytest

from aptly_gui.aptly import explain, with_explanation


@pytest.mark.parametrize(
    ("message", "expected_hint"),
    [
        (
            "unable to fetch mirror: verification of detached signature failed: exit status 2",
            "keyring",
        ),
        ("ReadTimeout: ", "did not answer in time"),
        ("httpx.ConnectTimeout: timed out", "did not answer in time"),
        (
            "unable to publish: link /a /b: invalid cross-device link",
            "different filesystem",
        ),
        ("unable to fetch mirror: HTTP 404", "no such suite"),
    ],
)
def test_known_failures_say_what_to_change(message: str, expected_hint: str) -> None:
    explanation = explain(message)
    assert explanation is not None
    assert expected_hint in explanation


def test_an_unfamiliar_message_is_left_alone() -> None:
    """Inventing an explanation for something unrecognised would be worse than none."""
    assert explain("the disk caught fire") is None
    assert with_explanation("the disk caught fire") == "the disk caught fire"


def test_the_original_message_is_kept() -> None:
    original = "unable to fetch mirror: verification of detached signature failed: exit status 2"
    combined = with_explanation(original)
    assert combined.startswith(original)
    assert len(combined) > len(original)


def test_the_keyring_hint_names_the_ubuntu_case() -> None:
    """That is the one that actually bit, and it is not obvious from the error."""
    explanation = explain("verification of detached signature failed: exit status 2")
    assert explanation is not None
    assert "ubuntu-keyring" in explanation
