from __future__ import annotations

from ..db import UserRole

# Paths reachable without a session at all.
PUBLIC_PREFIXES = ("/static", "/login", "/logout", "/auth/", "/setup")
PUBLIC_EXACT = ("/healthz",)

# Reserved for administrators whatever the method: who may sign in and how the
# instance is configured is not something a viewer should be able to read either.
ADMIN_PREFIXES = ("/settings", "/users")


def is_public(path: str) -> bool:
    return path in PUBLIC_EXACT or path.startswith(PUBLIC_PREFIXES)


def required_role(method: str, path: str) -> UserRole:
    """Least privilege that may perform this request.

    Unknown writes fall through to admin rather than operator: a new route that
    nobody remembered to classify should be too restricted, not too permissive.
    """
    if path.startswith(ADMIN_PREFIXES):
        return UserRole.ADMIN
    if method in ("GET", "HEAD", "OPTIONS"):
        return UserRole.VIEWER
    if path.startswith(("/sets", "/snapshots")):
        return UserRole.OPERATOR
    return UserRole.ADMIN
