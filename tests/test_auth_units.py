from __future__ import annotations

import base64
import hashlib

import pytest

from aptly_gui.auth import (
    challenge_for,
    csrf_matches,
    describe_weakness,
    hash_password,
    is_public,
    make_verifier,
    needs_rehash,
    required_role,
    verify_password,
)
from aptly_gui.auth.oidc import OidcClient, OidcConfig
from aptly_gui.db import UserRole

# --- passwords --------------------------------------------------------------


def test_password_round_trip() -> None:
    stored = hash_password("a-long-enough-password")
    assert stored != "a-long-enough-password"
    assert verify_password(stored, "a-long-enough-password")
    assert not verify_password(stored, "something else")
    assert not needs_rehash(stored)


def test_account_without_a_password_never_matches() -> None:
    """An OIDC account has no local hash; nothing should authenticate against it."""
    assert not verify_password(None, "")
    assert not verify_password("", "anything")


def test_short_passwords_are_rejected() -> None:
    assert describe_weakness("short") == "too_short"
    assert describe_weakness("0123456789") is None


# --- roles ------------------------------------------------------------------


def test_role_ordering() -> None:
    assert UserRole.ADMIN.can(UserRole.OPERATOR)
    assert UserRole.OPERATOR.can(UserRole.VIEWER)
    assert not UserRole.VIEWER.can(UserRole.OPERATOR)
    assert not UserRole.OPERATOR.can(UserRole.ADMIN)


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("GET", "/", UserRole.VIEWER),
        ("GET", "/sets", UserRole.VIEWER),
        ("POST", "/sets/1/update", UserRole.OPERATOR),
        ("POST", "/snapshots/discard", UserRole.OPERATOR),
        ("POST", "/settings", UserRole.ADMIN),
        # Who may sign in is not viewer-readable information.
        ("GET", "/users", UserRole.ADMIN),
        ("GET", "/settings", UserRole.ADMIN),
        ("POST", "/users/1/role", UserRole.ADMIN),
    ],
)
def test_required_role(method: str, path: str, expected: UserRole) -> None:
    assert required_role(method, path) == expected


def test_unclassified_writes_default_to_admin() -> None:
    """A route nobody remembered to classify should be too strict, not too loose."""
    assert required_role("POST", "/something-new") == UserRole.ADMIN


@pytest.mark.parametrize("path", ["/login", "/logout", "/auth/oidc/start", "/static/x", "/healthz"])
def test_public_paths(path: str) -> None:
    assert is_public(path)


@pytest.mark.parametrize("path", ["/", "/sets", "/users", "/jobs"])
def test_private_paths(path: str) -> None:
    assert not is_public(path)


# --- csrf -------------------------------------------------------------------


def test_csrf_needs_both_halves() -> None:
    assert csrf_matches("token", "token")
    assert not csrf_matches("token", "other")
    assert not csrf_matches(None, "token")
    assert not csrf_matches("token", None)
    assert not csrf_matches("", "")


# --- oidc -------------------------------------------------------------------


def test_pkce_challenge_is_s256_of_the_verifier() -> None:
    verifier = make_verifier()
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    expected = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    assert challenge_for(verifier) == expected
    assert "=" not in challenge_for(verifier)


def _client(**overrides: object) -> OidcClient:
    config = OidcConfig(
        issuer="https://id.example",
        client_id="aptly-gui",
        client_secret="secret",
        **overrides,  # type: ignore[arg-type]
    )
    return OidcClient(config)


def test_username_falls_back_through_claims() -> None:
    client = _client()
    assert client.username_from({"preferred_username": "jana"}) == "jana"
    assert client.username_from({"email": "jana@example.invalid"}) == "jana@example.invalid"
    assert client.username_from({"sub": "abc-123"}) == "abc-123"


def test_groups_map_to_roles() -> None:
    client = _client(admin_group="mirror-admins", operator_group="mirror-operators")
    assert client.role_from({"groups": ["mirror-admins"]}) == UserRole.ADMIN
    assert client.role_from({"groups": ["mirror-operators"]}) == UserRole.OPERATOR
    assert client.role_from({"groups": ["something-else"]}) == UserRole.VIEWER
    assert client.role_from({}) == UserRole.VIEWER


def test_admin_group_wins_over_operator() -> None:
    client = _client(admin_group="a", operator_group="o")
    assert client.role_from({"groups": ["o", "a"]}) == UserRole.ADMIN


def test_default_role_applies_when_no_groups_configured() -> None:
    client = _client(default_role=UserRole.OPERATOR)
    assert client.role_from({"groups": ["anything"]}) == UserRole.OPERATOR


def test_oidc_is_off_without_configuration() -> None:
    assert not OidcConfig(issuer="", client_id="", client_secret="").enabled
    assert OidcConfig(issuer="https://id", client_id="x", client_secret="").enabled
