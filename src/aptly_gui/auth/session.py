from __future__ import annotations

import secrets
from typing import Any

from itsdangerous import BadSignature, URLSafeTimedSerializer
from starlette.requests import Request
from starlette.responses import Response

COOKIE_NAME = "aptly_gui_session"
SALT = "aptly-gui-session"

# The session is a signed cookie rather than server state so that a restart does not
# log everybody out, and so nothing has to be cleaned up when it expires.


def _serializer(secret_key: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key, salt=SALT)


def read_session(request: Request) -> dict[str, Any]:
    raw = request.cookies.get(COOKIE_NAME)
    if not raw:
        return {}
    try:
        payload = _serializer(request.app.state.secret_key).loads(
            raw, max_age=request.app.state.session_max_age
        )
    except BadSignature:
        return {}
    return payload if isinstance(payload, dict) else {}


def write_session(request: Request, response: Response, payload: dict[str, Any]) -> None:
    response.set_cookie(
        COOKIE_NAME,
        _serializer(request.app.state.secret_key).dumps(payload),
        max_age=request.app.state.session_max_age,
        httponly=True,
        samesite="lax",
        secure=request.app.state.secure_cookies,
        path="/",
    )


def clear_session(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_matches(expected: str | None, submitted: str | None) -> bool:
    if not expected or not submitted:
        return False
    return secrets.compare_digest(expected, submitted)
