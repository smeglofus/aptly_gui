from __future__ import annotations

import pytest

from aptly_gui import i18n


@pytest.fixture(autouse=True)
def _english_afterwards() -> None:
    yield
    i18n.activate("en")


def test_czech_catalogue_is_available() -> None:
    assert "cs" in i18n.available_locales()


def test_czech_translates_a_known_string() -> None:
    i18n.activate("cs")
    assert i18n.gettext("Overview") == "Přehled"


@pytest.mark.parametrize(
    ("count", "expected"),
    [(1, "mirror"), (2, "mirrory"), (4, "mirrory"), (5, "mirrorů"), (11, "mirrorů")],
)
def test_czech_uses_all_three_plural_forms(count: int, expected: str) -> None:
    """Czech distinguishes 1, 2-4 and 5+; a two-form catalogue would fail at 2 or 5."""
    i18n.activate("cs")
    assert i18n.ngettext("mirror", "mirrors", count) == expected


@pytest.mark.parametrize(("count", "expected"), [(1, "mirror"), (2, "mirrors"), (5, "mirrors")])
def test_english_falls_back_to_msgid(count: int, expected: str) -> None:
    i18n.activate("en")
    assert i18n.ngettext("mirror", "mirrors", count) == expected


def test_unknown_locale_degrades_to_english() -> None:
    i18n.activate("xx")
    assert i18n.gettext("Overview") == "Overview"
