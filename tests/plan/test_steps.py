from __future__ import annotations

from datetime import UTC, datetime

import pytest

from v4t_setup.errors import PlanError
from v4t_setup.plan.steps import Plan, Step, StepKind, utc_now
from v4t_setup.util.digest import digest_bytes


def step(step_id: str, kind: StepKind = StepKind.COPY_FILE, **kwargs) -> Step:
    kwargs.setdefault("title", f"do {step_id}")
    return Step(step_id=step_id, kind=kind, **kwargs)


def test_a_step_needs_an_id():
    with pytest.raises(PlanError, match="step id must not be empty"):
        Step(step_id="", kind=StepKind.COPY_FILE, title="x")


def test_a_step_needs_a_title():
    with pytest.raises(PlanError, match="must have a title"):
        Step(step_id="one", kind=StepKind.COPY_FILE, title="")


@pytest.mark.parametrize(
    "kind",
    [
        StepKind.COPY_FILE,
        StepKind.WRITE_REGISTRY,
        StepKind.REGISTER_COM,
        StepKind.CREATE_DIRECTORY,
        StepKind.INSTALL_SXS_ASSEMBLY,
        StepKind.CREATE_SHORTCUT,
    ],
)
def test_ordinary_steps_are_reversible(kind):
    assert step("s", kind).reversible


@pytest.mark.parametrize(
    "kind",
    [StepKind.PROGRAM_FPGA, StepKind.RUN_REDISTRIBUTABLE, StepKind.REMOVE_OLD_PRODUCT],
)
def test_the_three_irreversible_kinds(kind):
    assert not step("s", kind).reversible


def test_as_dict_leaves_out_what_was_not_set():
    payload = step("one").as_dict()
    assert payload == {"step_id": "one", "kind": "copy-file", "title": "do one"}


def test_as_dict_includes_what_was_set():
    payload = step(
        "one",
        component="V4T.exe",
        target=r"C:\Sellenis\S-jet600\V4T.exe",
        source="payload/V4T.exe",
        digest=digest_bytes(b"MZ"),
        requires=("sxs.crt",),
        provides=("exe",),
        weight=-5,
        detail={"vital": True},
    ).as_dict()
    assert payload["component"] == "V4T.exe"
    assert payload["digest"].startswith("sha256:")
    assert payload["requires"] == ["sxs.crt"]
    assert payload["provides"] == ["exe"]
    assert payload["weight"] == -5
    assert payload["detail"] == {"vital": True}


def test_a_zero_weight_is_left_out():
    assert "weight" not in step("one", weight=0).as_dict()


def test_describe_names_the_target():
    described = step("one", target=r"C:\Sellenis\S-jet600\V4T.exe").describe()
    assert described == r"copy-file: do one -> C:\Sellenis\S-jet600\V4T.exe"


def test_describe_without_a_target():
    assert step("one").describe() == "copy-file: do one"


def _plan(profile) -> Plan:
    return Plan(
        profile=profile,
        steps=[
            step("dir.appdir", StepKind.CREATE_DIRECTORY),
            step("file.v4t", StepKind.COPY_FILE),
            step("ocx.c1sizer", StepKind.REGISTER_COM),
            step("fpga", StepKind.PROGRAM_FPGA),
            step("reg.productinfo", StepKind.WRITE_REGISTRY),
        ],
    )


def test_plan_sequence_protocol(profile):
    plan = _plan(profile)
    assert len(plan) == 5
    assert plan[0].step_id == "dir.appdir"
    assert [s.step_id for s in plan] == list(plan.step_ids)
    assert "fpga" in plan
    assert "nope" not in plan


def test_get_and_require(profile):
    plan = _plan(profile)
    assert plan.get("fpga").kind is StepKind.PROGRAM_FPGA
    assert plan.get("nope") is None
    assert plan.require("fpga").step_id == "fpga"


def test_require_reports_a_missing_step(profile):
    with pytest.raises(PlanError, match="no such step"):
        _plan(profile).require("nope")


def test_position_of(profile):
    plan = _plan(profile)
    assert plan.position_of("dir.appdir") == 0
    assert plan.position_of("reg.productinfo") == 4


def test_position_of_reports_a_missing_step(profile):
    with pytest.raises(PlanError, match="no such step"):
        _plan(profile).position_of("nope")


def test_of_kind(profile):
    plan = _plan(profile)
    assert [s.step_id for s in plan.of_kind(StepKind.COPY_FILE)] == ["file.v4t"]
    assert plan.of_kind(StepKind.INSTALL_FONT) == []


def test_irreversible_steps(profile):
    assert [s.step_id for s in _plan(profile).irreversible_steps] == ["fpga"]


def test_counts(profile):
    assert _plan(profile).counts() == {
        "create-directory": 1,
        "copy-file": 1,
        "register-com": 1,
        "program-fpga": 1,
        "write-registry": 1,
    }


def test_counts_of_an_empty_plan(profile):
    assert Plan(profile=profile).counts() == {}


def test_the_digest_is_stable_for_the_same_content(profile):
    assert _plan(profile).digest() == _plan(profile).digest()


def test_the_digest_moves_when_a_step_changes(profile):
    plan = _plan(profile)
    other = _plan(profile)
    other.steps[1] = step("file.v4t", StepKind.COPY_FILE, target="elsewhere")
    assert plan.digest() != other.digest()


def test_the_digest_moves_when_the_order_changes(profile):
    plan = _plan(profile)
    other = _plan(profile)
    other.steps.reverse()
    assert plan.digest() != other.digest()


def test_the_digest_ignores_the_terminal_id(profile):
    other_terminal = profile.model_copy(update={"terminal_id": "LINE-PC-99"})
    assert _plan(profile).digest() == _plan(other_terminal).digest()


def test_the_digest_follows_the_architecture(profile, profile_x86):
    assert _plan(profile).digest() != _plan(profile_x86).digest()


def test_the_digest_follows_the_language_set(profile):
    other = profile.with_languages(["pl-PL"])
    assert _plan(profile).digest() != _plan(other).digest()


def test_the_digest_follows_the_head_count(profile):
    other = profile.model_copy(update={"head_count": 2})
    assert _plan(profile).digest() != _plan(other).digest()


def test_canonical_is_compact_json(profile):
    canonical = _plan(profile).canonical()
    assert canonical.startswith("{")
    assert ", " not in canonical
    assert '"steps"' in canonical


def test_describe_a_stamped_plan(profile):
    plan = _plan(profile)
    plan.created_at = datetime(2025, 4, 28, 9, 0, tzinfo=UTC)
    described = plan.describe()
    assert described.startswith("LINE-PC-04 (s-jet600, x64, 1 head): 5 steps, plan ")
    assert described.endswith("2025-04-28T09:00:00+00:00")


def test_describe_an_unstamped_plan(profile):
    assert _plan(profile).describe().endswith("unstamped")


def test_an_empty_plan(profile):
    plan = Plan(profile=profile)
    assert len(plan) == 0
    assert plan.step_ids == ()
    assert plan.irreversible_steps == []


def test_utc_now_is_timezone_aware():
    assert utc_now().tzinfo is not None
