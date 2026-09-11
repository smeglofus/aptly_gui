"""Full mirror lifecycle against a live aptly.

Mirrors the workflow from KONCEPT.md chapter 7: sync, snapshot, publish, switch,
rollback. Uses a filter so the whole run downloads a single package.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest

from aptly_gui.aptly import AptlyClient, AptlyError, MirrorSpec, Signing

pytestmark = pytest.mark.integration

KEYRING = "/usr/share/keyrings/debian-archive-keyring.gpg"
SIGNING = Signing(gpg_key="demo@example.invalid")
SYNC_TIMEOUT = 300.0


def _spec(name: str) -> MirrorSpec:
    return MirrorSpec(
        name=name,
        archive_url="http://deb.debian.org/debian",
        distribution="trixie",
        components=["main"],
        architectures=["amd64"],
        filter="nginx",
        keyrings=[KEYRING],
    )


@pytest.fixture
async def synced_mirror(aptly: AptlyClient) -> AsyncIterator[MirrorSpec]:
    spec = _spec(f"test-{uuid.uuid4().hex[:8]}")
    await aptly.create_mirror(spec)
    try:
        task = await aptly.update_mirror(spec)
        await aptly.wait_for_task(task.id, timeout=SYNC_TIMEOUT)
        yield spec
    finally:
        await aptly.delete_mirror(spec.name, force=True)


async def test_server_is_reachable(aptly: AptlyClient) -> None:
    assert await aptly.version()
    assert (await aptly.storage()).free_mb > 0


async def test_update_records_download_time(aptly: AptlyClient, synced_mirror: MirrorSpec) -> None:
    mirror = await aptly.get_mirror(synced_mirror.name)
    assert mirror.last_download is not None


async def test_update_without_keyring_fails(aptly: AptlyClient) -> None:
    """Guards the aptly quirk that Keyrings are forgotten between create and update."""
    spec = _spec(f"test-{uuid.uuid4().hex[:8]}")
    await aptly.create_mirror(spec)
    try:
        stripped = MirrorSpec(**{**spec.__dict__, "keyrings": []})
        task = await aptly.update_mirror(stripped)
        with pytest.raises(AptlyError, match="signature"):
            await aptly.wait_for_task(task.id, timeout=SYNC_TIMEOUT)
    finally:
        await aptly.delete_mirror(spec.name, force=True)


async def test_publish_switch_and_rollback(aptly: AptlyClient, synced_mirror: MirrorSpec) -> None:
    prefix = f"t{uuid.uuid4().hex[:8]}"
    first = f"{synced_mirror.name}-first"
    second = f"{synced_mirror.name}-second"

    await aptly.create_snapshot_from_mirror(synced_mirror.name, first)
    await aptly.create_snapshot_from_mirror(synced_mirror.name, second)

    try:
        task = await aptly.publish_snapshots(
            prefix,
            "trixie",
            {"main": first},
            architectures=["amd64"],
            signing=SIGNING,
        )
        await aptly.wait_for_task(task.id, timeout=SYNC_TIMEOUT)
        assert _published(await aptly.list_published(), prefix) == first

        task = await aptly.switch_published(prefix, "trixie", {"main": second}, signing=SIGNING)
        await aptly.wait_for_task(task.id, timeout=SYNC_TIMEOUT)
        assert _published(await aptly.list_published(), prefix) == second

        task = await aptly.switch_published(prefix, "trixie", {"main": first}, signing=SIGNING)
        await aptly.wait_for_task(task.id, timeout=SYNC_TIMEOUT)
        assert _published(await aptly.list_published(), prefix) == first
    finally:
        task = await aptly.drop_published(prefix, "trixie", force=True)
        await aptly.wait_for_task(task.id, timeout=SYNC_TIMEOUT)
        for name in (first, second):
            await aptly.delete_snapshot(name, force=True)


async def test_published_snapshot_cannot_be_deleted(
    aptly: AptlyClient, synced_mirror: MirrorSpec
) -> None:
    """Retention can lean on aptly's own guard instead of reimplementing it."""
    prefix = f"t{uuid.uuid4().hex[:8]}"
    name = f"{synced_mirror.name}-held"
    await aptly.create_snapshot_from_mirror(synced_mirror.name, name)
    task = await aptly.publish_snapshots(
        prefix, "trixie", {"main": name}, architectures=["amd64"], signing=SIGNING
    )
    await aptly.wait_for_task(task.id, timeout=SYNC_TIMEOUT)
    try:
        with pytest.raises(AptlyError, match="published"):
            await aptly.delete_snapshot(name)
    finally:
        task = await aptly.drop_published(prefix, "trixie", force=True)
        await aptly.wait_for_task(task.id, timeout=SYNC_TIMEOUT)
        await aptly.delete_snapshot(name, force=True)


async def test_db_cleanup_runs(aptly: AptlyClient) -> None:
    task = await aptly.db_cleanup()
    assert (await aptly.wait_for_task(task.id, timeout=SYNC_TIMEOUT)).state.finished


def _published(published: list, prefix: str) -> str | None:
    entry = next((p for p in published if p.prefix == prefix), None)
    return entry.snapshot_for("main") if entry else None
