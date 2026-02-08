from __future__ import annotations

from datetime import timedelta

import pytest

from v4t_setup.model.profile import TerminalProfile
from v4t_setup.plan.cost import (
    BYTES_PER_SECOND,
    DEFAULT_SECONDS,
    SECONDS,
    SPREAD,
    VERIFY_SHARE,
    compare_estimates,
    estimate_plan,
    step_seconds,
)
from v4t_setup.plan.steps import Plan, Step, StepKind

PROFILE = TerminalProfile(terminal_id="LINE-PC-04", fpga_revision="PCDC-1.4")


def step(kind: StepKind, step_id: str = "s", **detail) -> Step:
    return Step(step_id=step_id, kind=kind, title=str(kind.value), detail=detail)


def plan(*steps: Step) -> Plan:
    return Plan(profile=PROFILE, steps=list(steps))


def test_every_step_kind_has_a_measured_cost():
    # A kind with no entry falls back to a default, which is a schedule that
    # runs late rather than one that fails.
    assert set(SECONDS) == set(StepKind)


def test_no_step_kind_is_free():
    assert all(seconds > 0 for seconds in SECONDS.values())


def test_the_bitstream_is_the_expensive_one():
    assert max(SECONDS, key=lambda kind: SECONDS[kind]) is StepKind.PROGRAM_FPGA


def test_a_step_costs_what_the_table_says():
    assert step_seconds(step(StepKind.REGISTER_COM)) == SECONDS[StepKind.REGISTER_COM]


def test_a_small_copy_costs_the_base():
    assert step_seconds(step(StepKind.COPY_FILE)) == SECONDS[StepKind.COPY_FILE]


def test_a_large_copy_costs_more():
    big = step(StepKind.COPY_FILE, size=120_000_000)
    assert step_seconds(big) == pytest.approx(SECONDS[StepKind.COPY_FILE] + 10)


def test_a_language_pack_is_sized_too():
    packed = step(StepKind.INSTALL_LANGUAGE_PACK, size=BYTES_PER_SECOND)
    assert step_seconds(packed) == pytest.approx(SECONDS[StepKind.INSTALL_LANGUAGE_PACK] + 1)


def test_a_size_on_a_kind_that_does_not_use_it_is_ignored():
    assert step_seconds(step(StepKind.WRITE_REGISTRY, size=999_999_999)) == (
        SECONDS[StepKind.WRITE_REGISTRY]
    )


@pytest.mark.parametrize("size", [0, -1, "big", None])
def test_a_size_that_is_not_a_positive_number_is_ignored(size):
    assert step_seconds(step(StepKind.COPY_FILE, size=size)) == SECONDS[StepKind.COPY_FILE]


def test_an_unknown_kind_falls_back_rather_than_costing_nothing(monkeypatch):
    monkeypatch.delitem(SECONDS, StepKind.CREATE_SHORTCUT)
    assert step_seconds(step(StepKind.CREATE_SHORTCUT)) == DEFAULT_SECONDS


def test_an_empty_plan_costs_nothing():
    estimate = estimate_plan(plan())
    assert estimate.expected == timedelta()
    assert estimate.by_kind == {}
    assert estimate.dominant() is None


def test_one_step_costs_its_own_time_plus_the_verify_pass():
    estimate = estimate_plan(plan(step(StepKind.REGISTER_COM)))
    expected = SECONDS[StepKind.REGISTER_COM] * (1 + VERIFY_SHARE)
    assert estimate.expected.total_seconds() == pytest.approx(expected)


def test_the_verify_pass_can_be_left_out():
    without = estimate_plan(plan(step(StepKind.REGISTER_COM)), with_verify=False)
    assert without.expected.total_seconds() == pytest.approx(SECONDS[StepKind.REGISTER_COM])


def test_the_range_brackets_the_estimate():
    estimate = estimate_plan(plan(step(StepKind.PROGRAM_FPGA)))
    assert estimate.low < estimate.expected < estimate.high


def test_the_spread_is_symmetrical():
    estimate = estimate_plan(plan(step(StepKind.PROGRAM_FPGA)))
    assert (estimate.expected - estimate.low) == pytest.approx(
        estimate.high - estimate.expected, abs=timedelta(seconds=0.001)
    )


def test_the_spread_is_the_documented_fraction():
    estimate = estimate_plan(plan(step(StepKind.PROGRAM_FPGA)))
    assert estimate.low.total_seconds() == pytest.approx(
        estimate.expected.total_seconds() * (1 - SPREAD)
    )


def test_the_breakdown_groups_by_kind():
    built = plan(
        step(StepKind.COPY_FILE, "a"),
        step(StepKind.COPY_FILE, "b"),
        step(StepKind.WRITE_REGISTRY, "c"),
    )
    estimate = estimate_plan(built)
    assert set(estimate.by_kind) == {"copy-file", "write-registry"}
    assert estimate.by_kind["copy-file"].total_seconds() == pytest.approx(
        2 * SECONDS[StepKind.COPY_FILE]
    )


def test_the_breakdown_is_before_the_verify_pass():
    # Otherwise the parts do not add up to the whole and somebody spends an
    # afternoon on it.
    built = plan(step(StepKind.COPY_FILE))
    estimate = estimate_plan(built)
    assert estimate.by_kind["copy-file"].total_seconds() == SECONDS[StepKind.COPY_FILE]
    assert estimate.expected > estimate.by_kind["copy-file"]


def test_the_dominant_kind_is_the_expensive_one():
    built = plan(
        step(StepKind.PROGRAM_FPGA, "fpga"),
        *(step(StepKind.CREATE_DIRECTORY, f"d{n}") for n in range(50)),
    )
    assert estimate_plan(built).dominant() == "program-fpga"


def test_minutes_is_the_expected_value():
    estimate = estimate_plan(plan(step(StepKind.PROGRAM_FPGA)))
    assert estimate.minutes == pytest.approx(estimate.expected.total_seconds() / 60)


def test_it_describes_itself_as_a_range():
    text = estimate_plan(plan(step(StepKind.PROGRAM_FPGA))).describe()
    assert " to " in text
    assert "minutes" in text
    assert "mostly program-fpga" in text


def test_an_empty_plan_describes_itself_without_a_leader():
    assert "mostly" not in estimate_plan(plan()).describe()


def test_a_mini_without_the_bitstream_is_much_cheaper():
    with_board = plan(step(StepKind.PROGRAM_FPGA, "f"), step(StepKind.COPY_FILE, "c"))
    without = plan(step(StepKind.COPY_FILE, "c"))
    assert estimate_plan(without).expected < estimate_plan(with_board).expected / 10


def test_comparing_two_estimates():
    small = estimate_plan(plan(step(StepKind.COPY_FILE)))
    large = estimate_plan(plan(step(StepKind.PROGRAM_FPGA)))
    assert "longer than before" in compare_estimates(small, large)
    assert "shorter than before" in compare_estimates(large, small)


def test_comparing_two_plans_that_cost_the_same():
    one = estimate_plan(plan(step(StepKind.COPY_FILE)))
    assert compare_estimates(one, one) == "no measurable change to how long a run takes"


def test_a_change_under_a_minute_is_not_reported():
    one = estimate_plan(plan(step(StepKind.COPY_FILE, "a")))
    two = estimate_plan(plan(step(StepKind.COPY_FILE, "a"), step(StepKind.CREATE_DIRECTORY, "b")))
    assert "no measurable change" in compare_estimates(one, two)


def test_the_number_of_minutes_is_named():
    small = estimate_plan(plan(step(StepKind.COPY_FILE)))
    large = estimate_plan(plan(step(StepKind.PROGRAM_FPGA)))
    assert "5 minutes longer" in compare_estimates(small, large)
