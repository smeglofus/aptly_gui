from __future__ import annotations

from dataclasses import dataclass

from ..aptly import Storage

GIB = 1024


@dataclass(frozen=True)
class Margin:
    """How much of the disk a sync must leave alone.

    Both a fixed and a proportional floor, because neither alone travels well: 15 % of
    a 4 TB array is far more than anyone needs, and 30 GB of a 200 GB disk is not much.
    """

    gigabytes: int
    percent: int

    def required_free_mb(self, total_mb: int) -> int:
        return max(self.gigabytes * GIB, total_mb * self.percent // 100)


@dataclass(frozen=True)
class Verdict:
    ok: bool
    free_mb: int
    required_mb: int
    needed_mb: int | None = None

    @property
    def shortfall_mb(self) -> int:
        return max(0, self.required_mb + (self.needed_mb or 0) - self.free_mb)


def check(storage: Storage, margin: Margin, *, download_mb: int | None = None) -> Verdict:
    """Decide whether there is room to proceed.

    `download_mb` is what aptly says it is about to fetch, once it knows; before that
    the check is only against the margin.
    """
    required = margin.required_free_mb(storage.total_mb)
    needed = download_mb or 0
    return Verdict(
        ok=storage.free_mb >= required + needed,
        free_mb=storage.free_mb,
        required_mb=required,
        needed_mb=download_mb,
    )
