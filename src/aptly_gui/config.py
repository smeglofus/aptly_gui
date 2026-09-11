from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    aptly_url: str = "http://127.0.0.1:8079"
    aptly_socket: str | None = None
    refresh_seconds: int = 15
    request_timeout: float = 30.0

    @classmethod
    def from_env(cls) -> Settings:
        socket = os.environ.get("APTLY_API_SOCKET") or None
        return cls(
            # With a unix socket the host part is ignored, but httpx still needs one.
            aptly_url=os.environ.get("APTLY_API_URL", "http://aptly" if socket else cls.aptly_url),
            aptly_socket=socket,
            refresh_seconds=int(os.environ.get("APTLY_GUI_REFRESH_SECONDS", cls.refresh_seconds)),
            request_timeout=float(os.environ.get("APTLY_GUI_TIMEOUT", cls.request_timeout)),
        )
