from __future__ import annotations

import pytest

from stavewright.plan.diff import (
    COMPARED_FIELDS,
    Change,
    PlanDiff,
    StepChange,
    crossed_irreversible,
    diff_plans,
)
from stavewright.plan.steps import Plan, Step, StepKind
from stavewright.util.digest import digest_bytes

APPDIR = r"C:\Sellenis\S-jet600"


def step(step_id: str, kind: StepKind = StepKind.COPY_FILE, **kwargs) -> Step:
    kwargs.setdefault("title", f"do {step_id}")
    return Step(step_id=step_id, kind=kind, **kwargs)


def plan_of(profile, *steps: Step) -> Plan:
    return Plan(profile=profile, steps=list(steps))


def base(profile) -> Plan:
    return plan_of(
        profile,
        step("dir.appdir", StepKind.CREATE_DIRECTORY, target=APPDIR),
        step("file.v4t", target=rf"{APPDIR}\V4T.exe", source="payload/V4T.exe"),
        step("reg.version", StepKind.WRITE_REGISTRY, detail={"key": "Software\\X"}),
    )


def test_a_plan_against_itself_is_identical(profile):
    result = diff_plans(base(profile), base(profile))
    assert result.is_identical
    assert not result.has_work
    assert result.counts() == {"same": 3}


def test_the_digests_are_recorded(profile):
    plan = base(profile)
    result = diff_plans(plan, plan)
    assert result.before_digest == str(plan.digest())
    assert result.after_digest == str(plan.digest())


def test_an_added_step(profile):
    after = base(profile)
    after.steps.append(step("file.imager", target=rf"{APPDIR}\qu_imager.dll"))
    result = diff_plans(base(profile), after)
    assert [entry.step_id for entry in result.of(Change.ADDED)] == ["file.imager"]
    assert result.has_work
    assert not result.is_identical


def test_a_removed_step(profile):
    after = base(profile)
    after.steps.pop()
    result = diff_plans(base(profile), after)
    assert [entry.step_id for entry in result.of(Change.REMOVED)] == ["reg.version"]
    assert result.has_work


def test_a_removed_step_records_where_it_used_to_be(profile):
    after = base(profile)
    after.steps.pop(0)
    entry = diff_plans(base(profile), after).of(Change.REMOVED)[0]
    assert entry.before == 0
    assert entry.after is None


def test_a_changed_payload(profile):
    after = base(profile)
    after.steps[1] = step(
        "file.v4t",
        target=rf"{APPDIR}\V4T.exe",
        source="payload/V4T.exe",
        digest=digest_bytes(b"a new build"),
    )
    result = diff_plans(base(profile), after)
    entry = result.of(Change.CHANGED)[0]
    assert entry.step_id == "file.v4t"
    assert entry.fields == ("digest",)
    assert entry.is_work


def test_a_changed_target(profile):
    after = base(profile)
    after.steps[1] = step("file.v4t", target=r"D:\elsewhere\V4T.exe", source="payload/V4T.exe")
    assert diff_plans(base(profile), after).of(Change.CHANGED)[0].fields == ("target",)


def test_several_changed_fields_are_all_named(profile):
    after = base(profile)
    after.steps[1] = step(
        "file.v4t",
        kind=StepKind.INSTALL_LANGUAGE_PACK,
        target=r"D:\elsewhere\V4T.exe",
        source="payload/other.exe",
    )
    fields = diff_plans(base(profile), after).of(Change.CHANGED)[0].fields
    assert set(fields) == {"kind", "target", "source"}


def test_a_reworded_title_is_not_a_change(profile):
    after = base(profile)
    after.steps[1] = Step(
        step_id="file.v4t",
        kind=StepKind.COPY_FILE,
        title="a much better description",
        target=rf"{APPDIR}\V4T.exe",
        source="payload/V4T.exe",
    )
    result = diff_plans(base(profile), after)
    assert result.is_identical


def test_a_changed_weight_is_not_a_change(profile):
    after = base(profile)
    after.steps[1] = step(
        "file.v4t", target=rf"{APPDIR}\V4T.exe", source="payload/V4T.exe", weight=-99
    )
    assert diff_plans(base(profile), after).is_identical


def test_title_and_weight_are_deliberately_not_compared():
    assert "title" not in COMPARED_FIELDS
    assert "weight" not in COMPARED_FIELDS


def test_a_moved_step(profile):
    after = plan_of(
        profile,
        base(profile).steps[1],
        base(profile).steps[0],
        base(profile).steps[2],
    )
    result = diff_plans(base(profile), after)
    moved = {entry.step_id for entry in result.of(Change.MOVED)}
    assert moved == {"dir.appdir", "file.v4t"}


def test_a_moved_step_records_both_positions(profile):
    after = plan_of(
        profile,
        base(profile).steps[1],
        base(profile).steps[0],
        base(profile).steps[2],
    )
    entry = next(e for e in diff_plans(base(profile), after) if e.step_id == "dir.appdir")
    assert entry.before == 0
    assert entry.after == 1


def test_a_move_is_not_work(profile):
    after = plan_of(
        profile,
        base(profile).steps[1],
        base(profile).steps[0],
        base(profile).steps[2],
    )
    result = diff_plans(base(profile), after)
    assert not result.has_work
    assert not result.is_identical


def test_counts_are_in_change_order(profile):
    after = base(profile)
    after.steps.append(step("file.new", target=rf"{APPDIR}\new.dll"))
    after.steps.pop(0)
    counts = diff_plans(base(profile), after).counts()
    assert list(counts) == [name for name in ("added", "removed", "changed", "moved", "same")
                            if name in counts]


def test_lines_hide_unchanged_steps_by_default(profile):
    after = base(profile)
    after.steps.append(step("file.new", target=rf"{APPDIR}\new.dll"))
    lines = diff_plans(base(profile), after).lines()
    assert any(line.startswith("+ file.new") for line in lines)
    assert not any(line.startswith("  ") for line in lines)


def test_lines_can_show_everything(profile):
    after = base(profile)
    lines = diff_plans(base(profile), after).lines(include_same=True)
    assert len([line for line in lines if line.startswith("  ")]) == 3


def test_lines_of_an_identical_pair_say_so(profile):
    assert diff_plans(base(profile), base(profile)).lines()[0] == "no difference"


@pytest.mark.parametrize(
    ("change", "prefix"),
    [
        (Change.ADDED, "+ "),
        (Change.REMOVED, "- "),
        (Change.MOVED, "> "),
        (Change.SAME, "  "),
    ],
)
def test_describe_uses_a_prefix_per_change(change, prefix):
    entry = StepChange(step_id="x", change=change, before=0, after=1)
    assert entry.describe().startswith(prefix)


def test_describe_a_changed_step_names_the_fields():
    entry = StepChange(step_id="x", change=Change.CHANGED, fields=("digest", "target"))
    assert entry.describe() == "~ x (digest, target)"


def test_an_empty_diff():
    result = PlanDiff()
    assert result.is_identical
    assert not result.has_work
    assert result.counts() == {}
    assert result.lines() == ["no difference"]


def _with_bitstream(profile) -> Plan:
    return plan_of(
        profile,
        step("driver.ftdi", StepKind.STAGE_DRIVER),
        step("fpga", StepKind.PROGRAM_FPGA, detail={"revision": "PCDC-1.4"}),
        step("file.v4t", target=rf"{APPDIR}\V4T.exe"),
    )


def test_nothing_crossed_when_nothing_moved(profile):
    plan = _with_bitstream(profile)
    assert crossed_irreversible(plan, plan, diff_plans(plan, plan)) == []


def test_a_step_that_moved_past_the_bitstream_is_reported(profile):
    before = _with_bitstream(profile)
    after = plan_of(
        profile,
        before.steps[0],
        before.steps[2],
        before.steps[1],
    )
    result = diff_plans(before, after)
    assert crossed_irreversible(before, after, result) == ["file.v4t"]


def test_a_step_that_moved_but_stayed_on_the_same_side_is_not_reported(profile):
    before = _with_bitstream(profile)
    after = plan_of(
        profile,
        before.steps[1],
        before.steps[0],
        before.steps[2],
    )
    result = diff_plans(before, after)
    # driver.ftdi and fpga swapped, but file.v4t is still after both.
    assert "file.v4t" not in crossed_irreversible(before, after, result)


def test_no_irreversible_step_means_nothing_to_cross(profile):
    before = base(profile)
    after = plan_of(profile, before.steps[1], before.steps[0], before.steps[2])
    result = diff_plans(before, after)
    assert crossed_irreversible(before, after, result) == []


def test_a_bitstream_added_in_the_new_plan_is_not_a_crossing(profile):
    before = base(profile)
    after = base(profile)
    after.steps.insert(1, step("fpga", StepKind.PROGRAM_FPGA, detail={"revision": "PCDC-1.4"}))
    result = diff_plans(before, after)
    # The marker is only in one plan, so there is no before and after to compare.
    assert crossed_irreversible(before, after, result) == []


def test_a_realistic_bundle_bump(bundle_manifest, profile, directory_table):
    from stavewright.model.manifest import Manifest
    from stavewright.plan.planner import build_plan

    before = build_plan(bundle_manifest, profile, directory_table, stamp=False).plan
    bumped = Manifest(items=list(bundle_manifest.items))
    bumped.items = [item for item in bumped.items if item.item_id != "shortcut.desktop"]
    after = build_plan(bumped, profile, directory_table, stamp=False).plan

    result = diff_plans(before, after)
    assert [entry.step_id for entry in result.of(Change.REMOVED)] == ["shortcut.desktop"]
    assert result.has_work


def test_a_language_added_to_the_manifest_is_no_work_for_this_terminal(
    bundle_manifest, profile, directory_table
):
    # The manifest changed, the plan for a terminal that does not install that
    # language did not. This is why the diff is over plans and not manifests.
    from stavewright.plan.planner import build_plan

    before = build_plan(bundle_manifest, profile, directory_table, stamp=False).plan
    after = build_plan(bundle_manifest, profile, directory_table, stamp=False).plan
    assert diff_plans(before, after).is_identical
