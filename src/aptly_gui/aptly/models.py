from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from typing import Any

# aptly reports a mirror that has never been downloaded with this timestamp.
_NEVER = "0001-01-01T00:00:00Z"


class TaskState(IntEnum):
    QUEUED = 0
    RUNNING = 1
    SUCCEEDED = 2
    FAILED = 3

    @property
    def finished(self) -> bool:
        return self in (TaskState.SUCCEEDED, TaskState.FAILED)


@dataclass(frozen=True)
class Task:
    id: int
    name: str
    state: TaskState

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> Task:
        return cls(id=raw["ID"], name=raw["Name"], state=TaskState(raw["State"]))


@dataclass(frozen=True)
class TaskProgress:
    """Download progress aptly reports for a running mirror update.

    Only tasks that download anything report this; everything else returns an empty
    object, which parses to None rather than a row of zeroes.
    """

    total_bytes: int
    remaining_bytes: int
    total_packages: int
    remaining_packages: int

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> TaskProgress | None:
        if not raw or "TotalDownloadSize" not in raw:
            return None
        return cls(
            total_bytes=raw["TotalDownloadSize"],
            remaining_bytes=raw["RemainingDownloadSize"],
            total_packages=raw["TotalNumberOfPackages"],
            remaining_packages=raw["RemainingNumberOfPackages"],
        )

    @property
    def downloaded_bytes(self) -> int:
        return max(0, self.total_bytes - self.remaining_bytes)

    @property
    def done_packages(self) -> int:
        return max(0, self.total_packages - self.remaining_packages)

    @property
    def percent(self) -> float:
        if self.total_bytes <= 0:
            return 100.0 if self.remaining_packages == 0 else 0.0
        return min(100.0, 100.0 * self.downloaded_bytes / self.total_bytes)


@dataclass(frozen=True)
class Storage:
    total_mb: int
    free_mb: int
    percent_full: float

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> Storage:
        return cls(
            total_mb=raw["Total"],
            free_mb=raw["Free"],
            percent_full=raw["PercentFull"],
        )


@dataclass(frozen=True)
class MirrorSpec:
    """Everything needed to create *and* re-run a mirror.

    aptly does not remember `Keyrings` across updates, so the full spec has to be
    resent on every sync. Keeping it in one object is what stops a partial update
    from silently failing signature verification.
    """

    name: str
    archive_url: str
    distribution: str
    components: list[str]
    architectures: list[str]
    keyrings: list[str] = field(default_factory=list)
    filter: str | None = None
    filter_with_deps: bool = False
    download_sources: bool = False
    download_udebs: bool = False
    ignore_signatures: bool = False

    def payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "Name": self.name,
            "ArchiveURL": self.archive_url,
            "Distribution": self.distribution,
            "Components": self.components,
            "Architectures": self.architectures,
            "Keyrings": self.keyrings,
            "FilterWithDeps": self.filter_with_deps,
            "DownloadSources": self.download_sources,
            "DownloadUdebs": self.download_udebs,
            "IgnoreSignatures": self.ignore_signatures,
        }
        if self.filter:
            body["Filter"] = self.filter
        return body


@dataclass(frozen=True)
class Mirror:
    name: str
    archive_url: str
    distribution: str
    components: list[str]
    architectures: list[str]
    filter: str | None
    last_download: datetime | None

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> Mirror:
        return cls(
            name=raw["Name"],
            archive_url=raw["ArchiveRoot"],
            distribution=raw["Distribution"],
            components=raw.get("Components") or [],
            architectures=raw.get("Architectures") or [],
            filter=raw.get("Filter") or None,
            last_download=_parse_time(raw.get("LastDownloadDate")),
        )


@dataclass(frozen=True)
class Snapshot:
    name: str
    created_at: datetime | None
    description: str

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> Snapshot:
        return cls(
            name=raw["Name"],
            created_at=_parse_time(raw.get("CreatedAt")),
            description=raw.get("Description") or "",
        )


@dataclass(frozen=True)
class PublishedSource:
    component: str
    name: str


@dataclass(frozen=True)
class Published:
    prefix: str
    distribution: str
    architectures: list[str]
    sources: list[PublishedSource]

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> Published:
        return cls(
            prefix=raw["Prefix"],
            distribution=raw["Distribution"],
            architectures=raw.get("Architectures") or [],
            sources=[
                PublishedSource(component=s["Component"], name=s["Name"])
                for s in raw.get("Sources") or []
            ],
        )

    def snapshot_for(self, component: str) -> str | None:
        return next((s.name for s in self.sources if s.component == component), None)


@dataclass(frozen=True)
class Signing:
    gpg_key: str | None = None
    keyring: str | None = None
    passphrase: str | None = None
    skip: bool = False

    def payload(self) -> dict[str, Any]:
        if self.skip:
            return {"Skip": True}
        body: dict[str, Any] = {"Batch": True}
        if self.gpg_key:
            body["GpgKey"] = self.gpg_key
        if self.keyring:
            body["Keyring"] = self.keyring
        if self.passphrase:
            body["Passphrase"] = self.passphrase
        return body


def _parse_time(value: str | None) -> datetime | None:
    if not value or value == _NEVER:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
