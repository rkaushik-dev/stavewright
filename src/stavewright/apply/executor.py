"""Running a plan, and taking it back when a step fails.

The executor is small on purpose. It walks the plan, asks each action whether
it is already satisfied, applies it if not, and writes what happened. All the
knowledge about what a step means lives in the action.

Rollback undoes the completed steps in reverse. It stops at the first step that
cannot be undone and reports how far it got, rather than skipping past and
leaving the terminal in a state nobody has a name for. A run that programmed
the controller and then failed is a run that needs a person.

A resumed run is the same code path as a fresh one. It reads the journal to
find what has already completed and starts there, but every action is
idempotent so re-running the last step is safe and the executor does not have
to guess whether it finished.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from stavewright.apply.actions import Action, PayloadSource, action_for, wrap_target_error
from stavewright.apply.journal import Journal, JournalState, RecordKind, read_journal, replay
from stavewright.errors import ApplyError, RollbackError
from stavewright.logging import get_logger
from stavewright.plan.steps import Plan, Step
from stavewright.target.base import TargetError, TargetSystem

log = get_logger(__name__)


class Outcome(StrEnum):
    """How a run ended."""

    COMPLETE = "complete"
    FAILED = "failed"
    ROLLED_BACK = "rolled-back"
    ROLLBACK_FAILED = "rollback-failed"


@dataclass
class StepResult:
    """What happened to one step."""

    step_id: str
    changed: bool
    skipped: bool = False
    detail: str = ""


@dataclass
class ApplyResult:
    """What happened to a run."""

    outcome: Outcome
    run_id: str
    applied: list[StepResult] = field(default_factory=list)
    failed_step: str | None = None
    failure: str = ""
    rolled_back: list[str] = field(default_factory=list)
    rollback_stopped_at: str | None = None
    journal_path: Path | None = None

    @property
    def succeeded(self) -> bool:
        return self.outcome is Outcome.COMPLETE

    @property
    def changed_steps(self) -> list[str]:
        """Steps that actually altered the terminal."""
        return [result.step_id for result in self.applied if result.changed]

    @property
    def skipped_steps(self) -> list[str]:
        """Steps that were already satisfied."""
        return [result.step_id for result in self.applied if result.skipped]

    def lines(self) -> list[str]:
        """Operator readable summary."""
        result = [
            f"{self.outcome.value}: {len(self.changed_steps)} changed, "
            f"{len(self.skipped_steps)} already in place"
        ]
        if self.failed_step:
            result.append(f"failed at {self.failed_step}: {self.failure}")
        if self.rolled_back:
            result.append(f"rolled back {', '.join(self.rolled_back)}")
        if self.rollback_stopped_at:
            result.append(f"rollback stopped at {self.rollback_stopped_at}")
        return result


class Executor:
    """Applies a plan to a target."""

    def __init__(
        self,
        target: TargetSystem,
        payload: PayloadSource,
        journal_path: Path,
        *,
        rollback_on_failure: bool = True,
    ) -> None:
        self.target = target
        self.payload = payload
        self.journal_path = journal_path
        self.rollback_on_failure = rollback_on_failure

    def _action(self, step: Step) -> Action:
        return action_for(step.kind)

    def _apply_step(self, step: Step) -> StepResult:
        action = self._action(step)
        try:
            if action.is_satisfied(self.target, step, self.payload):
                return StepResult(step_id=step.step_id, changed=False, skipped=True)
            changed = action.apply(self.target, step, self.payload)
        except TargetError as exc:
            raise wrap_target_error(step, exc) from exc
        return StepResult(step_id=step.step_id, changed=changed)

    def run(
        self,
        plan: Plan,
        run_id: str,
        *,
        start_after: set[str] | None = None,
    ) -> ApplyResult:
        """Apply *plan*.

        :param start_after: step ids already known to be done, from a journal.
            They are still re-checked, because a step that was recorded as
            complete may have been undone by hand since.
        """
        done = start_after or set()
        result = ApplyResult(
            outcome=Outcome.COMPLETE, run_id=run_id, journal_path=self.journal_path
        )
        completed: list[Step] = []

        with Journal(self.journal_path) as journal:
            journal.begin(str(plan.digest()), plan.profile.terminal_id, run_id)
            for step in plan:
                journal.append(RecordKind.STEP_START, step_id=step.step_id)
                try:
                    outcome = self._apply_step(step)
                except ApplyError as exc:
                    journal.append(
                        RecordKind.STEP_FAILED, step_id=step.step_id, detail=exc.message
                    )
                    log.error("step failed", step=step.step_id, detail=exc.message)
                    result.outcome = Outcome.FAILED
                    result.failed_step = step.step_id
                    result.failure = str(exc)
                    if self.rollback_on_failure:
                        self._rollback(journal, completed, result)
                    journal.end(result.outcome.value)
                    return result

                result.applied.append(outcome)
                if outcome.skipped:
                    journal.append(
                        RecordKind.STEP_SKIPPED, step_id=step.step_id, reason="already satisfied"
                    )
                else:
                    journal.append(
                        RecordKind.STEP_OK, step_id=step.step_id, changed=outcome.changed
                    )
                    completed.append(step)
                if step.step_id in done:
                    log.debug("step was already recorded as done", step=step.step_id)
            journal.end(Outcome.COMPLETE.value)
        return result

    def _rollback(self, journal: Journal, completed: list[Step], result: ApplyResult) -> None:
        journal.append(RecordKind.ROLLBACK_START, count=len(completed))
        for step in reversed(completed):
            action = self._action(step)
            if not action.reversible:
                journal.append(
                    RecordKind.ROLLBACK_FAILED,
                    step_id=step.step_id,
                    detail="step cannot be undone",
                )
                result.rollback_stopped_at = step.step_id
                result.outcome = Outcome.ROLLBACK_FAILED
                return
            try:
                action.revert(self.target, step, self.payload)
            except (ApplyError, TargetError) as exc:
                journal.append(
                    RecordKind.ROLLBACK_FAILED, step_id=step.step_id, detail=str(exc)
                )
                result.rollback_stopped_at = step.step_id
                result.outcome = Outcome.ROLLBACK_FAILED
                return
            journal.append(RecordKind.ROLLBACK_OK, step_id=step.step_id)
            result.rolled_back.append(step.step_id)
        result.outcome = Outcome.ROLLED_BACK

    def resume(self, plan: Plan, run_id: str, previous: Path) -> ApplyResult:
        """Continue a run that was interrupted.

        :raises ApplyError: when the previous journal was for a different plan.
        """
        state = replay(read_journal(previous))
        if state.plan_digest and state.plan_digest != str(plan.digest()):
            raise ApplyError(
                "the journal is for a different plan",
                context={"journal": state.plan_digest, "plan": str(plan.digest())},
            )
        return self.run(plan, run_id, start_after=set(state.completed) | set(state.skipped))

    def rollback(self, plan: Plan, journal_path: Path) -> ApplyResult:
        """Undo a completed or partial run described by a journal.

        :raises RollbackError: when the journal names a step the plan does not
            contain, which means the two do not belong together.
        """
        state: JournalState = replay(read_journal(journal_path))
        result = ApplyResult(
            outcome=Outcome.ROLLED_BACK,
            run_id=state.run_id,
            journal_path=self.journal_path,
        )
        steps: list[Step] = []
        for step_id in state.needs_rollback:
            step = plan.get(step_id)
            if step is None:
                raise RollbackError(
                    "the journal names a step this plan does not have",
                    context={"step": step_id},
                )
            steps.append(step)

        with Journal(self.journal_path) as journal:
            journal.begin(str(plan.digest()), plan.profile.terminal_id, state.run_id)
            # _rollback walks in reverse, and needs_rollback is already newest
            # first, so hand it back the other way round.
            self._rollback(journal, list(reversed(steps)), result)
            journal.end(result.outcome.value)
        return result


def dry_run(
    plan: Plan,
    seed: TargetSystem,
    payload: PayloadSource,
    journal_path: Path,
) -> ApplyResult:
    """Apply *plan* to *seed*, which is expected to be a throwaway copy.

    There is no separate dry run code path. The caller hands in a target that
    does not matter, usually an in memory one primed with what the real machine
    holds, and the result is the same report the real run would produce.
    """
    executor = Executor(seed, payload, journal_path, rollback_on_failure=False)
    return executor.run(plan, run_id="dry-run")


__all__ = ["ApplyResult", "Executor", "Outcome", "StepResult", "dry_run"]
