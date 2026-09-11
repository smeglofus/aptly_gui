from __future__ import annotations

import base64
import hashlib
import os
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from ..db import UserRole


class OidcError(RuntimeError):
    pass


@dataclass(frozen=True)
class OidcConfig:
    issuer: str
    client_id: str
    client_secret: str
    scopes: str = "openid profile email"
    username_claim: str = "preferred_username"
    groups_claim: str = "groups"
    admin_group: str | None = None
    operator_group: str | None = None
    default_role: str = UserRole.VIEWER
    base_url: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.issuer and self.client_id)

    @classmethod
    def from_env(cls) -> OidcConfig:
        return cls(
            issuer=os.environ.get("APTLY_GUI_OIDC_ISSUER", "").rstrip("/"),
            client_id=os.environ.get("APTLY_GUI_OIDC_CLIENT_ID", ""),
            client_secret=os.environ.get("APTLY_GUI_OIDC_CLIENT_SECRET", ""),
            scopes=os.environ.get("APTLY_GUI_OIDC_SCOPES", cls.scopes),
            username_claim=os.environ.get("APTLY_GUI_OIDC_USERNAME_CLAIM", cls.username_claim),
            groups_claim=os.environ.get("APTLY_GUI_OIDC_GROUPS_CLAIM", cls.groups_claim),
            admin_group=os.environ.get("APTLY_GUI_OIDC_ADMIN_GROUP") or None,
            operator_group=os.environ.get("APTLY_GUI_OIDC_OPERATOR_GROUP") or None,
            default_role=os.environ.get("APTLY_GUI_OIDC_DEFAULT_ROLE", cls.default_role),
            base_url=os.environ.get("APTLY_GUI_BASE_URL") or None,
        )


def make_verifier() -> str:
    return secrets.token_urlsafe(64)


def challenge_for(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


class OidcClient:
    """Authorization code flow with PKCE against a discovered provider.

    Claims come from the userinfo endpoint rather than the id_token: it is an
    authenticated call over TLS, which gives the same assurance without this service
    having to get JWT signature validation right.
    """

    def __init__(self, config: OidcConfig, *, timeout: float = 15.0) -> None:
        self.config = config
        self._timeout = timeout
        self._metadata: dict[str, Any] | None = None

    async def metadata(self) -> dict[str, Any]:
        if self._metadata is None:
            url = f"{self.config.issuer}/.well-known/openid-configuration"
            async with httpx.AsyncClient(timeout=self._timeout) as http:
                response = await http.get(url)
            if response.status_code != 200:
                raise OidcError(f"discovery failed at {url}: HTTP {response.status_code}")
            metadata = response.json()
            for field in ("authorization_endpoint", "token_endpoint", "userinfo_endpoint"):
                if not metadata.get(field):
                    raise OidcError(f"provider does not advertise {field}")
            self._metadata = metadata
        return self._metadata

    async def authorization_url(self, *, state: str, verifier: str, redirect_uri: str) -> str:
        metadata = await self.metadata()
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.config.client_id,
                "redirect_uri": redirect_uri,
                "scope": self.config.scopes,
                "state": state,
                "code_challenge": challenge_for(verifier),
                "code_challenge_method": "S256",
            }
        )
        return f"{metadata['authorization_endpoint']}?{query}"

    async def exchange(self, *, code: str, verifier: str, redirect_uri: str) -> str:
        metadata = await self.metadata()
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self.config.client_id,
            "code_verifier": verifier,
        }
        if self.config.client_secret:
            data["client_secret"] = self.config.client_secret
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            response = await http.post(metadata["token_endpoint"], data=data)
        if response.status_code != 200:
            raise OidcError(f"token exchange failed: HTTP {response.status_code}")
        token = response.json().get("access_token")
        if not token:
            raise OidcError("provider returned no access token")
        return str(token)

    async def userinfo(self, access_token: str) -> dict[str, Any]:
        metadata = await self.metadata()
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            response = await http.get(
                metadata["userinfo_endpoint"],
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if response.status_code != 200:
            raise OidcError(f"userinfo failed: HTTP {response.status_code}")
        claims = response.json()
        if not isinstance(claims, dict):
            raise OidcError("userinfo did not return an object")
        return claims

    def username_from(self, claims: dict[str, Any]) -> str:
        for key in (self.config.username_claim, "preferred_username", "email", "sub"):
            value = claims.get(key)
            if isinstance(value, str) and value:
                return value
        raise OidcError("no usable username claim in userinfo")

    def role_from(self, claims: dict[str, Any]) -> UserRole:
        groups = claims.get(self.config.groups_claim) or []
        if isinstance(groups, str):
            groups = [groups]
        members = {str(group) for group in groups}
        if self.config.admin_group and self.config.admin_group in members:
            return UserRole.ADMIN
        if self.config.operator_group and self.config.operator_group in members:
            return UserRole.OPERATOR
        return UserRole(self.config.default_role)
