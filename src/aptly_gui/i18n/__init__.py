from __future__ import annotations

from contextvars import ContextVar
from pathlib import Path

from babel.support import NullTranslations, Translations

LOCALES_DIR = Path(__file__).parent / "locales"
DOMAIN = "messages"

# Set per request. Unset means untranslated msgids, which are English, so the UI
# renders correctly even before any catalogue is compiled.
_current: ContextVar[NullTranslations | None] = ContextVar("translations", default=None)

_cache: dict[str, NullTranslations] = {}


def _active() -> NullTranslations:
    return _current.get() or NullTranslations()


def load(locale: str) -> NullTranslations:
    if locale not in _cache:
        _cache[locale] = Translations.load(LOCALES_DIR, [locale], domain=DOMAIN)
    return _cache[locale]


def activate(locale: str) -> None:
    _current.set(load(locale))


def gettext(message: str) -> str:
    return _active().gettext(message)


def ngettext(singular: str, plural: str, n: int) -> str:
    return _active().ngettext(singular, plural, n)


def available_locales() -> list[str]:
    if not LOCALES_DIR.is_dir():
        return ["en"]
    found = {p.name for p in LOCALES_DIR.iterdir() if (p / "LC_MESSAGES").is_dir()}
    return sorted({"en", *found})
