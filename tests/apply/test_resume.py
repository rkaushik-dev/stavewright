from __future__ import annotations

from pathlib import Path

import pytest

from v4t_setup.apply.journal import Journal, RecordKind
from v4t_setup.apply.resume import (
    check_resume,
    read_resume_state,
    remaining,
)
from v4t_setup.errors import ApplyError
from v4t_setup.model.profile import TerminalProfile
from v4t_setup.plan.steps import Plan, Step, StepKind

PROFILE = TerminalProfile(terminal_id="LINE-PC-04", fpga_revision="PCDC-1.4")


def plan() -> Plan:
    return Plan(
        profile=PROFILE,
        steps=[
            Step(step_id="dir.appdir", kind=StepKind.CREATE_DIRECTORY, title="make APPDIR"),
            Step(step_id="file.v4t", kind=StepKind.COPY_FILE, title="copy V4T.exe"),
            Step(step_id="reg.version", kind=StepKind.WRITE_REGISTRY, title="write version"),
        ],
    )


def write_journal(
    path: Path,
    *,
    done: tuple[str, ...] = (),
    failed: str | None = None,
    rollback: str | None = None,
    finish: str | None = None,
    digest: str | None = None,
    terminal: str = "LINE-PC-04",
) -> Path:
    with Journal(path) as journal:
        journal.begin(digest if digest is not None else str(plan().digest()), terminal, "run-1")
        for step_id in done:
            journal.append(RecordKind.STEP_START, step_id=step_id)
            journal.append(RecordKind.STEP_OK, step_id=step_id, changed=True)
        if failed:
            journal.append(RecordKind.STEP_START, step_id=failed)
            journal.append(RecordKind.STEP_FAILED, step_id=failed, detail="it broke")
        if rollback == "started":
            journal.append(RecordKind.ROLLBACK_START, count=len(done))
        if rollback == "finished":
            journal.append(RecordKind.ROLLBACK_START, count=len(done))
            for step_id in reversed(done):
                journal.append(RecordKind.ROLLBACK_OK, step_id=step_id)
        if finish:
            journal.end(finish)
    return path


def test_a_journal_that_stopped_after_two_steps(tmp_path):
    path = write_journal(tmp_path / "run-1.jsonl", done=("dir.appdir", "file.v4t"))
    state = read_resume_state(path)
    assert state.completed == {"dir.appdir", "file.v4t"}
    assert not state.is_finished
    assert state.can_resume


def test_it_reads_the_run_and_the_terminal(tmp_path):
    path = write_journal(tmp_path / "run-1.jsonl", done=("dir.appdir",))
    state = read_resume_state(path)
    assert state.run_id == "run-1"
    assert state.terminal == "LINE-PC-04"
    assert state.plan_digest == str(plan().digest())


def test_a_finished_run_has_nothing_to_resume(tmp_path):
    path = write_journal(
        tmp_path / "run-1.jsonl",
        done=("dir.appdir", "file.v4t", "reg.version"),
        finish="complete",
    )
    state = read_resume_state(path)
    assert state.is_finished
    assert not state.can_resume
    assert "nothing to pick up" in state.why_not


def test_a_run_that_began_a_rollback_and_stopped(tmp_path):
    path = write_journal(
        tmp_path / "run-1.jsonl",
        done=("dir.appdir",),
        failed="file.v4t",
        rollback="started",
    )
    state = read_resume_state(path)
    assert state.rollback_started
    assert not state.is_finished
    assert not state.can_resume
    assert "began a rollback" in state.why_not


def test_a_run_that_rolled_back_and_finished_is_not_resumable(tmp_path):
    path = write_journal(
        tmp_path / "run-1.jsonl",
        done=("dir.appdir",),
        failed="file.v4t",
        rollback="finished",
        finish="failed",
    )
    state = read_resume_state(path)
    assert state.rolled_back
    # It ended. There is nothing to pick up, and the terminal is back where it
    # started rather than half done.
    assert not state.can_resume
    assert "where it started" in state.why_not


def test_a_failed_step_is_recorded(tmp_path):
    path = write_journal(tmp_path / "run-1.jsonl", done=("dir.appdir",), failed="file.v4t")
    assert read_resume_state(path).failed_step == "file.v4t"


def test_it_describes_itself(tmp_path):
    path = write_journal(tmp_path / "run-1.jsonl", done=("dir.appdir",))
    assert read_resume_state(path).describe() == "run-1.jsonl: 1 steps done, resumable"


def test_a_finished_run_describes_why_not(tmp_path):
    path = write_journal(tmp_path / "run-1.jsonl", done=("dir.appdir",), finish="complete")
    assert "nothing to pick up" in read_resume_state(path).describe()


def test_a_journal_that_is_not_there(tmp_path):
    with pytest.raises(ApplyError, match="cannot read the journal"):
        read_resume_state(tmp_path / "gone.jsonl")


def test_a_journal_that_has_been_edited(tmp_path):
    path = write_journal(tmp_path / "run-1.jsonl", done=("dir.appdir",))
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[1] = lines[1].replace("dir.appdir", "dir.tampered")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ApplyError, match="cannot read the journal"):
        read_resume_state(path)


def test_what_is_left_to_do(tmp_path):
    path = write_journal(tmp_path / "run-1.jsonl", done=("dir.appdir",))
    assert remaining(read_resume_state(path), plan()) == ["file.v4t", "reg.version"]


def test_nothing_left_when_every_step_is_recorded(tmp_path):
    path = write_journal(
        tmp_path / "run-1.jsonl", done=("dir.appdir", "file.v4t", "reg.version")
    )
    assert remaining(read_resume_state(path), plan()) == []


def test_what_is_left_is_in_plan_order(tmp_path):
    path = write_journal(tmp_path / "run-1.jsonl")
    assert remaining(read_resume_state(path), plan()) == [
        step.step_id for step in plan()
    ]


def test_a_resumable_journal_passes_the_check(tmp_path):
    path = write_journal(tmp_path / "run-1.jsonl", done=("dir.appdir",))
    assert check_resume(read_resume_state(path), plan()) is None


def test_a_finished_journal_is_refused(tmp_path):
    path = write_journal(tmp_path / "run-1.jsonl", done=("dir.appdir",), finish="complete")
    with pytest.raises(ApplyError, match="cannot be resumed") as caught:
        check_resume(read_resume_state(path), plan())
    assert "nothing to pick up" in caught.value.context["why"]


def test_a_journal_for_another_terminal_is_refused(tmp_path):
    path = write_journal(
        tmp_path / "run-1.jsonl", done=("dir.appdir",), terminal="LINE-PC-09"
    )
    with pytest.raises(ApplyError, match="another terminal") as caught:
        check_resume(read_resume_state(path), plan())
    assert caught.value.context["journal"] == "LINE-PC-09"
    assert caught.value.context["plan"] == "LINE-PC-04"


def test_a_journal_for_another_plan_is_refused(tmp_path):
    # The one that matters. A bundle rebuilt between the two runs means the
    # step ids in the journal are not the steps in this plan.
    path = write_journal(tmp_path / "run-1.jsonl", done=("dir.appdir",), digest="sha256:other")
    with pytest.raises(ApplyError, match="different plan") as caught:
        check_resume(read_resume_state(path), plan())
    assert caught.value.context["journal"] == "sha256:other"


def test_a_half_rolled_back_journal_is_refused(tmp_path):
    path = write_journal(
        tmp_path / "run-1.jsonl",
        done=("dir.appdir",),
        failed="file.v4t",
        rollback="started",
    )
    with pytest.raises(ApplyError, match="cannot be resumed"):
        check_resume(read_resume_state(path), plan())
