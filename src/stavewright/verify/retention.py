"""Deciding which journals a bench laptop may delete.

A journal is the only record that a terminal was commissioned, so nothing here
deletes anything without being asked twice: the caller works out a plan, looks
at it, and then applies it. There is no function that takes a directory and
removes files.

Four rules, and every one of them is about not losing evidence:

The most recent finished run for each terminal is kept, always, however old.
That one is the answer to "what is on this machine", and a bench laptop with
six years of journals and none for LINE-PC-04 is worse than a full disk.

A run that never finished is kept, always. It is the record that somebody has
to go and look at a machine.

A damaged journal is kept and reported. It cannot be read, so it cannot be
judged, and deleting the one file nobody can read is how a bad disk goes
unnoticed for a year.

Everything else is kept for a number of days and a number of runs per terminal,
whichever keeps more.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path

from stavewright.clock import now as clock_now
from stavewright.errors import VerifyError
from stavewright.verify.history import History, RunSummary

DEFAULT_KEEP_DAYS = 730
"""Two years. Long enough to cover an annual audit and the one before it."""

DEFAULT_KEEP_RUNS = 10
"""Per terminal, on top of the age rule. A machine that is re-commissioned
weekly for a month should not lose the month."""


class Keep(StrEnum):
    """Why a journal is being kept, or that it is not."""

    LATEST = "latest"
    UNFINISHED = "unfinished"
    DAMAGED = "damaged"
    RECENT = "recent"
    WITHIN_COUNT = "within-count"
    EXPIRED = "expired"

    @property
    def is_kept(self) -> bool:
        return self is not Keep.EXPIRED


@dataclass(frozen=True, slots=True)
class Decision:
    """One journal and what is to become of it."""

    run: RunSummary
    keep: Keep

    @property
    def path(self) -> Path:
        return self.run.path

    @property
    def is_kept(self) -> bool:
        return self.keep.is_kept

    def describe(self) -> str:
        verb = "keep" if self.is_kept else "delete"
        return f"{verb} {self.path.name} ({self.keep.value})"


@dataclass(frozen=True, slots=True)
class RetentionPlan:
    """What to keep and what to remove, before anything is removed."""

    decisions: tuple[Decision, ...] = ()

    def __len__(self) -> int:
        return len(self.decisions)

    def __iter__(self) -> Iterator[Decision]:
        return iter(self.decisions)

    @property
    def kept(self) -> list[Decision]:
        return [decision for decision in self.decisions if decision.is_kept]

    @property
    def expired(self) -> list[Decision]:
        return [decision for decision in self.decisions if not decision.is_kept]

    @property
    def paths(self) -> list[Path]:
        """What would be deleted, in the order it would be deleted."""
        return [decision.path for decision in self.expired]

    def counts(self) -> dict[str, int]:
        counted: dict[str, int] = {}
        for decision in self.decisions:
            counted[decision.keep.value] = counted.get(decision.keep.value, 0) + 1
        return dict(sorted(counted.items()))

    def describe(self) -> list[str]:
        return [decision.describe() for decision in self.decisions]


def _sort_key(run: RunSummary) -> tuple[float, str]:
    when = run.started_at.timestamp() if run.started_at else 0.0
    return (-when, run.path.name)


def plan_retention(
    history: History,
    *,
    keep_days: int = DEFAULT_KEEP_DAYS,
    keep_runs: int = DEFAULT_KEEP_RUNS,
    moment: datetime | None = None,
) -> RetentionPlan:
    """Decide the fate of every journal in *history*.

    :param keep_days: how long an ordinary finished run is kept.
    :param keep_runs: how many finished runs per terminal are kept regardless
        of age. Whichever of the two rules keeps a journal, keeps it.
    :raises VerifyError: when either limit is negative, which would mean this
        function had been asked to justify deleting everything.
    """
    if keep_days < 0 or keep_runs < 0:
        raise VerifyError(
            "retention limits cannot be negative",
            context={"keep_days": keep_days, "keep_runs": keep_runs},
        )
    when = moment or clock_now()
    cutoff = when - timedelta(days=keep_days)

    by_terminal: dict[str, list[RunSummary]] = {}
    for run in history:
        by_terminal.setdefault(run.terminal, []).append(run)
    for runs in by_terminal.values():
        runs.sort(key=_sort_key)

    decisions: list[Decision] = []
    for runs in by_terminal.values():
        newest_finished = next((run for run in runs if run.is_complete), None)
        for position, run in enumerate(runs):
            decisions.append(
                Decision(run=run, keep=_decide(run, position, newest_finished, cutoff, keep_runs))
            )
    decisions.sort(key=lambda decision: decision.path.name)
    return RetentionPlan(decisions=tuple(decisions))


def _decide(
    run: RunSummary,
    position: int,
    newest_finished: RunSummary | None,
    cutoff: datetime,
    keep_runs: int,
) -> Keep:
    if not run.is_readable:
        return Keep.DAMAGED
    if run.is_unfinished:
        return Keep.UNFINISHED
    if newest_finished is not None and run.path == newest_finished.path:
        return Keep.LATEST
    if position < keep_runs:
        return Keep.WITHIN_COUNT
    if run.started_at is not None and run.started_at >= cutoff:
        return Keep.RECENT
    return Keep.EXPIRED


def apply_retention(plan: RetentionPlan, *, dry_run: bool = True) -> list[Path]:
    """Remove what the plan says to remove.

    :param dry_run: on by default, and that is deliberate. The caller has to
        say out loud that it means to delete commissioning records.
    :raises VerifyError: when a file cannot be removed. The rest are left
        alone, because a half applied retention run is worse than none.
    """
    removed: list[Path] = []
    for path in plan.paths:
        if dry_run:
            removed.append(path)
            continue
        try:
            path.unlink()
        except OSError as exc:
            raise VerifyError(
                "cannot remove a journal",
                context={"path": str(path), "detail": str(exc), "removed": len(removed)},
            ) from exc
        removed.append(path)
    return removed


__all__ = [
    "DEFAULT_KEEP_DAYS",
    "DEFAULT_KEEP_RUNS",
    "Decision",
    "Keep",
    "RetentionPlan",
    "apply_retention",
    "plan_retention",
]
