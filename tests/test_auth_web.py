"""The guard is the only thing between an anonymous request and aptly, so it is
tested through real requests rather than by calling the policy directly."""

from __future__ import annotations

from typing import Any

import pytest
from tests.conftest import CSRF

from aptly_gui.auth import COOKIE_NAME
from aptly_gui.db import UserRole


async def test_anonymous_is_sent_to_the_login_page(app_client) -> None:
    http, _ = await app_client()
    http.cookies.clear()
    async with http:
        response = await http.get("/sets", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/sets"


async def test_login_page_is_reachable_without_a_session(app_client) -> None:
    http, _ = await app_client()
    http.cookies.clear()
    async with http:
        response = await http.get("/login")
    assert response.status_code == 200
    # A token is handed out even to a signed-out visitor, or the form could not post.
    assert 'name="csrf_token"' in response.text
    assert "set-cookie" in response.headers


async def test_healthz_stays_public(app_client) -> None:
    http, _ = await app_client()
    http.cookies.clear()
    async with http:
        response = await http.get("/healthz")
    assert response.status_code == 200


async def test_post_without_a_token_is_refused(app_client) -> None:
    http, app = await app_client()
    async with http:
        response = await http.post("/sets/1/update", data={}, follow_redirects=False)
    assert response.status_code == 400
    assert app.state.runner.calls == []


async def test_post_with_the_wrong_token_is_refused(app_client) -> None:
    http, app = await app_client()
    async with http:
        response = await http.post(
            "/sets/1/update", data={"csrf_token": "not-the-one"}, follow_redirects=False
        )
    assert response.status_code == 400
    assert app.state.runner.calls == []


async def test_viewer_may_read_but_not_act(app_client) -> None:
    http, app = await app_client(None, UserRole.VIEWER)
    async with http:
        readable = await http.get("/sets")
        acted = await http.post("/sets/1/update", data={"csrf_token": CSRF}, follow_redirects=False)
    assert readable.status_code == 200
    assert acted.status_code == 403
    assert app.state.runner.calls == []


@pytest.mark.parametrize("path", ["/users", "/settings"])
async def test_viewer_cannot_even_read_administration(app_client, path: str) -> None:
    """Who may sign in, and how the instance is wired up, is not viewer business."""
    http, _ = await app_client(None, UserRole.VIEWER)
    async with http:
        response = await http.get(path)
    assert response.status_code == 403


async def test_operator_may_act_but_not_administer(app_client) -> None:
    http, app = await app_client(None, UserRole.OPERATOR)
    async with http:
        acted = await http.post("/sets/1/update", data={"csrf_token": CSRF}, follow_redirects=False)
        administered = await http.post(
            "/settings", data={"csrf_token": CSRF, "language": "cs"}, follow_redirects=False
        )
    assert acted.status_code == 303
    assert [call[0] for call in app.state.runner.calls] == ["update"]
    assert administered.status_code == 403


async def test_admin_may_do_both(app_client) -> None:
    http, app = await app_client(None, UserRole.ADMIN)
    async with http:
        assert (await http.get("/users")).status_code == 200
        administered = await http.post(
            "/settings", data={"csrf_token": CSRF, "language": "cs"}, follow_redirects=False
        )
    assert administered.status_code == 303


async def test_deactivated_account_is_treated_as_signed_out(app_client) -> None:
    http, app = await app_client()
    async with app.state.sessions() as session:
        from sqlalchemy import select

        from aptly_gui.db import User

        user = (await session.execute(select(User))).scalars().first()
        assert user is not None
        user.active = False
        await session.commit()
    async with http:
        response = await http.get("/sets", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


async def test_signing_out_expires_the_cookie(app_client) -> None:
    http, _ = await app_client()
    async with http:
        response = await http.get("/logout", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    cookie = response.headers["set-cookie"]
    assert COOKIE_NAME in cookie
    # Expired rather than merely replaced, so the browser drops it.
    assert "Max-Age=0" in cookie or "01 Jan 1970" in cookie


async def test_setup_is_closed_once_an_account_exists(app_client) -> None:
    http, _ = await app_client()
    async with http:
        response = await http.get("/setup", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"


async def test_last_administrator_cannot_be_demoted(app_client) -> None:
    http, app = await app_client()
    async with app.state.sessions() as session:
        from sqlalchemy import select

        from aptly_gui.db import User

        user = (await session.execute(select(User))).scalars().first()
        assert user is not None
        user_id = user.id
    async with http:
        response = await http.post(
            f"/users/{user_id}/role",
            data={"csrf_token": CSRF, "role": "viewer"},
            follow_redirects=False,
        )
    assert response.headers["location"] == "/users?error=last_admin"
    async with app.state.sessions() as session:
        from aptly_gui.db import User

        again = await session.get(User, user_id)
        assert again is not None
        assert again.role == UserRole.ADMIN


async def test_last_administrator_cannot_be_deactivated(app_client) -> None:
    http, app = await app_client()
    async with app.state.sessions() as session:
        from sqlalchemy import select

        from aptly_gui.db import User

        user = (await session.execute(select(User))).scalars().first()
        assert user is not None
        user_id = user.id
    async with http:
        response = await http.post(
            f"/users/{user_id}/active",
            data={"csrf_token": CSRF, "active": "0"},
            follow_redirects=False,
        )
    assert response.headers["location"] == "/users?error=last_admin"


async def test_oidc_start_without_configuration_goes_back_to_login(app_client) -> None:
    http, _ = await app_client()
    async with http:
        response = await http.get("/auth/oidc/start", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_next_is_confined_to_this_site() -> None:
    """An open redirect here would turn the login page into a phishing hop."""
    from aptly_gui.web.app import _safe_next

    assert _safe_next("/sets") == "/sets"
    assert _safe_next("https://evil.example/") == "/"
    assert _safe_next("//evil.example/") == "/"
    assert _safe_next("not-a-path") == "/"


def _unused(value: Any) -> None:
    return None
