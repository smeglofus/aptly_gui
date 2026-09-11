from __future__ import annotations

from dataclasses import dataclass

import httpx

SIGNED_MARKER = "-----BEGIN PGP SIGNED MESSAGE-----"


@dataclass(frozen=True)
class ClientCheck:
    """What a client actually gets, rather than what aptly believes it published."""

    url: str
    status: int | None
    signed: bool
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == 200 and self.signed

    def describe(self) -> str:
        if self.error:
            return f"{self.url}: {self.error}"
        if self.status != 200:
            return f"{self.url}: HTTP {self.status}"
        if not self.signed:
            return f"{self.url}: fetched but not signed"
        return f"{self.url}: signed and reachable"


async def check_release(
    public_url: str, distribution: str, *, timeout: float = 15.0
) -> ClientCheck:
    """Fetch InRelease the way apt would.

    A successful API call only means aptly wrote files; it says nothing about whether
    the web server in front of them serves the result, which is what breaks in practice.
    """
    url = f"{public_url.rstrip('/')}/dists/{distribution}/InRelease"
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as http:
            response = await http.get(url)
    except httpx.HTTPError as exc:
        return ClientCheck(url=url, status=None, signed=False, error=str(exc) or type(exc).__name__)
    return ClientCheck(
        url=url,
        status=response.status_code,
        signed=SIGNED_MARKER in response.text[:2048],
    )
