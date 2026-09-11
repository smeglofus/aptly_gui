from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..aptly import AptlyClient, AptlyError, MirrorSpec, Signing, TaskFailed, TaskState
from ..db import (
    AuditEntry,
    Job,
    JobState,
    JobStep,
    JobType,
    MirrorSet,
    SnapshotSet,
    SnapshotSetState,
    StepState,
)

log = logging.getLogger(__name__)

OUTPUT_TAIL = 4000
SUITE_ORDER_HINT = ("", "-updates", "-security", "-backports")


def snapshot_stamp(moment: datetime | None = None) -> str:
    return (moment or datetime.now(UTC)).strftime("%Y%m%dT%H%MZ")


def order_suites(suites: list[str]) -> list[str]:
    """Base first, then updates, security, backports — the order the concept fixes.

    A partially switched publication is visible to clients, so the sequence is part of
    the behaviour rather than an implementation detail.
    """

    def rank(suite: str) -> tuple[int, str]:
        for index, marker in enumerate(SUITE_ORDER_HINT):
            if marker and suite.endswith(marker):
                return (index, suite)
        return (0, suite)

    return sorted(suites, key=rank)


def _expected_steps(job_type: JobType, mirror_set: MirrorSet | None) -> int:
    """How many steps the job will create, so the overall bar is honest from the start."""
    if mirror_set is None:
        return 1
    mirrors = len(mirror_set.suites) * len(mirror_set.components)
    if job_type is JobType.CREATE:
        return mirrors
    if job_type is JobType.UPDATE:
        return 1 + mirrors * 2  # preflight, then a sync and a snapshot per mirror
    if job_type is JobType.SWITCH:
        return len(mirror_set.suites) + 1  # one publish per suite, then verify
    return 1


def discard_steps(count: int) -> int:
    # One delete per snapshot, then the cleanup that actually frees the disk.
    return count + 1


def spec_for(mirror_set: MirrorSet, suite: str, component: str) -> MirrorSpec:
    return MirrorSpec(
        name=mirror_set.mirror_name(suite, component),
        archive_url=mirror_set.archive_url,
        distribution=suite,
        components=[component],
        architectures=list(mirror_set.architectures),
        filter=mirror_set.filter,
        keyrings=list(mirror_set.keyrings),
    )


class JobRunner:
    """Runs one write job at a time against a single aptly instance.

    A single worker task *is* the global lock from the design: aptly has one database,
    and running two sets concurrently would mean two writers into it.
    """

    def __init__(
        self,
        client: AptlyClient,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        poll_interval: float = 2.0,
    ) -> None:
        self._client = client
        self._sessions = session_factory
        self._poll = poll_interval
        self._queue: asyncio.Queue[int] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._next_position = 0
        self.current_job_id: int | None = None

    # --- lifecycle ------------------------------------------------------

    async def start(self) -> None:
        await self._recover_orphans()
        self._worker = asyncio.create_task(self._run_forever())

    async def stop(self) -> None:
        if self._worker:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker
            self._worker = None

    async def _recover_orphans(self) -> None:
        """Anything left running belongs to a previous process, so it is interrupted.

        aptly's task ids do not survive its restart either, so the stored ids are not
        consulted; what actually happened is read back from aptly in reconcile().
        """
        async with self._sessions() as session:
            stale = (
                await session.execute(
                    select(Job).where(Job.state.in_([JobState.RUNNING, JobState.QUEUED]))
                )
            ).scalars()
            for job in stale:
                job.state = JobState.INTERRUPTED
                job.finished_at = datetime.now(UTC)
                job.error = "Interrupted by a restart of aptly-gui"
            await session.commit()

    # --- queueing -------------------------------------------------------

    async def enqueue(
        self,
        job_type: JobType,
        mirror_set_id: int | None,
        *,
        author: str,
        params: dict[str, object],
    ) -> Job:
        async with self._sessions() as session:
            job = Job(
                type=job_type,
                mirror_set_id=mirror_set_id,
                author=author,
                params=dict(params),
                state=JobState.QUEUED,
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)
        await self._queue.put(job.id)
        return job

    async def _run_forever(self) -> None:
        while True:
            job_id = await self._queue.get()
            try:
                await self._run_job(job_id)
            except Exception:
                log.exception("job %s crashed", job_id)
            finally:
                self.current_job_id = None
                self._queue.task_done()

    # --- execution ------------------------------------------------------

    async def _run_job(self, job_id: int) -> None:
        self.current_job_id = job_id
        self._next_position = 0
        async with self._sessions() as session:
            job = await session.get(Job, job_id)
            if job is None:
                return
            # Discarding snapshots that belong to no set has no mirror set to act on.
            mirror_set = (
                await session.get(MirrorSet, job.mirror_set_id)
                if job.mirror_set_id is not None
                else None
            )
            if mirror_set is None and job.type != JobType.DISCARD:
                return

            job.state = JobState.RUNNING
            job.started_at = datetime.now(UTC)
            job.expected_steps = (
                discard_steps(len(job.params.get("snapshots", [])))
                if job.type == JobType.DISCARD
                else _expected_steps(JobType(job.type), mirror_set)
            )
            await session.commit()

            try:
                if job.type == JobType.DISCARD:
                    await self._discard(session, job)
                elif mirror_set is None:
                    raise ValueError("job has no mirror set")
                elif job.type == JobType.CREATE:
                    await self._create(session, job, mirror_set)
                elif job.type == JobType.UPDATE:
                    await self._update(session, job, mirror_set)
                elif job.type == JobType.SWITCH:
                    await self._switch(session, job, mirror_set)
                else:
                    raise ValueError(f"unknown job type {job.type}")
                job.state = JobState.SUCCEEDED
            except Exception as exc:
                job.state = JobState.FAILED
                job.error = str(exc) or exc.__class__.__name__
                log.warning("job %s failed: %s", job_id, job.error)
            finally:
                job.finished_at = datetime.now(UTC)
                session.add(
                    AuditEntry(
                        actor=job.author,
                        action=f"job.{job.type}",
                        target=mirror_set.name if mirror_set else "snapshots",
                        result=job.state,
                        detail=job.error,
                    )
                )
                await session.commit()

    async def _add_step(self, session: AsyncSession, job: Job, description: str) -> JobStep:
        # Position comes from a counter rather than len(job.steps): touching the
        # relationship would lazy-load inside async code, which SQLAlchemy refuses.
        self._next_position += 1
        step = JobStep(
            job_id=job.id,
            position=self._next_position,
            description=description,
            state=StepState.RUNNING,
            started_at=datetime.now(UTC),
        )
        session.add(step)
        await session.commit()
        return step

    async def _finish_step(
        self,
        session: AsyncSession,
        step: JobStep,
        state: StepState,
        output: str | None = None,
    ) -> None:
        step.state = state
        step.finished_at = datetime.now(UTC)
        if output:
            step.output_tail = output[-OUTPUT_TAIL:]
        await session.commit()

    async def _await_task(self, session: AsyncSession, step: JobStep, task_id: int) -> str:
        """Wait for an aptly task, recording download progress onto the step as it goes.

        aptly only reports progress once it has planned the download, so the first
        polls come back empty and the step simply has no numbers yet.
        """
        while True:
            task = await self._client.get_task(task_id)
            if task.state.finished:
                break
            progress = await self._client.task_progress(task_id)
            if progress is not None:
                step.total_bytes = progress.total_bytes
                step.remaining_bytes = progress.remaining_bytes
                step.total_packages = progress.total_packages
                step.remaining_packages = progress.remaining_packages
                await session.commit()
            await asyncio.sleep(self._poll)

        output = await self._client.task_output(task_id)
        if task.state is TaskState.FAILED:
            raise TaskFailed(task, output)
        if step.total_bytes is not None:
            step.remaining_bytes = 0
            step.remaining_packages = 0
            await session.commit()
        return output

    # --- create: register the mirrors in aptly, without downloading ---

    async def _create(self, session: AsyncSession, job: Job, mirror_set: MirrorSet) -> None:
        """Create one aptly mirror per suite and component.

        aptly fetches and verifies the upstream Release while creating a mirror, so a
        wrong keyring or archive URL fails here rather than hours into a sync.
        """
        existing = {mirror.name for mirror in await self._client.list_mirrors()}
        for suite in order_suites(list(mirror_set.suites)):
            for component in mirror_set.components:
                spec = spec_for(mirror_set, suite, component)
                step = await self._add_step(session, job, f"Create {spec.name}")
                if spec.name in existing:
                    await self._finish_step(
                        session, step, StepState.SKIPPED, "already exists in aptly"
                    )
                    continue
                await self._client.create_mirror(spec)
                await self._finish_step(session, step, StepState.SUCCEEDED)

    # --- update: sync then snapshot, deliberately stopping before publish ---

    async def _update(self, session: AsyncSession, job: Job, mirror_set: MirrorSet) -> None:
        step = await self._add_step(session, job, "Preflight")
        existing = {m.name for m in await self._client.list_mirrors()}
        missing = [name for name in mirror_set.expected_mirrors if name not in existing]
        if missing:
            await self._finish_step(session, step, StepState.FAILED, "\n".join(missing))
            raise AptlyError(f"mirrors missing in aptly: {', '.join(missing)}")
        await self._finish_step(session, step, StepState.SUCCEEDED)

        stamp = snapshot_stamp()
        snapshot_set = SnapshotSet(
            mirror_set_id=mirror_set.id,
            job_id=job.id,
            state=SnapshotSetState.INCOMPLETE,
            snapshots={},
        )
        session.add(snapshot_set)
        await session.commit()

        for suite in order_suites(list(mirror_set.suites)):
            for component in mirror_set.components:
                spec = spec_for(mirror_set, suite, component)
                step = await self._add_step(session, job, f"Sync {spec.name}")
                task = await self._client.update_mirror(spec)
                step.aptly_task_id = task.id
                await session.commit()
                output = await self._await_task(session, step, task.id)
                await self._finish_step(session, step, StepState.SUCCEEDED, output)

        taken: dict[str, str] = {}
        for suite in order_suites(list(mirror_set.suites)):
            for component in mirror_set.components:
                mirror_name = mirror_set.mirror_name(suite, component)
                snapshot_name = f"{mirror_name}-{stamp}"
                step = await self._add_step(session, job, f"Snapshot {snapshot_name}")
                await self._client.create_snapshot_from_mirror(
                    mirror_name, snapshot_name, description=f"aptly-gui job {job.id}"
                )
                taken[mirror_name] = snapshot_name
                snapshot_set.snapshots = dict(taken)
                await self._finish_step(session, step, StepState.SUCCEEDED)

        snapshot_set.state = SnapshotSetState.COMPLETE
        await session.commit()

    # --- discard: delete snapshots and actually reclaim the space ---

    async def _discard(self, session: AsyncSession, job: Job) -> None:
        """Drop the named snapshots, then compact.

        Deleting a snapshot frees nothing on its own — the pool keeps the files until
        a database cleanup runs — so the cleanup is part of the job rather than
        something the operator has to remember.
        """
        names = [str(name) for name in job.params.get("snapshots", [])]
        for name in names:
            step = await self._add_step(session, job, f"Delete {name}")
            try:
                await self._client.delete_snapshot(name)
            except AptlyError as exc:
                await self._finish_step(session, step, StepState.FAILED, str(exc))
                raise
            await self._finish_step(session, step, StepState.SUCCEEDED)

        step = await self._add_step(session, job, "Reclaim disk space")
        task = await self._client.db_cleanup()
        step.aptly_task_id = task.id
        await session.commit()
        output = await self._await_task(session, step, task.id)
        await self._finish_step(session, step, StepState.SUCCEEDED, output)

        set_id = job.params.get("snapshot_set_id")
        if set_id is not None:
            snapshot_set = await session.get(SnapshotSet, int(set_id))
            if snapshot_set is not None:
                await session.delete(snapshot_set)
                await session.commit()

    # --- switch: point publications at a chosen snapshot set ---

    async def _switch(self, session: AsyncSession, job: Job, mirror_set: MirrorSet) -> None:
        target_id = int(job.params["snapshot_set_id"])
        target = await session.get(SnapshotSet, target_id)
        if target is None or target.mirror_set_id != mirror_set.id:
            raise ValueError("snapshot set does not belong to this mirror set")
        if target.state != SnapshotSetState.COMPLETE:
            raise ValueError("refusing to publish an incomplete snapshot set")

        published = {(p.prefix, p.distribution): p for p in await self._client.list_published()}
        signing = Signing(gpg_key=mirror_set.signing_key)

        for suite in order_suites(list(mirror_set.suites)):
            sources = {
                component: target.snapshots[mirror_set.mirror_name(suite, component)]
                for component in mirror_set.components
            }
            key = (mirror_set.publish_prefix, suite)
            step = await self._add_step(
                session, job, f"Publish {mirror_set.publish_prefix}/{suite}"
            )
            if key in published:
                task = await self._client.switch_published(
                    mirror_set.publish_prefix, suite, sources, signing=signing
                )
            else:
                task = await self._client.publish_snapshots(
                    mirror_set.publish_prefix,
                    suite,
                    sources,
                    architectures=list(mirror_set.architectures),
                    signing=signing,
                )
            step.aptly_task_id = task.id
            await session.commit()
            output = await self._await_task(session, step, task.id)
            await self._finish_step(session, step, StepState.SUCCEEDED, output)

        step = await self._add_step(session, job, "Verify")
        actual = {(p.prefix, p.distribution): p for p in await self._client.list_published()}
        problems = []
        for suite in mirror_set.suites:
            pub = actual.get((mirror_set.publish_prefix, suite))
            if pub is None:
                problems.append(f"{mirror_set.publish_prefix}/{suite} is not published")
                continue
            for component in mirror_set.components:
                expected = target.snapshots[mirror_set.mirror_name(suite, component)]
                if pub.snapshot_for(component) != expected:
                    problems.append(f"{suite}/{component} points at {pub.snapshot_for(component)}")
        if problems:
            await self._finish_step(session, step, StepState.FAILED, "\n".join(problems))
            raise AptlyError("; ".join(problems))
        await self._finish_step(session, step, StepState.SUCCEEDED)


async def reconcile(
    client: AptlyClient, session: AsyncSession, snapshot_set: SnapshotSet
) -> SnapshotSetState:
    """Work out what actually happened by asking aptly, not by replaying task ids."""
    existing = {s.name for s in await client.list_snapshots()}
    present = {
        mirror: snapshot
        for mirror, snapshot in snapshot_set.snapshots.items()
        if snapshot in existing
    }
    expected = len(snapshot_set.mirror_set.suites) * len(snapshot_set.mirror_set.components)
    snapshot_set.snapshots = present
    snapshot_set.state = (
        SnapshotSetState.COMPLETE if len(present) == expected else SnapshotSetState.INCOMPLETE
    )
    await session.commit()
    return SnapshotSetState(snapshot_set.state)
