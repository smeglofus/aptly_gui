"""Snapshots may only be removed where that cannot break something in use."""

from __future__ import annotations

from aptly_gui.services import discard_steps


def test_discard_counts_a_cleanup_step() -> None:
    """Deleting frees nothing until the pool is compacted, so cleanup is part of the job."""
    assert discard_steps(1) == 2
    assert discard_steps(16) == 17
