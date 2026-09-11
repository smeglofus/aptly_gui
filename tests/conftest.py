from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest

from aptly_gui.aptly import AptlyClient

DEFAULT_API_URL = "http://127.0.0.1:8079"


@pytest.fixture
async def aptly() -> AsyncIterator[AptlyClient]:
    url = os.environ.get("APTLY_API_URL", DEFAULT_API_URL)
    client = AptlyClient(url)
    try:
        if not await client.is_ready():
            pytest.skip(f"no aptly API at {url} (start it with: cd demo && docker compose up -d)")
        yield client
    finally:
        await client.aclose()
