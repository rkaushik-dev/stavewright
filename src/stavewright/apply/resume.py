"""Picking a run back up after it stopped.

A bench laptop loses power, a network share goes away, somebody closes the lid.
The journal is on disk and it says exactly how far the run got, so starting
again from nothing is a waste of twenty minutes and, worse, a second set of
records for one commissioning.

What resuming does not do is skip anything. Every action in this toolkit is
idempotent by contract, so a resumed run re-checks each step and does nothing
where the terminal already agrees. That is slower than trusting the journal by
a few seconds and it is the difference between a resume that is correct and one
that is correct only when nobody touched the machine in between.

What the journal is for here is the two things re-checking cannot tell you:
which run this continues, and whether the last one stopped in the middle of a
rollback. A terminal left half rolled back is not a terminal to resume onto.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from stavewright.apply.journal import RecordKind, read_journal, replay
from stavewright.errors import ApplyError
from stavewright.plan.steps import Plan


@dataclass(frozen=True, slots=True)
class ResumeState:
    """What an interrupted journal says about where the run got to."""

    journal_path: Path
    run_id: str = ""
    terminal: str = ""
    plan_digest: str = ""
    completed: frozenset[str] = field(default_factory=frozenset)
    failed_step: str | None = None
    rollback_started: bool = False
    is_finished: bool = False
    """Whether the run wrote its end record, however it ended."""

    outcome: str = ""

    @property
    def rolled_back(self) -> bool:
        """Whether a rollback ran to the end. The terminal is back where it was."""
        return self.rollback_started and self.is_finished

    @property
    def can_resume(self) -> bool:
        """Whether picking this up is safe.

        A run that wrote an end record is finished whichever way it went, and
        a rolled back terminal is back where it started rather than half done,
        so there is nothing to pick up in either case.

        A run that began a rollback and never ended left the terminal in a
        state no plan describes, and the only safe move there is a person.
        """
        if self.is_finished:
            return False
        return not self.rollback_started

    @property
    def why_not(self) -> str:
        if self.rolled_back:
            return "this run rolled back, the terminal is where it started"
        if self.is_finished:
            return "this run finished, there is nothing to pick up"
        if self.rollback_started:
            return "this run began a rollback and did not finish it"
        return ""

    def describe(self) -> str:
        state = "resumable" if self.can_resume else self.why_not
        return (
            f"{self.journal_path.name}: {len(self.completed)} steps done, "
            f"{state}"
        )


def read_resume_state(journal_path: Path) -> ResumeState:
    """Read a journal and work out where its run stopped.

    :raises ApplyError: when the journal cannot be read or its hash chain does
        not hold. A journal that has been edited is not evidence of anything,
        and resuming from one would write a second run that claims it did.
    """
    try:
        records = read_journal(journal_path)
    except Exception as exc:
        raise ApplyError(
            "cannot read the journal to resume from",
            context={"path": str(journal_path), "detail": str(exc)},
        ) from exc

    state = replay(records)
    kinds = {record.kind for record in records}
    return ResumeState(
        journal_path=journal_path,
        run_id=state.run_id,
        terminal=state.terminal,
        plan_digest=state.plan_digest,
        completed=frozenset(state.completed),
        failed_step=state.failed,
        # The executor writes ROLLBACK_START before it undoes anything, so this
        # is true whether the rollback finished or the laptop died halfway
        # through it. The end record is what separates the two.
        rollback_started=RecordKind.ROLLBACK_START in kinds,
        is_finished=state.finished,
        outcome=state.outcome,
    )


def check_resume(state: ResumeState, plan: Plan) -> None:
    """Refuse a resume that does not belong to this plan.

    :raises ApplyError: when the run cannot be picked up, was against another
        terminal, or was against a different plan. The last one matters most:
        a bundle rebuilt between the two runs means the completed steps in the
        journal are not the steps in this plan, and the ones that share an id
        may not share a target.
    """
    if not state.can_resume:
        raise ApplyError(
            "this journal cannot be resumed",
            context={"path": str(state.journal_path), "why": state.why_not},
        )
    if state.terminal and state.terminal != plan.profile.terminal_id:
        raise ApplyError(
            "this journal is for another terminal",
            context={
                "journal": state.terminal,
                "plan": plan.profile.terminal_id,
            },
        )
    if state.plan_digest and state.plan_digest != str(plan.digest()):
        raise ApplyError(
            "this journal is for a different plan",
            context={"journal": state.plan_digest, "plan": str(plan.digest())},
        )


def remaining(state: ResumeState, plan: Plan) -> list[str]:
    """The steps the journal has no record of finishing, in plan order.

    Not what a resumed run skips. Every step is re-checked. This is what to
    tell the operator so the number on the screen matches the work left.
    """
    return [step.step_id for step in plan if step.step_id not in state.completed]


__all__ = [
    "ResumeState",
    "check_resume",
    "read_resume_state",
    "remaining",
]
