"""Putting waves into the windows the plant is willing to give up.

The rollout says what order. This says when, and the answer is usually that
some of it does not fit, which is the useful part. A plan that quietly assumes
eleven hours of downtime nobody agreed to is worse than one that says four
waves are unplaced.

One rule decides most of it: a wave does not span two windows. Half the steps
in a commissioning run cannot be rolled back, so a machine left part done at
the end of a shift is a machine somebody has to finish by hand at six in the
morning. If the wave does not fit in the window, it waits for the next one.

Time is in UTC throughout, like every other timestamp this toolkit writes. A
plant that wants local time on the printout converts on the way out, because a
schedule that crosses a daylight saving boundary in local time has an hour in
it that either happens twice or not at all.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise

from v4t_setup.errors import PlanError
from v4t_setup.fleet.waves import Rollout, Wave

DEFAULT_PER_MACHINE = timedelta(minutes=25)
"""How long one machine takes, measured across 60 runs on the bench.

Staging is done before anybody walks onto the floor, so this is the apply, the
verify and the operator signing it off. It is not the median, it is roughly the
80th percentile, because a schedule built on the median runs late every time.
"""

DEFAULT_CHANGEOVER = timedelta(minutes=10)
"""Getting the laptop from one machine to the next, once per wave."""


@dataclass(frozen=True, slots=True)
class Window:
    """A period the plant has agreed the bench may work in."""

    start: datetime
    end: datetime
    label: str = ""

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise PlanError(
                "a maintenance window needs timezone aware times",
                context={"label": self.label},
            )
        if self.end <= self.start:
            raise PlanError(
                "a maintenance window ends before it starts",
                context={
                    "label": self.label,
                    "start": self.start.isoformat(),
                    "end": self.end.isoformat(),
                },
            )

    @property
    def duration(self) -> timedelta:
        return self.end - self.start

    def contains(self, moment: datetime) -> bool:
        """Whether *moment* is inside the window. The end is exclusive."""
        return self.start <= moment < self.end

    def overlaps(self, other: Window) -> bool:
        return self.start < other.end and other.start < self.end

    def describe(self) -> str:
        name = f"{self.label}: " if self.label else ""
        hours = self.duration.total_seconds() / 3600
        return (
            f"{name}{self.start.isoformat(sep=' ')} to "
            f"{self.end.isoformat(sep=' ')} ({hours:.1f}h)"
        )


@dataclass(frozen=True, slots=True)
class Booking:
    """One wave, placed."""

    wave: Wave
    window: Window
    start: datetime
    end: datetime

    @property
    def duration(self) -> timedelta:
        return self.end - self.start

    def describe(self) -> str:
        return (
            f"wave {self.wave.number} at {self.start.isoformat(sep=' ')} "
            f"for {int(self.duration.total_seconds() // 60)}m "
            f"({', '.join(self.wave.terminal_ids)})"
        )


@dataclass(frozen=True, slots=True)
class Schedule:
    """Where every wave ended up, and what did not fit."""

    plant: str
    bookings: tuple[Booking, ...] = ()
    unplaced: tuple[Wave, ...] = ()

    def __len__(self) -> int:
        return len(self.bookings)

    def __iter__(self) -> Iterator[Booking]:
        return iter(self.bookings)

    @property
    def is_complete(self) -> bool:
        return not self.unplaced

    @property
    def finishes_at(self) -> datetime | None:
        """When the last booked wave ends, or None if nothing was placed."""
        return max((booking.end for booking in self.bookings), default=None)

    @property
    def working_time(self) -> timedelta:
        return sum((booking.duration for booking in self.bookings), timedelta())

    def in_window(self, label: str) -> list[Booking]:
        return [booking for booking in self.bookings if booking.window.label == label]

    def describe(self) -> list[str]:
        lines = [booking.describe() for booking in self.bookings]
        lines.extend(
            f"wave {wave.number} does not fit any window "
            f"({', '.join(wave.terminal_ids)})"
            for wave in self.unplaced
        )
        return lines


def wave_duration(
    wave: Wave,
    *,
    engineers: int = 1,
    per_machine: timedelta = DEFAULT_PER_MACHINE,
    changeover: timedelta = DEFAULT_CHANGEOVER,
) -> timedelta:
    """How long a wave takes with *engineers* people on it.

    Machines in a wave are on different lines by construction, so they can be
    done at the same time by different people. With one engineer the wave is
    serial and the arithmetic is the obvious one.

    :raises PlanError: when *engineers* is not at least one.
    """
    if engineers < 1:
        raise PlanError(
            "a wave needs at least one engineer",
            context={"wave": wave.number, "engineers": engineers},
        )
    rounds = -(-len(wave) // engineers)
    return rounds * per_machine + changeover


def check_windows(windows: Sequence[Window]) -> list[Window]:
    """Sort windows and refuse a set that overlaps.

    :raises PlanError: when two windows overlap. Overlapping windows mean the
        same hour is offered twice, and a schedule built from them books two
        waves onto one bench.
    """
    ordered = sorted(windows, key=lambda window: (window.start, window.end))
    for earlier, later in pairwise(ordered):
        if earlier.overlaps(later):
            raise PlanError(
                "two maintenance windows overlap",
                context={"first": earlier.describe(), "second": later.describe()},
            )
    return ordered


def schedule_rollout(
    rollout: Rollout,
    windows: Sequence[Window],
    *,
    engineers: int = 1,
    per_machine: timedelta = DEFAULT_PER_MACHINE,
    changeover: timedelta = DEFAULT_CHANGEOVER,
) -> Schedule:
    """Place each wave in the first window it fits, in wave order.

    Waves keep their order. A later wave never jumps into a gap an earlier one
    could not use, because the order is what makes the spares prove the bundle
    before anything that matters is touched, and a scheduler that reorders for
    packing throws that away for twenty minutes.

    :raises PlanError: when the windows overlap or *engineers* is below one.
    """
    ordered = check_windows(windows)
    cursors = {index: window.start for index, window in enumerate(ordered)}

    bookings: list[Booking] = []
    unplaced: list[Wave] = []
    for wave in rollout:
        needed = wave_duration(
            wave, engineers=engineers, per_machine=per_machine, changeover=changeover
        )
        placed = False
        for index, window in enumerate(ordered):
            start = cursors[index]
            if start + needed > window.end:
                continue
            bookings.append(Booking(wave=wave, window=window, start=start, end=start + needed))
            cursors[index] = start + needed
            placed = True
            break
        if not placed:
            unplaced.append(wave)
    return Schedule(
        plant=rollout.plant,
        bookings=tuple(bookings),
        unplaced=tuple(unplaced),
    )


__all__ = [
    "DEFAULT_CHANGEOVER",
    "DEFAULT_PER_MACHINE",
    "Booking",
    "Schedule",
    "Window",
    "check_windows",
    "schedule_rollout",
    "wave_duration",
]
