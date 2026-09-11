from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_LANGUAGE = "en"
LANGUAGES = {"en": "English", "cs": "Čeština"}


@dataclass(frozen=True)
class Settings:
    aptly_url: str = "http://127.0.0.1:8079"
    aptly_socket: str | None = None
    database_url: str = "sqlite+aiosqlite:///./aptly-gui.db"
    refresh_seconds: int = 15
    request_timeout: float = 30.0
    default_language: str = DEFAULT_LANGUAGE

    @classmethod
    def from_env(cls) -> Settings:
        socket = os.environ.get("APTLY_API_SOCKET") or None
        return cls(
            # With a unix socket the host part is ignored, but httpx still needs one.
            aptly_url=os.environ.get("APTLY_API_URL", "http://aptly" if socket else cls.aptly_url),
            aptly_socket=socket,
            database_url=os.environ.get("APTLY_GUI_DATABASE_URL", cls.database_url),
            refresh_seconds=int(os.environ.get("APTLY_GUI_REFRESH_SECONDS", cls.refresh_seconds)),
            request_timeout=float(os.environ.get("APTLY_GUI_TIMEOUT", cls.request_timeout)),
            default_language=os.environ.get("APTLY_GUI_LANGUAGE", cls.default_language),
        )
