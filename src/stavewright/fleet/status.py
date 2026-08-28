"""What the roster says about a terminal, against what the journals show.

Two records of the same fact. The roster carries a ``commissioned`` date
somebody typed, and the journal directory carries a run record the toolkit
wrote. They disagree more often than anybody expects, and every way they can
disagree means something different:

The roster says a date and there is no run. Either the machine was done before
this toolkit existed, or somebody filled the roster in from a plan rather than
from the work. Both happen and they are not the same problem.

There is a run and the roster says never. Somebody commissioned a machine and
did not update the roster, which is the common one, and it means the rollout
planner will send the bench back to a machine that is already done.

The last run never wrote an end record. The machine is part commissioned and
somebody has to look at it before anything else touches it. That is the only
standing here that stops work.

The journals win on whether a run happened, because they are written by the
thing that did it. The roster wins on where the machine is, because nothing
else knows.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from stavewright.clock import now as clock_now
from stavewright.fleet.roster import Roster, RosterEntry
from stavewright.verify.history import History, RunSummary

DEFAULT_STALE_AFTER = timedelta(days=365)
"""Past a year a commissioning record is history rather than a description.

Not a rule from anywhere. It is how often the customers on this product
refresh a bundle, so a terminal nobody has touched in longer than that is one
nobody has checked against the current one either.
"""


class Standing(StrEnum):
    """Where a terminal is, once both records have been read."""

    NEVER = "never"
    """No run and no date. Work to do."""

    COMMISSIONED = "commissioned"
    """A run finished, recently enough to still describe the machine."""

    STALE = "stale"
    """A run finished, long enough ago that nobody should trust it."""

    UNFINISHED = "unfinished"
    """A run started and never ended. Nothing else should touch this machine."""

    DISPUTED = "disputed"
    """The roster and the journals do not agree that a run happened."""

    @property
    def needs_work(self) -> bool:
        """Whether the bench has something to do here."""
        return self in (Standing.NEVER, Standing.STALE, Standing.UNFINISHED)

    @property
    def blocks_others(self) -> bool:
        """Whether this machine has to be dealt with before the rest."""
        return self is Standing.UNFINISHED


@dataclass(frozen=True, slots=True)
class TerminalStatus:
    """One terminal, both records, and what they add up to."""

    entry: RosterEntry
    standing: Standing
    last_run: RunSummary | None = None
    note: str = ""

    @property
    def terminal_id(self) -> str:
        return self.entry.terminal_id

    @property
    def last_run_at(self) -> datetime | None:
        return self.last_run.started_at if self.last_run else None

    def describe(self) -> str:
        when = self.last_run_at.date().isoformat() if self.last_run_at else "no run"
        suffix = f" ({self.note})" if self.note else ""
        return f"{self.terminal_id} on {self.entry.line}: {self.standing.value}, {when}{suffix}"


@dataclass(frozen=True, slots=True)
class PlantStatus:
    """Every terminal in a plant, with both records read."""

    plant: str
    entries: tuple[TerminalStatus, ...] = ()

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self) -> Iterator[TerminalStatus]:
        return iter(self.entries)

    def of_standing(self, standing: Standing) -> list[TerminalStatus]:
        return [entry for entry in self.entries if entry.standing is standing]

    @property
    def blocked(self) -> list[TerminalStatus]:
        """Machines that have to be dealt with before anything else runs."""
        return [entry for entry in self.entries if entry.standing.blocks_others]

    @property
    def outstanding(self) -> list[str]:
        """Terminal ids the bench still has work on, in roster order."""
        return [entry.terminal_id for entry in self.entries if entry.standing.needs_work]

    def counts(self) -> dict[str, int]:
        counted: dict[str, int] = {}
        for entry in self.entries:
            counted[entry.standing.value] = counted.get(entry.standing.value, 0) + 1
        return dict(sorted(counted.items()))

    def describe(self) -> list[str]:
        return [entry.describe() for entry in self.entries]


def _standing_for(
    entry: RosterEntry,
    run: RunSummary | None,
    stale_after: timedelta,
    moment: datetime,
) -> tuple[Standing, str]:
    if run is not None and run.is_unfinished:
        return Standing.UNFINISHED, f"run {run.run_id or run.path.name} never ended"
    if run is None:
        if entry.is_commissioned:
            return (
                Standing.DISPUTED,
                "the roster carries a date and there is no run in the journals",
            )
        return Standing.NEVER, ""
    if not entry.is_commissioned:
        return (
            Standing.DISPUTED,
            "the journals carry a run and the roster says this was never done",
        )
    started = run.started_at
    if started is not None and moment - started > stale_after:
        days = (moment - started).days
        return Standing.STALE, f"last run was {days} days ago"
    return Standing.COMMISSIONED, ""


def plant_status(
    roster: Roster,
    history: History,
    *,
    stale_after: timedelta = DEFAULT_STALE_AFTER,
    moment: datetime | None = None,
) -> PlantStatus:
    """Read both records for every terminal in *roster*.

    :param stale_after: how old a finished run may be and still count.
    :param moment: what to measure staleness against. Defaults to the clock,
        which is patched in tests rather than passed.
    """
    when = moment or clock_now()
    entries = [
        _status(entry, history.latest(entry.terminal_id), stale_after, when)
        for entry in roster.in_working_order()
    ]
    return PlantStatus(plant=roster.plant, entries=tuple(entries))


def _status(
    entry: RosterEntry,
    run: RunSummary | None,
    stale_after: timedelta,
    moment: datetime,
) -> TerminalStatus:
    standing, note = _standing_for(entry, run, stale_after, moment)
    return TerminalStatus(entry=entry, standing=standing, last_run=run, note=note)


__all__ = [
    "DEFAULT_STALE_AFTER",
    "PlantStatus",
    "Standing",
    "TerminalStatus",
    "plant_status",
]
