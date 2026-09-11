"""The overview counts are the obvious thing to click, so they have to lead somewhere."""

from __future__ import annotations

import re

import pytest

TARGETS = ["/sets", "/mirrors", "/snapshots", "/publications", "/snapshots?state=unpublished"]


async def test_every_count_on_the_overview_is_a_link(app_client) -> None:
    http, _ = await app_client()
    async with http:
        response = await http.get("/")
    cards = re.findall(r'<a class="card[^"]*" href="([^"]+)"', response.text)
    assert cards == TARGETS


async def test_unfiltered_snapshots_show_everything(app_client) -> None:
    http, _ = await app_client()
    async with http:
        response = await http.get("/snapshots")
    assert "ubuntu-noble-noble-main-20260911T0800Z" in response.text
    assert "Show all" not in response.text


async def test_unpublished_filter_hides_the_published_one(app_client) -> None:
    """The card says how many are unpublished, so the page it opens must agree."""
    http, _ = await app_client()
    async with http:
        response = await http.get("/snapshots?state=unpublished")
    # The fixture's only snapshot is published, so the filtered list is empty.
    assert "ubuntu-noble-noble-main-20260911T0800Z" not in response.text
    assert "Show all" in response.text


async def test_published_filter_keeps_it(app_client) -> None:
    http, _ = await app_client()
    async with http:
        response = await http.get("/snapshots?state=published")
    assert "ubuntu-noble-noble-main-20260911T0800Z" in response.text


@pytest.mark.parametrize("value", ["nonsense", "", "published; drop table"])
async def test_an_unknown_filter_falls_back_to_everything(app_client, value: str) -> None:
    http, _ = await app_client()
    async with http:
        response = await http.get(f"/snapshots?state={value}")
    assert response.status_code == 200
    assert "ubuntu-noble-noble-main-20260911T0800Z" in response.text
