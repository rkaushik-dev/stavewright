"""Where each terminal sits, and what it costs to stop it.

A profile says what to install. It says nothing about the machine's place in
the plant, and that is what decides the order the bench works in. Two things
matter and neither is in a profile:

Position along the line. Coders sit in sequence, and a coder upstream of a
labeller feeds it. Taking the upstream one down empties the line before the
downstream one has anything to do, so upstream goes first if you want the line
back in one shift rather than two.

What stopping it costs. A coder on the export line is not the same machine as
the one on the sample line, even when the two profiles are identical.

The roster carries both, in one file per plant::

    plant = "Wrexham"

    [line.export]
    criticality = "critical"
    shift_pattern = "continuous"

    [line.export.terminal.LINE-PC-04]
    position = 1
    commissioned = 2024-11-03

    [line.export.terminal.LINE-PC-05]
    position = 2

A terminal named here must have a profile. The roster does not repeat what the
profile already says, because the two drift apart the moment it does.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Any

from v4t_setup.config.toml import load_toml
from v4t_setup.errors import ConfigError
from v4t_setup.model.profile import TerminalProfile

LINE_TABLE = "line"
TERMINAL_TABLE = "terminal"


class Criticality(StrEnum):
    """What an hour of downtime on this line costs.

    Three levels, because the bench has never needed a fourth and every extra
    level is one more argument at the planning meeting.
    """

    CRITICAL = "critical"
    """Stops customer shipments. Out of hours only, one machine at a time."""

    NORMAL = "normal"
    """Stops a line. Planned downtime, several machines at a time is fine."""

    SPARE = "spare"
    """Stops nothing. A bench machine or a line that is not running."""

    @property
    def rank(self) -> int:
        """Lower sorts earlier. Spares get commissioned first, always."""
        return {Criticality.SPARE: 0, Criticality.NORMAL: 1, Criticality.CRITICAL: 2}[self]

    @property
    def max_concurrent(self) -> int:
        """How many machines of this kind may be down at once."""
        return {Criticality.SPARE: 8, Criticality.NORMAL: 3, Criticality.CRITICAL: 1}[self]


class ShiftPattern(StrEnum):
    """When the line runs, which bounds when the bench can touch it."""

    CONTINUOUS = "continuous"
    TWO_SHIFT = "two-shift"
    DAY = "day"

    @property
    def has_planned_downtime(self) -> bool:
        """True when there is a window that does not need a stoppage."""
        return self is not ShiftPattern.CONTINUOUS


@dataclass(frozen=True, slots=True)
class RosterEntry:
    """One terminal, its profile, and its place in the plant."""

    terminal_id: str
    profile: TerminalProfile
    line: str
    position: int
    criticality: Criticality
    shift_pattern: ShiftPattern
    commissioned: date | None = None

    @property
    def is_commissioned(self) -> bool:
        return self.commissioned is not None

    def sort_key(self) -> tuple[int, str, int]:
        """Least disruptive first, then line, then upstream to downstream."""
        return (self.criticality.rank, self.line, self.position)

    def describe(self) -> str:
        when = self.commissioned.isoformat() if self.commissioned else "never"
        return (
            f"{self.terminal_id} on {self.line} at {self.position} "
            f"({self.criticality.value}, last commissioned {when})"
        )


@dataclass(frozen=True, slots=True)
class Roster:
    """Every terminal in one plant."""

    plant: str
    entries: tuple[RosterEntry, ...] = ()

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self) -> Iterator[RosterEntry]:
        return iter(self.entries)

    def __contains__(self, terminal_id: str) -> bool:
        return any(entry.terminal_id == terminal_id for entry in self.entries)

    def get(self, terminal_id: str) -> RosterEntry:
        """One entry by terminal id.

        :raises ConfigError: when the plant has no such terminal.
        """
        for entry in self.entries:
            if entry.terminal_id == terminal_id:
                return entry
        raise ConfigError(
            "the roster has no such terminal",
            context={"plant": self.plant, "terminal": terminal_id, "known": self.terminal_ids},
        )

    @property
    def terminal_ids(self) -> list[str]:
        return sorted(entry.terminal_id for entry in self.entries)

    @property
    def lines(self) -> list[str]:
        return sorted({entry.line for entry in self.entries})

    def on_line(self, line: str) -> list[RosterEntry]:
        """Every terminal on one line, upstream first."""
        found = [entry for entry in self.entries if entry.line == line]
        return sorted(found, key=lambda entry: entry.position)

    def of_criticality(self, criticality: Criticality) -> list[RosterEntry]:
        return [entry for entry in self.entries if entry.criticality is criticality]

    def uncommissioned(self) -> list[RosterEntry]:
        """Terminals nobody has ever run a bundle onto, in working order."""
        return sorted(
            (entry for entry in self.entries if not entry.is_commissioned),
            key=RosterEntry.sort_key,
        )

    def in_working_order(self) -> list[RosterEntry]:
        """Everything, least disruptive first."""
        return sorted(self.entries, key=RosterEntry.sort_key)

    def upstream_of(self, terminal_id: str) -> list[RosterEntry]:
        """Terminals feeding the named one, nearest first."""
        entry = self.get(terminal_id)
        found = [
            other
            for other in self.on_line(entry.line)
            if other.position < entry.position
        ]
        return sorted(found, key=lambda other: other.position, reverse=True)

    def describe(self) -> list[str]:
        return [entry.describe() for entry in self.in_working_order()]


def _table(payload: Any, what: str, where: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ConfigError(f"{what} is not a table", context={"where": where})
    return payload


def parse_roster(
    payload: dict[str, Any],
    profiles: dict[str, TerminalProfile],
    *,
    where: str = "(in memory)",
) -> Roster:
    """Build a roster from a parsed document and the profiles it references.

    :raises ConfigError: when the plant is unnamed, a line or terminal entry is
        not a table, a terminal has no profile, a position is missing or not a
        positive integer, or two terminals on a line claim the same position.
    """
    plant = payload.get("plant")
    if not isinstance(plant, str) or not plant.strip():
        raise ConfigError("roster does not name its plant", context={"where": where})

    lines = _table(payload.get(LINE_TABLE, {}), "the line entry", where)
    entries: list[RosterEntry] = []
    for line_name, line_body in lines.items():
        body = _table(line_body, f"line {line_name}", where)
        criticality = _enum(Criticality, body.get("criticality", "normal"), line_name, where)
        pattern = _enum(ShiftPattern, body.get("shift_pattern", "day"), line_name, where)
        terminals = _table(body.get(TERMINAL_TABLE, {}), f"line {line_name} terminals", where)
        seen: dict[int, str] = {}
        for terminal_id, terminal_body in terminals.items():
            entry_body = _table(terminal_body, f"terminal {terminal_id}", where)
            profile = profiles.get(terminal_id)
            if profile is None:
                raise ConfigError(
                    "the roster names a terminal with no profile",
                    context={
                        "where": where,
                        "terminal": terminal_id,
                        "known": sorted(profiles),
                    },
                )
            position = _position(entry_body.get("position"), terminal_id, where)
            if position in seen:
                raise ConfigError(
                    "two terminals on one line claim the same position",
                    context={
                        "where": where,
                        "line": line_name,
                        "position": position,
                        "first": seen[position],
                        "second": terminal_id,
                    },
                )
            seen[position] = terminal_id
            entries.append(
                RosterEntry(
                    terminal_id=terminal_id,
                    profile=profile,
                    line=line_name,
                    position=position,
                    criticality=criticality,
                    shift_pattern=pattern,
                    commissioned=_commissioned(entry_body.get("commissioned"), terminal_id, where),
                )
            )
    return Roster(plant=plant.strip(), entries=tuple(entries))


def _enum(kind: type[Any], value: object, line_name: str, where: str) -> Any:
    try:
        return kind(value)
    except ValueError:
        raise ConfigError(
            f"{kind.__name__.lower()} is not one this toolkit knows",
            context={
                "where": where,
                "line": line_name,
                "value": str(value),
                "known": [member.value for member in kind],
            },
        ) from None


def _position(value: object, terminal_id: str, where: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ConfigError(
            "terminal position must be a whole number from one upwards",
            context={"where": where, "terminal": terminal_id, "value": repr(value)},
        )
    return value


def _commissioned(value: object, terminal_id: str, where: str) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    raise ConfigError(
        "commissioned must be a date",
        context={"where": where, "terminal": terminal_id, "value": repr(value)},
    )


def read_roster(path: Path, profiles: dict[str, TerminalProfile]) -> Roster:
    """Read a roster file.

    :raises ConfigError: when the file cannot be read, is not TOML, or does not
        describe a plant this toolkit can act on.
    """
    payload = load_toml(path, what="roster file")
    return parse_roster(payload, profiles, where=str(path))


__all__ = [
    "LINE_TABLE",
    "TERMINAL_TABLE",
    "Criticality",
    "Roster",
    "RosterEntry",
    "ShiftPattern",
    "parse_roster",
    "read_roster",
]
