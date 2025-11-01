"""Turning a roster into the order the bench actually works in.

The bench does not commission a plant in one go. It gets a window, takes some
machines down, puts them back, and comes again. A wave is one of those windows.

Three constraints decide what may share a wave, and all three came from getting
it wrong:

One machine per line per wave. Coders on a line feed each other, so taking two
down together empties the line twice over and the second one has nothing to
prove itself against when it comes back up.

A wave does not mix criticalities. The limit on how many machines may be down
at once is a property of what stopping them costs, and a wave holding one
critical and two spares has no single limit that means anything.

The wave size is the criticality's own limit. One critical machine at a time,
three normal, eight spares, unless the caller asks for fewer.

Least disruptive first, so the first wave is the one that proves the bundle
against real hardware without anybody noticing if it goes wrong.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from v4t_setup.errors import PlanError
from v4t_setup.fleet.roster import Criticality, Roster, RosterEntry


@dataclass(frozen=True, slots=True)
class Wave:
    """One window's worth of work."""

    number: int
    entries: tuple[RosterEntry, ...]
    criticality: Criticality

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self) -> Iterator[RosterEntry]:
        return iter(self.entries)

    @property
    def terminal_ids(self) -> list[str]:
        return [entry.terminal_id for entry in self.entries]

    @property
    def lines(self) -> list[str]:
        return sorted({entry.line for entry in self.entries})

    @property
    def needs_stoppage(self) -> bool:
        """True when no machine in this wave has a planned window to use."""
        return any(not entry.shift_pattern.has_planned_downtime for entry in self.entries)

    def describe(self) -> str:
        machines = "machine" if len(self.entries) == 1 else "machines"
        where = ", ".join(self.lines)
        suffix = " (needs a stoppage)" if self.needs_stoppage else ""
        return (
            f"wave {self.number}: {len(self.entries)} {self.criticality.value} "
            f"{machines} on {where}{suffix}"
        )


@dataclass(frozen=True, slots=True)
class Rollout:
    """Every wave for one plant, in the order they run."""

    plant: str
    waves: tuple[Wave, ...] = ()

    def __len__(self) -> int:
        return len(self.waves)

    def __iter__(self) -> Iterator[Wave]:
        return iter(self.waves)

    @property
    def terminal_ids(self) -> list[str]:
        return [entry.terminal_id for wave in self.waves for entry in wave]

    @property
    def stoppages(self) -> int:
        """How many of the waves need the line stopped to run at all."""
        return sum(1 for wave in self.waves if wave.needs_stoppage)

    def wave_of(self, terminal_id: str) -> Wave:
        """The wave a terminal is in.

        :raises PlanError: when the rollout does not cover it.
        """
        for wave in self.waves:
            if terminal_id in wave.terminal_ids:
                return wave
        raise PlanError(
            "the rollout does not cover this terminal",
            context={"plant": self.plant, "terminal": terminal_id},
        )

    def describe(self) -> list[str]:
        return [wave.describe() for wave in self.waves]


def _fits(wave: list[RosterEntry], entry: RosterEntry, limit: int) -> bool:
    if len(wave) >= limit:
        return False
    return all(other.line != entry.line for other in wave)


def plan_rollout(
    roster: Roster,
    *,
    only: Sequence[str] | None = None,
    batch_limit: int | None = None,
) -> Rollout:
    """Group the roster into waves.

    :param only: terminal ids to include, when the window is not for the whole
        plant. Order is ignored, the rules decide it.
    :param batch_limit: an upper bound on any wave, on top of what the
        criticality already allows. Use it when the bench is short handed.
    :raises PlanError: when *only* names a terminal the roster does not have,
        or *batch_limit* is not positive.
    """
    if batch_limit is not None and batch_limit < 1:
        raise PlanError(
            "batch limit must be at least one machine",
            context={"plant": roster.plant, "batch_limit": batch_limit},
        )

    entries = roster.in_working_order()
    if only is not None:
        wanted = list(dict.fromkeys(only))
        missing = sorted(name for name in wanted if name not in roster)
        if missing:
            raise PlanError(
                "the roster has no such terminal",
                context={"plant": roster.plant, "missing": missing},
            )
        keep = set(wanted)
        entries = [entry for entry in entries if entry.terminal_id in keep]

    waves: list[Wave] = []
    by_impact = sorted(Criticality.__members__.values(), key=lambda member: member.rank)
    for criticality in by_impact:
        pending = [entry for entry in entries if entry.criticality is criticality]
        if not pending:
            continue
        limit = criticality.max_concurrent
        if batch_limit is not None:
            limit = min(limit, batch_limit)
        for group in _pack(pending, limit):
            waves.append(
                Wave(
                    number=len(waves) + 1,
                    entries=tuple(group),
                    criticality=criticality,
                )
            )
    return Rollout(plant=roster.plant, waves=tuple(waves))


def _pack(pending: list[RosterEntry], limit: int) -> Iterator[list[RosterEntry]]:
    """Greedily fill waves, keeping the roster's order inside each one.

    Greedy is right here rather than optimal. A wave the bench cannot look at
    and immediately see the shape of is a wave somebody reorders by hand on the
    night, and then the plan and the work have nothing to do with each other.
    """
    remaining = list(pending)
    while remaining:
        wave: list[RosterEntry] = []
        rest: list[RosterEntry] = []
        for entry in remaining:
            if _fits(wave, entry, limit):
                wave.append(entry)
            else:
                rest.append(entry)
        yield wave
        remaining = rest


def check_rollout(roster: Roster, rollout: Rollout, *, whole_plant: bool = True) -> list[str]:
    """Re-derive the constraints from the finished plan.

    The planner is the only thing that builds a rollout, so this is not
    defending against a caller. It is there because the packing loop is the
    part most likely to be changed by somebody in a hurry, and a plan that
    quietly drops a machine looks exactly like one that does not.

    :param whole_plant: whether every terminal in the roster is meant to be
        covered. Pass False for a plan built with ``only``, or the check
        reports the machines the caller deliberately left out.

    Returns one line per problem, empty when the plan holds.
    """
    problems: list[str] = []
    covered = rollout.terminal_ids
    duplicates = sorted({name for name in covered if covered.count(name) > 1})
    if duplicates:
        problems.append(f"terminals appear in more than one wave: {', '.join(duplicates)}")

    for wave in rollout:
        lines = [entry.line for entry in wave]
        if len(set(lines)) != len(lines):
            problems.append(f"wave {wave.number} takes two machines off one line")
        if len(wave) > wave.criticality.max_concurrent:
            problems.append(
                f"wave {wave.number} has {len(wave)} machines, "
                f"{wave.criticality.value} allows {wave.criticality.max_concurrent}"
            )
        if any(entry.criticality is not wave.criticality for entry in wave):
            problems.append(f"wave {wave.number} mixes criticalities")

    ranks = [wave.criticality.rank for wave in rollout]
    if ranks != sorted(ranks):
        problems.append("waves do not run least disruptive first")

    if whole_plant:
        problems.extend(
            f"{terminal_id} is in the roster and in no wave"
            for terminal_id in roster.terminal_ids
            if terminal_id not in covered
        )
    return problems


__all__ = ["Rollout", "Wave", "check_rollout", "plan_rollout"]
