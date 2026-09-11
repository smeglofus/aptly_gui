"""Guards against the two ways a translation catalogue silently rots."""

from __future__ import annotations

import pytest
from babel.messages.pofile import read_po

from aptly_gui import i18n

LOCALES = [locale for locale in i18n.available_locales() if locale != "en"]


def _po_path(locale: str):
    return i18n.LOCALES_DIR / locale / "LC_MESSAGES" / "messages.po"


@pytest.mark.parametrize("locale", LOCALES)
def test_every_message_is_translated(locale: str) -> None:
    with _po_path(locale).open(encoding="utf-8") as handle:
        catalog = read_po(handle)
    missing = [message.id for message in catalog if message.id and not message.string]
    assert not missing, f"{locale} is missing translations for: {missing}"


@pytest.mark.parametrize("locale", LOCALES)
def test_compiled_catalogue_matches_source(locale: str) -> None:
    """A .mo left behind after editing the .po would serve stale text with no error."""
    with _po_path(locale).open(encoding="utf-8") as handle:
        catalog = read_po(handle)
    translations = i18n.load(locale)

    for message in catalog:
        if not message.id or isinstance(message.id, tuple):
            continue
        assert translations.gettext(message.id) == message.string, (
            f"{locale}: '{message.id}' differs between messages.po and messages.mo — "
            "recompile with: pybabel compile -d src/aptly_gui/i18n/locales"
        )
