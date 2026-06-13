from __future__ import annotations

from datetime import timedelta

import pytest

from v4t_setup.errors import PlanCycleError
from v4t_setup.model.profile import TerminalProfile
from v4t_setup.plan.cost import SECONDS
from v4t_setup.plan.critical import (
    bottlenecks,
    check_no_cycle,
    critical_path,
    graph_of,
)
from v4t_setup.plan.steps import Plan, Step, StepKind

PROFILE = TerminalProfile(terminal_id="LINE-PC-04", fpga_revision="PCDC-1.4")


def step(step_id: str, kind: StepKind, requires=(), provides=()) -> Step:
    return Step(
        step_id=step_id,
        kind=kind,
        title=step_id,
        requires=tuple(requires),
        provides=tuple(provides),
    )


def plan(*steps: Step) -> Plan:
    return Plan(profile=PROFILE, steps=list(steps))


CHAIN = plan(
    step("dir", StepKind.CREATE_DIRECTORY, provides=("appdir",)),
    step("copy", StepKind.COPY_FILE, requires=("appdir",), provides=("exe",)),
    step("reg", StepKind.WRITE_REGISTRY, requires=("exe",)),
)

WIDE = plan(
    step("dir", StepKind.CREATE_DIRECTORY, provides=("appdir",)),
    step("fpga", StepKind.PROGRAM_FPGA),
    step("copy1", StepKind.COPY_FILE, requires=("appdir",)),
    step("copy2", StepKind.COPY_FILE, requires=("appdir",)),
)


def test_the_graph_comes_out_of_requires_and_provides():
    graph = graph_of(CHAIN)
    assert graph.successors("dir") == ["copy"]
    assert graph.predecessors("reg") == ["copy"]


def test_a_step_that_provides_and_requires_the_same_tag_does_not_wait_on_itself():
    odd = plan(step("one", StepKind.COPY_FILE, requires=("x",), provides=("x",)))
    assert graph_of(odd).edge_count == 0


def test_a_chain_has_no_slack_anywhere():
    result = critical_path(CHAIN)
    assert len(result) == 3
    assert all(result.timings[name].is_critical for name in ("dir", "copy", "reg"))


def test_a_chain_gains_nothing_from_parallelism():
    result = critical_path(CHAIN)
    assert result.span == result.total
    assert result.saving == timedelta()
    assert result.parallel_share == pytest.approx(1.0)


def test_the_path_is_in_the_order_the_steps_would_run():
    assert critical_path(CHAIN).path == ("dir", "copy", "reg")


def test_an_independent_step_is_not_on_the_path():
    result = critical_path(WIDE)
    assert "fpga" not in result.path or "copy1" not in result.path


def test_the_longest_independent_step_is_the_span():
    # Programming the controller is four minutes and nothing waits on it, so
    # it is the whole span on its own.
    result = critical_path(WIDE)
    assert result.span.total_seconds() == pytest.approx(SECONDS[StepKind.PROGRAM_FPGA])


def test_the_total_is_every_step_added_up():
    result = critical_path(WIDE)
    expected = (
        SECONDS[StepKind.CREATE_DIRECTORY]
        + SECONDS[StepKind.PROGRAM_FPGA]
        + 2 * SECONDS[StepKind.COPY_FILE]
    )
    assert result.total.total_seconds() == pytest.approx(expected)


def test_the_saving_is_what_parallelism_would_buy():
    result = critical_path(WIDE)
    assert result.saving == result.total - result.span
    assert result.saving > timedelta()


def test_a_short_step_off_the_path_has_slack():
    result = critical_path(WIDE)
    assert result.slack_for("copy1") > timedelta()


def test_a_step_that_is_not_in_the_plan_has_no_slack():
    assert critical_path(WIDE).slack_for("nonsense") is None


def test_membership_and_iteration():
    result = critical_path(CHAIN)
    assert "dir" in result
    assert "nonsense" not in result
    assert list(result) == list(result.path)


def test_an_empty_plan():
    result = critical_path(plan())
    assert len(result) == 0
    assert result.span == timedelta()
    assert result.total == timedelta()
    assert result.saving == timedelta()
    assert result.parallel_share == 1.0


def test_one_step_is_its_own_critical_path():
    one = plan(step("only", StepKind.COPY_FILE))
    result = critical_path(one)
    assert result.path == ("only",)
    assert result.span == result.total


def test_two_independent_steps_of_the_same_length():
    both = plan(
        step("a", StepKind.COPY_FILE),
        step("b", StepKind.COPY_FILE),
    )
    result = critical_path(both)
    assert result.span.total_seconds() == pytest.approx(SECONDS[StepKind.COPY_FILE])
    assert result.total == 2 * result.span


def test_the_path_is_the_same_every_time():
    both = plan(
        step("a", StepKind.COPY_FILE),
        step("b", StepKind.COPY_FILE),
    )
    assert critical_path(both).path == critical_path(both).path


def test_a_diamond_takes_the_longer_arm():
    diamond = plan(
        step("start", StepKind.CREATE_DIRECTORY, provides=("root",)),
        step("slow", StepKind.STAGE_DRIVER, requires=("root",), provides=("slow",)),
        step("fast", StepKind.WRITE_REGISTRY, requires=("root",), provides=("fast",)),
        step("end", StepKind.CREATE_SHORTCUT, requires=("slow", "fast")),
    )
    result = critical_path(diamond)
    assert "slow" in result.path
    assert "fast" not in result.path
    assert result.slack_for("fast") > timedelta()


def test_the_earliest_and_latest_starts_line_up_on_the_path():
    result = critical_path(CHAIN)
    for name in result.path:
        timing = result.timings[name]
        assert timing.earliest_start == timing.latest_start


def test_a_timing_describes_itself():
    result = critical_path(WIDE)
    assert "critical" in result.timings["fpga"].describe()
    assert "slack" in result.timings["copy1"].describe()


def test_the_path_describes_itself():
    text = critical_path(WIDE).describe()
    assert "steps on the critical path" in text
    assert "minutes" in text


def test_the_bottlenecks_are_the_longest_steps_on_the_path():
    long_chain = plan(
        step("dir", StepKind.CREATE_DIRECTORY, provides=("a",)),
        step("driver", StepKind.STAGE_DRIVER, requires=("a",), provides=("b",)),
        step("reg", StepKind.WRITE_REGISTRY, requires=("b",)),
    )
    worst = bottlenecks(critical_path(long_chain))
    assert worst[0].step_id == "driver"


def test_the_bottleneck_list_is_capped():
    many = plan(
        *[
            step(f"s{n}", StepKind.COPY_FILE, requires=(f"t{n}",), provides=(f"t{n + 1}",))
            for n in range(20)
        ]
    )
    assert len(bottlenecks(critical_path(many), limit=3)) == 3


def test_a_plan_with_no_cycle_passes_the_check():
    assert check_no_cycle(CHAIN) is None


def test_a_cycle_is_refused():
    looping = plan(
        step("a", StepKind.COPY_FILE, requires=("y",), provides=("x",)),
        step("b", StepKind.COPY_FILE, requires=("x",), provides=("y",)),
    )
    with pytest.raises(PlanCycleError, match="loop") as caught:
        check_no_cycle(looping)
    assert set(caught.value.context["cycle"]) >= {"a", "b"}


def test_a_prebuilt_graph_can_be_passed_in():
    graph = graph_of(CHAIN)
    assert critical_path(CHAIN, graph).path == critical_path(CHAIN).path
