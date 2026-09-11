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
    create_timeout: float = 300.0
    default_language: str = DEFAULT_LANGUAGE
    # Empty means "generate one and keep it in the database", so a fresh install
    # needs no configuration and a restart does not sign everyone out.
    secret_key: str = ""
    session_max_age: int = 14 * 24 * 3600
    secure_cookies: bool = False
    # The one local account. Everyone else signs in through the provider.
    admin_username: str = ""
    admin_password: str = ""
    # A sync stops rather than filling the disk it shares with the published tree.
    safety_margin_gb: int = 30
    safety_margin_percent: int = 15

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
            create_timeout=float(os.environ.get("APTLY_GUI_CREATE_TIMEOUT", cls.create_timeout)),
            default_language=os.environ.get("APTLY_GUI_LANGUAGE", cls.default_language),
            secret_key=os.environ.get("APTLY_GUI_SECRET_KEY", cls.secret_key),
            session_max_age=int(os.environ.get("APTLY_GUI_SESSION_MAX_AGE", cls.session_max_age)),
            secure_cookies=os.environ.get("APTLY_GUI_SECURE_COOKIES", "").lower()
            in ("1", "true", "yes"),
            admin_username=os.environ.get("APTLY_GUI_ADMIN_USERNAME", cls.admin_username),
            admin_password=os.environ.get("APTLY_GUI_ADMIN_PASSWORD", cls.admin_password),
            safety_margin_gb=int(
                os.environ.get("APTLY_GUI_SAFETY_MARGIN_GB", cls.safety_margin_gb)
            ),
            safety_margin_percent=int(
                os.environ.get("APTLY_GUI_SAFETY_MARGIN_PERCENT", cls.safety_margin_percent)
            ),
        )
