"""What changed between two plans.

The question this answers is the one asked before every re-commission: the
bundle has moved on, what is actually going to happen to a terminal that is
already running the old one.

Comparing the two plans is a better answer than comparing the two manifests,
because a manifest change need not reach a given terminal. A new Turkish
language pack changes the manifest and changes nothing at all for a machine
that only speaks Polish, and a diff that reports it as work makes an engineer
plan for an outage that is not needed.

The interesting part is not the added and removed steps, which are obvious. It
is the ones that stayed and moved, because a step that has been reordered
relative to an irreversible one is a step whose rollback story has changed.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum

from stavewright.plan.steps import Plan, Step


class Change(StrEnum):
    """What happened to one step between two plans."""

    ADDED = "added"
    REMOVED = "removed"
    CHANGED = "changed"
    MOVED = "moved"
    SAME = "same"


@dataclass(frozen=True, slots=True)
class StepChange:
    """One step's fate."""

    step_id: str
    change: Change
    before: int | None = None
    after: int | None = None
    fields: tuple[str, ...] = ()
    """Which fields differ, for a ``CHANGED`` step."""

    @property
    def is_work(self) -> bool:
        """True when this change means something happens to the terminal.

        A moved step is not work. It runs either way and its effect is the
        same; only its position changed.
        """
        return self.change in (Change.ADDED, Change.CHANGED)

    def describe(self) -> str:
        match self.change:
            case Change.ADDED:
                return f"+ {self.step_id}"
            case Change.REMOVED:
                return f"- {self.step_id}"
            case Change.CHANGED:
                return f"~ {self.step_id} ({', '.join(self.fields)})"
            case Change.MOVED:
                return f"> {self.step_id} ({self.before} -> {self.after})"
            case _:
                return f"  {self.step_id}"


COMPARED_FIELDS = ("kind", "target", "source", "digest", "component", "detail")
"""What makes two steps with the same id different.

``weight`` is left out on purpose. It is a tie break for the sort and changing
it moves a step without changing what the step does, which is what ``MOVED`` is
for.

``title`` is left out too. It is prose for a report and a reworded title is not
a reason to tell somebody their terminal needs work.
"""


def _differences(before: Step, after: Step) -> tuple[str, ...]:
    changed: list[str] = []
    for name in COMPARED_FIELDS:
        if getattr(before, name) != getattr(after, name):
            changed.append(name)
    return tuple(changed)


@dataclass
class PlanDiff:
    """Every step's fate between two plans."""

    changes: list[StepChange] = field(default_factory=list)
    before_digest: str = ""
    after_digest: str = ""

    def __len__(self) -> int:
        return len(self.changes)

    def __iter__(self) -> Iterator[StepChange]:
        return iter(self.changes)

    def of(self, change: Change) -> list[StepChange]:
        return [entry for entry in self.changes if entry.change is change]

    @property
    def is_identical(self) -> bool:
        """True when nothing moved and nothing changed."""
        return all(entry.change is Change.SAME for entry in self.changes)

    @property
    def has_work(self) -> bool:
        """True when applying the new plan would alter the terminal."""
        return any(entry.is_work for entry in self.changes) or bool(self.of(Change.REMOVED))

    def counts(self) -> dict[str, int]:
        """How many of each kind of change, in change order, zeroes left out."""
        counts: dict[str, int] = {}
        for entry in self.changes:
            counts[entry.change.value] = counts.get(entry.change.value, 0) + 1
        return {kind.value: counts[kind.value] for kind in Change if kind.value in counts}

    def lines(self, *, include_same: bool = False) -> list[str]:
        """Operator readable summary."""
        summary = ", ".join(f"{count} {name}" for name, count in self.counts().items())
        head = "no difference" if self.is_identical else summary
        result = [head]
        result.extend(
            entry.describe()
            for entry in self.changes
            if include_same or entry.change is not Change.SAME
        )
        return result


def diff_plans(before: Plan, after: Plan) -> PlanDiff:
    """Compare two plans step by step.

    Steps are matched by id. That is the right key because a step id comes from
    the payload item it was built from, so the same thing keeps its id across
    bundle versions even when its content changes.
    """
    before_at = {step.step_id: index for index, step in enumerate(before)}
    after_at = {step.step_id: index for index, step in enumerate(after)}
    before_by_id = {step.step_id: step for step in before}
    after_by_id = {step.step_id: step for step in after}

    changes: list[StepChange] = []
    for step in after:
        if step.step_id not in before_by_id:
            changes.append(
                StepChange(
                    step_id=step.step_id,
                    change=Change.ADDED,
                    after=after_at[step.step_id],
                )
            )
            continue
        fields = _differences(before_by_id[step.step_id], step)
        position_before = before_at[step.step_id]
        position_after = after_at[step.step_id]
        if fields:
            changes.append(
                StepChange(
                    step_id=step.step_id,
                    change=Change.CHANGED,
                    before=position_before,
                    after=position_after,
                    fields=fields,
                )
            )
        elif position_before != position_after:
            changes.append(
                StepChange(
                    step_id=step.step_id,
                    change=Change.MOVED,
                    before=position_before,
                    after=position_after,
                )
            )
        else:
            changes.append(
                StepChange(
                    step_id=step.step_id,
                    change=Change.SAME,
                    before=position_before,
                    after=position_after,
                )
            )

    for step in before:
        if step.step_id not in after_by_id:
            changes.append(
                StepChange(
                    step_id=step.step_id,
                    change=Change.REMOVED,
                    before=before_at[step.step_id],
                )
            )

    return PlanDiff(
        changes=changes,
        before_digest=str(before.digest()),
        after_digest=str(after.digest()),
    )


def crossed_irreversible(before: Plan, after: Plan, diff: PlanDiff) -> list[str]:
    """Steps that moved across an irreversible one.

    A step that used to run before the bitstream and now runs after it has a
    different rollback story, because a failure past the bitstream cannot be
    taken back. Nothing about the step changed, so the ordinary diff calls it
    moved and says nothing more, and this is the reason to look.
    """
    irreversible_before = {step.step_id for step in before.irreversible_steps}
    irreversible_after = {step.step_id for step in after.irreversible_steps}
    shared = irreversible_before & irreversible_after
    if not shared:
        return []

    crossed: list[str] = []
    for entry in diff.of(Change.MOVED):
        for marker in sorted(shared):
            was_before = before.position_of(entry.step_id) < before.position_of(marker)
            now_before = after.position_of(entry.step_id) < after.position_of(marker)
            if was_before != now_before:
                crossed.append(entry.step_id)
                break
    return crossed


__all__ = [
    "COMPARED_FIELDS",
    "Change",
    "PlanDiff",
    "StepChange",
    "crossed_irreversible",
    "diff_plans",
]
