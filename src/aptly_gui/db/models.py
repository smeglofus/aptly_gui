from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    return datetime.now(UTC)


class UtcDateTime(TypeDecorator[datetime]):
    """Always hand back timezone-aware UTC.

    SQLite stores no offset, so a plain DateTime column reads back naive and then
    cannot be compared with anything aware.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value if value.tzinfo else value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[str]: JSON}


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"

    @property
    def finished(self) -> bool:
        return self in (JobState.SUCCEEDED, JobState.FAILED)


class JobType(StrEnum):
    UPDATE = "update"
    # Rollback is the same operation as a forward switch, just to an older set.
    SWITCH = "switch"
    CLEANUP = "cleanup"


class StepState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class SnapshotSetState(StrEnum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"


class AppSetting(Base):
    """Key/value application preferences, e.g. the interface language."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255))


class MirrorSet(Base):
    """The GUI's grouping of aptly mirrors, and the source of truth for their definition.

    aptly has no concept of a set, and forgets a mirror's keyrings between updates, so
    everything needed to re-run a sync lives here and is replayed on every job.
    """

    __tablename__ = "mirror_sets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    archive_url: Mapped[str] = mapped_column(String(512))
    suites: Mapped[list[str]] = mapped_column(JSON)
    components: Mapped[list[str]] = mapped_column(JSON)
    architectures: Mapped[list[str]] = mapped_column(JSON)
    keyrings: Mapped[list[str]] = mapped_column(JSON, default=list)
    filter: Mapped[str | None] = mapped_column(String(512), default=None)
    publish_prefix: Mapped[str] = mapped_column(String(128))
    signing_key: Mapped[str | None] = mapped_column(String(128), default=None)
    adopted: Mapped[bool] = mapped_column(default=False)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)

    snapshot_sets: Mapped[list[SnapshotSet]] = relationship(
        back_populates="mirror_set", cascade="all, delete-orphan"
    )

    def mirror_name(self, suite: str, component: str) -> str:
        return f"{self.name}-{suite}-{component}"

    @property
    def expected_mirrors(self) -> list[str]:
        return [
            self.mirror_name(suite, component)
            for suite in self.suites
            for component in self.components
        ]


class SnapshotSet(Base):
    """Snapshots taken from one mirror set in a single run, i.e. one point in time."""

    __tablename__ = "snapshot_sets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mirror_set_id: Mapped[int] = mapped_column(ForeignKey("mirror_sets.id"))
    taken_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    # mirror name -> snapshot name
    snapshots: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String(16), default=SnapshotSetState.INCOMPLETE)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), default=None)

    mirror_set: Mapped[MirrorSet] = relationship(back_populates="snapshot_sets")

    __table_args__ = (UniqueConstraint("mirror_set_id", "taken_at"),)

    def snapshot_for(self, suite: str, component: str, set_name: str) -> str | None:
        return self.snapshots.get(f"{set_name}-{suite}-{component}")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(16))
    mirror_set_id: Mapped[int | None] = mapped_column(ForeignKey("mirror_sets.id"), default=None)
    state: Mapped[str] = mapped_column(String(16), default=JobState.QUEUED)
    author: Mapped[str] = mapped_column(String(128), default="anonymous")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    # Free-form job input, e.g. which snapshot set a rollback targets.
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    steps: Mapped[list[JobStep]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="JobStep.position"
    )
    mirror_set: Mapped[MirrorSet | None] = relationship()


class JobStep(Base):
    __tablename__ = "job_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    position: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(String(255))
    state: Mapped[str] = mapped_column(String(16), default=StepState.PENDING)
    # Kept for audit and live output only; it is worthless after aptly restarts.
    aptly_task_id: Mapped[int | None] = mapped_column(Integer, default=None)
    output_tail: Mapped[str | None] = mapped_column(Text, default=None)
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)

    job: Mapped[Job] = relationship(back_populates="steps")


class AuditEntry(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    actor: Mapped[str] = mapped_column(String(128), default="anonymous")
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(String(255), default="")
    result: Mapped[str] = mapped_column(String(32), default="ok")
    detail: Mapped[str | None] = mapped_column(Text, default=None)
