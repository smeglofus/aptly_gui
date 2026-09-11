from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ..aptly import AptlyClient, Mirror, Published, Snapshot, Storage


@dataclass(frozen=True)
class AptlyState:
    """A consistent read of aptly at one moment.

    Kept whole rather than refreshed per-page so every screen agrees about what it
    is showing, and so a page can still render from the last good read when aptly
    is unreachable.
    """

    fetched_at: datetime
    version: str = ""
    storage: Storage | None = None
    mirrors: list[Mirror] = field(default_factory=list)
    snapshots: list[Snapshot] = field(default_factory=list)
    published: list[Published] = field(default_factory=list)

    @property
    def published_snapshot_names(self) -> set[str]:
        return {source.name for pub in self.published for source in pub.sources}

    @property
    def snapshot_names(self) -> set[str]:
        return {s.name for s in self.snapshots}

    @property
    def unpublished_snapshots(self) -> list[Snapshot]:
        published = self.published_snapshot_names
        return [s for s in self.snapshots if s.name not in published]

    @property
    def missing_snapshots(self) -> list[tuple[Published, str]]:
        """Publications pointing at snapshots that no longer exist.

        Full drift detection needs mirror sets (v0.2); this is the part that can be
        derived from aptly alone.
        """
        known = self.snapshot_names
        return [
            (pub, source.name)
            for pub in self.published
            for source in pub.sources
            if source.name not in known
        ]


@dataclass
class CachedState:
    state: AptlyState
    stale: bool
    error: str | None

    @property
    def age_seconds(self) -> float:
        return (datetime.now(UTC) - self.state.fetched_at).total_seconds()


class StateCache:
    """Holds the last successful read so an aptly outage degrades instead of blanking."""

    def __init__(self, client: AptlyClient, *, refresh_seconds: int) -> None:
        self._client = client
        self._refresh_seconds = refresh_seconds
        self._cached: CachedState | None = None
        self._lock = asyncio.Lock()

    async def get(self, *, force: bool = False) -> CachedState:
        async with self._lock:
            fresh_enough = (
                self._cached is not None
                and not self._cached.stale
                and self._cached.age_seconds < self._refresh_seconds
            )
            if not force and fresh_enough:
                assert self._cached is not None
                return self._cached
            try:
                self._cached = CachedState(state=await self._read(), stale=False, error=None)
            except Exception as exc:  # noqa: BLE001 - any failure means "show stale"
                message = str(exc) or exc.__class__.__name__
                if self._cached is None:
                    self._cached = CachedState(
                        state=AptlyState(fetched_at=datetime.now(UTC)),
                        stale=True,
                        error=message,
                    )
                else:
                    self._cached = CachedState(state=self._cached.state, stale=True, error=message)
            return self._cached

    async def _read(self) -> AptlyState:
        version, storage, mirrors, snapshots, published = await asyncio.gather(
            self._client.version(),
            self._client.storage(),
            self._client.list_mirrors(),
            self._client.list_snapshots(),
            self._client.list_published(),
        )
        return AptlyState(
            fetched_at=datetime.now(UTC),
            version=version,
            storage=storage,
            mirrors=mirrors,
            snapshots=snapshots,
            published=published,
        )
