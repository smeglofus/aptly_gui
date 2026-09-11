from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from aptly_gui.aptly import TaskProgress
from aptly_gui.db import JobStep, StepState

SAMPLE = {
    "TotalDownloadSize": 1437811898,
    "RemainingDownloadSize": 1069152444,
    "TotalNumberOfPackages": 303,
    "RemainingNumberOfPackages": 150,
}


def test_parses_a_real_aptly_payload() -> None:
    progress = TaskProgress.parse(SAMPLE)
    assert progress is not None
    assert progress.downloaded_bytes == 1437811898 - 1069152444
    assert progress.done_packages == 153
    assert 25 < progress.percent < 26


@pytest.mark.parametrize("payload", [{}, {"Foo": 1}])
def test_task_without_downloads_reports_nothing(payload: dict[str, int]) -> None:
    """Cleanup and publish tasks return an empty object; that is not zero progress."""
    assert TaskProgress.parse(payload) is None


def _step(**kwargs: object) -> JobStep:
    defaults: dict[str, object] = {
        "position": 1,
        "description": "Sync x",
        "state": StepState.RUNNING,
        "started_at": datetime.now(UTC) - timedelta(seconds=100),
    }
    return JobStep(**{**defaults, **kwargs})


def test_step_without_progress_shows_none() -> None:
    step = _step()
    assert not step.has_progress
    assert step.bytes_per_second is None
    assert step.eta_seconds is None


def test_step_computes_speed_and_eta() -> None:
    step = _step(total_bytes=1000, remaining_bytes=400, total_packages=10, remaining_packages=4)
    assert step.downloaded_bytes == 600
    assert step.done_packages == 6
    assert step.percent == 60
    # 600 bytes over ~100 s, so roughly 6 B/s and ~66 s for the remaining 400.
    assert step.bytes_per_second == pytest.approx(6, rel=0.05)
    assert step.eta_seconds == pytest.approx(66, rel=0.05)


def test_speed_is_withheld_until_there_is_enough_to_measure() -> None:
    step = _step(
        started_at=datetime.now(UTC),
        total_bytes=1000,
        remaining_bytes=999,
        total_packages=1,
        remaining_packages=1,
    )
    assert step.bytes_per_second is None


def test_finished_step_without_download_reads_as_complete() -> None:
    step = _step(state=StepState.SUCCEEDED, finished_at=datetime.now(UTC))
    assert step.percent == 100.0


def test_percent_never_exceeds_one_hundred() -> None:
    step = _step(total_bytes=100, remaining_bytes=-50)
    assert step.percent == 100.0


def test_expected_steps_for_an_ubuntu_sized_update() -> None:
    """Counting up front stops the overall bar jumping as steps are created."""
    from aptly_gui.db import JobType, MirrorSet
    from aptly_gui.services.jobs import _expected_steps

    mirror_set = MirrorSet(
        name="ubuntu-noble",
        archive_url="http://archive.ubuntu.com/ubuntu",
        suites=["noble", "noble-updates", "noble-security", "noble-backports"],
        components=["main", "restricted", "universe", "multiverse"],
        architectures=["amd64"],
        publish_prefix="ubuntu",
    )
    # 16 mirrors: one preflight, then a sync and a snapshot each.
    assert _expected_steps(JobType.UPDATE, mirror_set) == 33
    # One publish per suite, then a verify.
    assert _expected_steps(JobType.SWITCH, mirror_set) == 5
