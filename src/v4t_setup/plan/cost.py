"""How long a plan takes, so a schedule can be built from the real work.

The scheduler had one number for every machine: twenty five minutes, measured
across sixty bench runs. That is fine for a plant where every terminal gets the
same bundle, and wrong the moment one line takes the four language packs and
another takes none, or one machine has the controller board and another does
not. The Mini skips the bitstream, which is four minutes on its own.

So a plan can price itself. The numbers below are per step kind, measured on
the bench against a 2013 line PC with a mechanical disk, which is the slowest
thing still in the field and therefore the one worth planning against.

Two things this deliberately does not model. Network, because staging happens
before anybody walks onto the floor. And the operator, because the time spent
looking for the right USB cable is real and is not a property of the plan.

Estimates come back as a range rather than a number. A single figure gets read
as a promise, and the bench asks for a window.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from v4t_setup.plan.steps import Plan, Step, StepKind

SECONDS: dict[StepKind, float] = {
    StepKind.CREATE_DIRECTORY: 0.2,
    StepKind.COPY_FILE: 1.5,
    StepKind.WRITE_REGISTRY: 0.3,
    StepKind.REGISTER_COM: 6.0,
    StepKind.INSTALL_SXS_ASSEMBLY: 12.0,
    StepKind.INSTALL_SXS_POLICY: 4.0,
    StepKind.STAGE_DRIVER: 25.0,
    StepKind.PROGRAM_FPGA: 240.0,
    StepKind.INSTALL_FONT: 3.0,
    StepKind.CREATE_SHORTCUT: 0.5,
    StepKind.SET_PATH: 1.0,
    StepKind.INSTALL_TEMPLATE_SET: 2.0,
    StepKind.INSTALL_LANGUAGE_PACK: 1.5,
    StepKind.RUN_REDISTRIBUTABLE: 90.0,
    StepKind.REMOVE_OLD_PRODUCT: 120.0,
}
"""Seconds per step, on the slowest machine still in the field."""

DEFAULT_SECONDS = 2.0
"""For a kind nobody has measured. Deliberately not zero: a new step kind that
costs nothing is a schedule that runs late and a number nobody questioned."""

BYTES_PER_SECOND = 12_000_000
"""Sustained write to a mechanical disk, which is what a copy step is bounded
by once the file is bigger than a few megabytes."""

SPREAD = 0.35
"""How much either side of the estimate the range runs. Measured as roughly
the spread between the fastest and slowest of sixty bench runs."""

VERIFY_SHARE = 0.25
"""Verifying a terminal reads everything the plan wrote and hashes it, which
comes out at about a quarter of the time it took to write."""


@dataclass(frozen=True, slots=True)
class Estimate:
    """A range, and where it came from."""

    expected: timedelta
    low: timedelta
    high: timedelta
    by_kind: dict[str, timedelta]

    @property
    def minutes(self) -> float:
        return self.expected.total_seconds() / 60

    def dominant(self) -> str | None:
        """The step kind that costs the most, which is the one worth arguing about."""
        if not self.by_kind:
            return None
        return max(self.by_kind, key=lambda kind: self.by_kind[kind])

    def describe(self) -> str:
        low = int(self.low.total_seconds() // 60)
        high = int(-(-self.high.total_seconds() // 60))
        leader = self.dominant()
        suffix = f", mostly {leader}" if leader else ""
        return f"{low} to {high} minutes{suffix}"


def step_seconds(step: Step) -> float:
    """What one step costs.

    A copy is the only kind whose size matters, and only once the file is big
    enough for the disk rather than the call to dominate.
    """
    base = SECONDS.get(step.kind, DEFAULT_SECONDS)
    if step.kind in (StepKind.COPY_FILE, StepKind.INSTALL_LANGUAGE_PACK):
        size = step.detail.get("size")
        if isinstance(size, int) and size > 0:
            return base + size / BYTES_PER_SECOND
    return base


def estimate_plan(plan: Plan, *, with_verify: bool = True) -> Estimate:
    """Price a whole plan.

    :param with_verify: whether to include the verify pass that follows an
        apply. The bench always runs one, so it is on by default, and a
        schedule built without it is short by a quarter.
    """
    by_kind: dict[str, float] = {}
    for step in plan:
        by_kind[step.kind.value] = by_kind.get(step.kind.value, 0.0) + step_seconds(step)
    total = sum(by_kind.values())
    if with_verify:
        total *= 1 + VERIFY_SHARE
    return Estimate(
        expected=timedelta(seconds=total),
        low=timedelta(seconds=total * (1 - SPREAD)),
        high=timedelta(seconds=total * (1 + SPREAD)),
        by_kind={kind: timedelta(seconds=seconds) for kind, seconds in by_kind.items()},
    )


def compare_estimates(before: Estimate, after: Estimate) -> str:
    """One line saying which way a plan change moved the clock."""
    difference = after.expected - before.expected
    minutes = difference.total_seconds() / 60
    if abs(minutes) < 1:
        return "no measurable change to how long a run takes"
    direction = "longer" if minutes > 0 else "shorter"
    return f"about {abs(minutes):.0f} minutes {direction} than before"


__all__ = [
    "BYTES_PER_SECOND",
    "DEFAULT_SECONDS",
    "SECONDS",
    "SPREAD",
    "VERIFY_SHARE",
    "Estimate",
    "compare_estimates",
    "estimate_plan",
    "step_seconds",
]
