from __future__ import annotations

import pytest

from v4t_setup.apply.actions import DirectoryPayload
from v4t_setup.apply.executor import ApplyResult, Executor, Outcome, dry_run
from v4t_setup.apply.journal import read_journal, replay
from v4t_setup.errors import ApplyError, RollbackError
from v4t_setup.model.registry import RegistryRoot, RegistryValueType
from v4t_setup.plan.steps import Plan, Step, StepKind
from v4t_setup.target.memory import MemoryTarget, normalise_path

APPDIR = r"C:\Sellenis\S-jet600"


@pytest.fixture
def payload(tmp_path):
    root = tmp_path / "stage"
    (root / "payload").mkdir(parents=True)
    (root / "payload" / "V4T.exe").write_bytes(b"MZ\x90\x00")
    (root / "payload" / "qu_imager.dll").write_bytes(b"MZimager")
    (root / "payload" / "vcredist.exe").write_bytes(b"MZvc")
    return DirectoryPayload(root)


@pytest.fixture
def target():
    return MemoryTarget()


@pytest.fixture
def journal_path(tmp_path):
    return tmp_path / "_journal" / "run.jsonl"


def simple_plan(profile) -> Plan:
    return Plan(
        profile=profile,
        steps=[
            Step(
                step_id="dir.appdir",
                kind=StepKind.CREATE_DIRECTORY,
                title="make APPDIR",
                target=APPDIR,
            ),
            Step(
                step_id="file.v4t",
                kind=StepKind.COPY_FILE,
                title="copy V4T.exe",
                target=rf"{APPDIR}\V4T.exe",
                source="payload/V4T.exe",
            ),
            Step(
                step_id="file.imager",
                kind=StepKind.COPY_FILE,
                title="copy qu_imager.dll",
                target=rf"{APPDIR}\qu_imager.dll",
                source="payload/qu_imager.dll",
            ),
            Step(
                step_id="reg.version",
                kind=StepKind.WRITE_REGISTRY,
                title="write Version",
                detail={
                    "root": int(RegistryRoot.LOCAL_MACHINE),
                    "key": r"Software\Matricode\V4T",
                    "name": "Version",
                    "value": "1.0.0",
                    "value_type": RegistryValueType.STRING.value,
                },
            ),
        ],
    )


def test_a_clean_run_completes(profile, target, payload, journal_path):
    result = Executor(target, payload, journal_path).run(simple_plan(profile), "run0001")
    assert result.succeeded
    assert result.outcome is Outcome.COMPLETE
    assert result.changed_steps == ["dir.appdir", "file.v4t", "file.imager", "reg.version"]
    assert result.skipped_steps == []


def test_a_clean_run_puts_everything_in_place(profile, target, payload, journal_path):
    Executor(target, payload, journal_path).run(simple_plan(profile), "run0001")
    assert target.read_file(rf"{APPDIR}\V4T.exe") == b"MZ\x90\x00"
    assert target.read_value(
        RegistryRoot.LOCAL_MACHINE, r"Software\Matricode\V4T", "Version"
    ).data == "1.0.0"


def test_the_journal_records_the_whole_run(profile, target, payload, journal_path):
    Executor(target, payload, journal_path).run(simple_plan(profile), "run0001")
    state = replay(read_journal(journal_path))
    assert state.run_id == "run0001"
    assert state.terminal == "LINE-PC-04"
    assert state.completed == ["dir.appdir", "file.v4t", "file.imager", "reg.version"]
    assert state.outcome == "complete"
    assert state.is_complete


def test_the_journal_records_the_plan_digest(profile, target, payload, journal_path):
    plan = simple_plan(profile)
    Executor(target, payload, journal_path).run(plan, "run0001")
    assert replay(read_journal(journal_path)).plan_digest == str(plan.digest())


def test_a_second_run_changes_nothing(profile, target, payload, tmp_path):
    plan = simple_plan(profile)
    Executor(target, payload, tmp_path / "one.jsonl").run(plan, "run0001")
    second = Executor(target, payload, tmp_path / "two.jsonl").run(plan, "run0002")
    assert second.succeeded
    assert second.changed_steps == []
    assert len(second.skipped_steps) == 4


def test_a_partially_commissioned_terminal_only_gets_what_it_needs(
    profile, target, payload, journal_path
):
    target.write_file(rf"{APPDIR}\V4T.exe", b"MZ\x90\x00")
    result = Executor(target, payload, journal_path).run(simple_plan(profile), "run0001")
    assert "file.v4t" in result.skipped_steps
    assert "file.imager" in result.changed_steps


def test_a_failing_step_stops_the_run(profile, target, payload, journal_path):
    plan = simple_plan(profile)
    plan.steps.insert(
        2,
        Step(
            step_id="bad.copy",
            kind=StepKind.COPY_FILE,
            title="copy something that is not staged",
            target=rf"{APPDIR}\missing.dll",
            source="payload/missing.dll",
        ),
    )
    result = Executor(target, payload, journal_path).run(plan, "run0001")
    assert not result.succeeded
    assert result.failed_step == "bad.copy"
    assert "cannot read payload" in result.failure


def test_a_failing_step_rolls_the_run_back(profile, target, payload, journal_path):
    plan = simple_plan(profile)
    plan.steps.insert(
        2,
        Step(
            step_id="bad.copy",
            kind=StepKind.COPY_FILE,
            title="copy something that is not staged",
            target=rf"{APPDIR}\missing.dll",
            source="payload/missing.dll",
        ),
    )
    result = Executor(target, payload, journal_path).run(plan, "run0001")
    assert result.outcome is Outcome.ROLLED_BACK
    assert result.rolled_back == ["file.v4t", "dir.appdir"]
    assert not target.path_exists(rf"{APPDIR}\V4T.exe")
    assert not target.is_directory(APPDIR)


def test_rollback_can_be_turned_off(profile, target, payload, journal_path):
    plan = simple_plan(profile)
    plan.steps.insert(
        2,
        Step(
            step_id="bad.copy",
            kind=StepKind.COPY_FILE,
            title="bad",
            target=rf"{APPDIR}\missing.dll",
            source="payload/missing.dll",
        ),
    )
    executor = Executor(target, payload, journal_path, rollback_on_failure=False)
    result = executor.run(plan, "run0001")
    assert result.outcome is Outcome.FAILED
    assert result.rolled_back == []
    assert target.path_exists(rf"{APPDIR}\V4T.exe")


def test_rollback_stops_at_a_step_that_cannot_be_undone(profile, target, payload, journal_path):
    plan = Plan(
        profile=profile,
        steps=[
            Step(
                step_id="dir.appdir",
                kind=StepKind.CREATE_DIRECTORY,
                title="make APPDIR",
                target=APPDIR,
            ),
            Step(
                step_id="fpga",
                kind=StepKind.PROGRAM_FPGA,
                title="program the controller",
                source="payload/V4T.exe",
                detail={"revision": "PCDC-1.4"},
            ),
            Step(
                step_id="bad.copy",
                kind=StepKind.COPY_FILE,
                title="bad",
                target=rf"{APPDIR}\missing.dll",
                source="payload/missing.dll",
            ),
        ],
    )
    result = Executor(target, payload, journal_path).run(plan, "run0001")
    assert result.outcome is Outcome.ROLLBACK_FAILED
    assert result.rollback_stopped_at == "fpga"
    # The directory before it is left alone, because getting past the bitstream
    # would mean claiming a state nobody has a name for.
    assert target.is_directory(APPDIR)


def test_the_journal_records_a_failure_and_its_rollback(profile, target, payload, journal_path):
    plan = simple_plan(profile)
    plan.steps.insert(
        2,
        Step(
            step_id="bad.copy",
            kind=StepKind.COPY_FILE,
            title="bad",
            target=rf"{APPDIR}\missing.dll",
            source="payload/missing.dll",
        ),
    )
    Executor(target, payload, journal_path).run(plan, "run0001")
    state = replay(read_journal(journal_path))
    assert state.failed == "bad.copy"
    assert state.rolled_back == ["file.v4t", "dir.appdir"]
    assert state.outcome == "rolled-back"


def test_a_target_refusal_is_reported_against_its_step(profile, target, payload, journal_path):
    plan = Plan(
        profile=profile,
        steps=[
            Step(
                step_id="fpga",
                kind=StepKind.PROGRAM_FPGA,
                title="program the controller",
                source="payload/V4T.exe",
                detail={"revision": "PCDC-1.4"},
            )
        ],
    )
    target.has_controller = False
    result = Executor(target, payload, journal_path).run(plan, "run0001")
    assert result.failed_step == "fpga"
    assert "no controller board" in result.failure


def test_resuming_an_interrupted_run(profile, target, payload, tmp_path):
    plan = simple_plan(profile)
    plan.steps.insert(
        2,
        Step(
            step_id="bad.copy",
            kind=StepKind.COPY_FILE,
            title="bad",
            target=rf"{APPDIR}\missing.dll",
            source="payload/missing.dll",
        ),
    )
    first = tmp_path / "one.jsonl"
    Executor(target, payload, first, rollback_on_failure=False).run(plan, "run0001")

    # Stage the file that was missing, then resume.
    (payload.root / "payload" / "missing.dll").write_bytes(b"MZlate")
    second = tmp_path / "two.jsonl"
    result = Executor(target, payload, second).resume(plan, "run0002", first)
    assert result.succeeded
    assert "file.v4t" in result.skipped_steps
    assert "bad.copy" in result.changed_steps


def test_resuming_with_a_journal_for_another_plan_is_refused(profile, target, payload, tmp_path):
    plan = simple_plan(profile)
    first = tmp_path / "one.jsonl"
    Executor(target, payload, first).run(plan, "run0001")

    other = simple_plan(profile)
    other.steps.pop()
    with pytest.raises(ApplyError, match="different plan"):
        Executor(target, payload, tmp_path / "two.jsonl").resume(other, "run0002", first)


def test_rolling_back_a_completed_run(profile, target, payload, tmp_path):
    plan = simple_plan(profile)
    first = tmp_path / "one.jsonl"
    Executor(target, payload, first).run(plan, "run0001")
    assert target.path_exists(rf"{APPDIR}\V4T.exe")

    result = Executor(target, payload, tmp_path / "undo.jsonl").rollback(plan, first)
    assert result.outcome is Outcome.ROLLED_BACK
    assert result.rolled_back == ["reg.version", "file.imager", "file.v4t", "dir.appdir"]
    assert not target.path_exists(rf"{APPDIR}\V4T.exe")
    assert target.read_value(
        RegistryRoot.LOCAL_MACHINE, r"Software\Matricode\V4T", "Version"
    ) is None


def test_rolling_back_a_journal_that_names_an_unknown_step(profile, target, payload, tmp_path):
    plan = simple_plan(profile)
    first = tmp_path / "one.jsonl"
    Executor(target, payload, first).run(plan, "run0001")
    plan.steps = [s for s in plan.steps if s.step_id != "file.imager"]
    with pytest.raises(RollbackError, match="does not have"):
        Executor(target, payload, tmp_path / "undo.jsonl").rollback(plan, first)


def test_rolling_back_twice_does_nothing_the_second_time(profile, target, payload, tmp_path):
    plan = simple_plan(profile)
    first = tmp_path / "one.jsonl"
    Executor(target, payload, first).run(plan, "run0001")
    undo = tmp_path / "undo.jsonl"
    Executor(target, payload, undo).rollback(plan, first)
    second = Executor(target, payload, tmp_path / "undo2.jsonl").rollback(plan, undo)
    assert second.rolled_back == []


def test_a_dry_run_leaves_the_real_target_alone(profile, target, payload, journal_path):
    real = MemoryTarget()
    scratch = MemoryTarget()
    result = dry_run(simple_plan(profile), scratch, payload, journal_path)
    assert result.succeeded
    assert scratch.path_exists(rf"{APPDIR}\V4T.exe")
    assert not real.path_exists(rf"{APPDIR}\V4T.exe")


def test_a_dry_run_reports_what_a_real_run_would_change(profile, payload, tmp_path):
    scratch = MemoryTarget()
    scratch.seed_file(rf"{APPDIR}\V4T.exe", b"MZ\x90\x00")
    result = dry_run(simple_plan(profile), scratch, payload, tmp_path / "dry.jsonl")
    assert "file.v4t" in result.skipped_steps
    assert "file.imager" in result.changed_steps


def test_a_dry_run_does_not_roll_back_on_failure(profile, payload, tmp_path):
    plan = simple_plan(profile)
    plan.steps.append(
        Step(
            step_id="bad.copy",
            kind=StepKind.COPY_FILE,
            title="bad",
            target=rf"{APPDIR}\missing.dll",
            source="payload/missing.dll",
        )
    )
    scratch = MemoryTarget()
    result = dry_run(plan, scratch, payload, tmp_path / "dry.jsonl")
    assert result.outcome is Outcome.FAILED
    assert scratch.path_exists(rf"{APPDIR}\V4T.exe")


def test_result_lines_read_as_a_summary(profile, target, payload, journal_path):
    result = Executor(target, payload, journal_path).run(simple_plan(profile), "run0001")
    lines = result.lines()
    assert lines[0] == "complete: 4 changed, 0 already in place"


def test_result_lines_name_the_failure(profile, target, payload, journal_path):
    plan = simple_plan(profile)
    plan.steps.append(
        Step(
            step_id="bad.copy",
            kind=StepKind.COPY_FILE,
            title="bad",
            target=rf"{APPDIR}\missing.dll",
            source="payload/missing.dll",
        )
    )
    lines = Executor(target, payload, journal_path).run(plan, "run0001").lines()
    assert any("failed at bad.copy" in line for line in lines)
    assert any("rolled back" in line for line in lines)


def test_result_lines_name_a_stalled_rollback(profile, target, payload, journal_path):
    plan = Plan(
        profile=profile,
        steps=[
            Step(
                step_id="fpga",
                kind=StepKind.PROGRAM_FPGA,
                title="program",
                source="payload/V4T.exe",
                detail={"revision": "PCDC-1.4"},
            ),
            Step(
                step_id="bad.copy",
                kind=StepKind.COPY_FILE,
                title="bad",
                target=rf"{APPDIR}\missing.dll",
                source="payload/missing.dll",
            ),
        ],
    )
    lines = Executor(target, payload, journal_path).run(plan, "run0001").lines()
    assert any("rollback stopped at fpga" in line for line in lines)


def test_an_empty_plan_runs_and_completes(profile, target, payload, journal_path):
    result = Executor(target, payload, journal_path).run(Plan(profile=profile), "run0001")
    assert result.succeeded
    assert result.applied == []


def test_the_result_carries_the_journal_path(profile, target, payload, journal_path):
    result = Executor(target, payload, journal_path).run(simple_plan(profile), "run0001")
    assert result.journal_path == journal_path


def test_an_empty_result_reports_no_changes():
    result = ApplyResult(outcome=Outcome.COMPLETE, run_id="x")
    assert result.changed_steps == []
    assert result.skipped_steps == []
    assert result.succeeded


def test_a_redistributable_exit_code_reaches_the_result(profile, target, payload, journal_path):
    plan = Plan(
        profile=profile,
        steps=[
            Step(
                step_id="vcredist",
                kind=StepKind.RUN_REDISTRIBUTABLE,
                title="run vcredist",
                target=r"C:\stage\vcredist.exe",
                source="payload/vcredist.exe",
            )
        ],
    )
    target.installer_exit_codes[normalise_path(r"C:\stage\vcredist.exe")] = 1603
    result = Executor(target, payload, journal_path).run(plan, "run0001")
    assert result.failed_step == "vcredist"
    assert "1603" in result.failure
