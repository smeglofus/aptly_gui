from __future__ import annotations

import httpx
import pytest

from aptly_gui.aptly import Storage
from aptly_gui.services import Margin, check
from aptly_gui.services.verify import SIGNED_MARKER, check_release

GIB = 1024


def _storage(total_gib: int, free_gib: int) -> Storage:
    return Storage(
        total_mb=total_gib * GIB,
        free_mb=free_gib * GIB,
        percent_full=100 * (total_gib - free_gib) / total_gib,
    )


# --- margin ------------------------------------------------------------------


def test_the_larger_of_the_two_floors_applies() -> None:
    """Neither floor travels alone: 15% of 4 TB is far more than anyone needs, and
    30 GB of a small disk is not much."""
    margin = Margin(gigabytes=30, percent=15)
    assert margin.required_free_mb(100 * GIB) == 30 * GIB  # 15% would be only 15 GB
    assert margin.required_free_mb(1000 * GIB) == 150 * GIB  # 30 GB would be too little


def test_plenty_of_room_passes() -> None:
    verdict = check(_storage(700, 636), Margin(gigabytes=31, percent=15))
    assert verdict.ok
    assert verdict.shortfall_mb == 0


def test_below_the_margin_fails_before_anything_is_downloaded() -> None:
    verdict = check(_storage(700, 20), Margin(gigabytes=31, percent=15))
    assert not verdict.ok
    assert verdict.shortfall_mb > 0


def test_a_download_that_does_not_fit_is_refused() -> None:
    """The real server's estimate was 251 GB wanted against 636 GB free."""
    margin = Margin(gigabytes=31, percent=15)
    assert check(_storage(700, 636), margin, download_mb=251 * GIB).ok
    assert not check(_storage(700, 200), margin, download_mb=251 * GIB).ok


def test_shortfall_says_how_much_is_missing() -> None:
    verdict = check(_storage(700, 120), Margin(gigabytes=30, percent=0), download_mb=200 * GIB)
    # 200 wanted + 30 margin against 120 free leaves 110 GiB short.
    assert verdict.shortfall_mb == 110 * GIB


# --- client-side check -------------------------------------------------------


async def test_a_signed_release_passes(monkeypatch) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/ubuntu/dists/noble/InRelease"
        return httpx.Response(200, text=f"{SIGNED_MARKER}\nHash: SHA256\n")

    _patch_transport(monkeypatch, handler)
    result = await check_release("http://mirror.example/ubuntu", "noble")
    assert result.ok
    assert "signed and reachable" in result.describe()


async def test_a_missing_release_fails(monkeypatch) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="nope")

    _patch_transport(monkeypatch, handler)
    result = await check_release("http://mirror.example/ubuntu", "noble")
    assert not result.ok
    assert "HTTP 404" in result.describe()


async def test_an_unsigned_release_fails(monkeypatch) -> None:
    """Served but unsigned means apt will refuse it, so aptly's own view is not enough."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="Origin: Ubuntu\nSuite: noble\n")

    _patch_transport(monkeypatch, handler)
    result = await check_release("http://mirror.example/ubuntu", "noble")
    assert not result.ok
    assert "not signed" in result.describe()


async def test_an_unreachable_server_is_reported_not_raised(monkeypatch) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    _patch_transport(monkeypatch, handler)
    result = await check_release("http://mirror.example/ubuntu", "noble")
    assert not result.ok
    assert result.status is None
    assert result.error


@pytest.mark.parametrize("base", ["http://m.example/ubuntu", "http://m.example/ubuntu/"])
async def test_a_trailing_slash_does_not_double_up(monkeypatch, base: str) -> None:
    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, text=SIGNED_MARKER)

    _patch_transport(monkeypatch, handler)
    await check_release(base, "noble")
    assert seen == ["http://m.example/ubuntu/dists/noble/InRelease"]


def _patch_transport(monkeypatch, handler) -> None:
    original = httpx.AsyncClient.__init__

    def patched(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        original(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)
