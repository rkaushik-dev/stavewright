from __future__ import annotations

import pytest

from v4t_setup.errors import PlanError
from v4t_setup.fleet.roster import Criticality, Roster, RosterEntry, ShiftPattern
from v4t_setup.fleet.waves import Rollout, check_rollout, plan_rollout
from v4t_setup.model.profile import TerminalProfile


def entry(
    terminal_id: str,
    line: str,
    position: int,
    criticality: Criticality = Criticality.NORMAL,
    shift: ShiftPattern = ShiftPattern.DAY,
) -> RosterEntry:
    return RosterEntry(
        terminal_id=terminal_id,
        profile=TerminalProfile(terminal_id=terminal_id, fpga_revision="PCDC-1.4"),
        line=line,
        position=position,
        criticality=criticality,
        shift_pattern=shift,
    )


def roster(*entries: RosterEntry, plant: str = "Wrexham") -> Roster:
    return Roster(plant=plant, entries=entries)


def line_of(name: str, count: int, criticality: Criticality, shift=ShiftPattern.DAY):
    return [
        entry(f"{name.upper()}-{n:02d}", name, n, criticality, shift) for n in range(1, count + 1)
    ]


PLANT = roster(
    *line_of("export", 3, Criticality.CRITICAL, ShiftPattern.CONTINUOUS),
    *line_of("sample", 4, Criticality.NORMAL),
    *line_of("bench", 2, Criticality.SPARE),
)


def test_a_plan_carries_the_plant_name():
    assert plan_rollout(PLANT).plant == "Wrexham"


def test_an_empty_roster_makes_an_empty_plan():
    plan = plan_rollout(roster())
    assert len(plan) == 0
    assert plan.terminal_ids == []


def test_every_machine_is_covered_exactly_once():
    plan = plan_rollout(PLANT)
    assert sorted(plan.terminal_ids) == PLANT.terminal_ids


def test_the_plan_checks_out():
    assert check_rollout(PLANT, plan_rollout(PLANT)) == []


def test_waves_are_numbered_from_one_without_gaps():
    plan = plan_rollout(PLANT)
    assert [wave.number for wave in plan] == list(range(1, len(plan) + 1))


def test_the_spares_go_first():
    plan = plan_rollout(PLANT)
    assert plan.waves[0].criticality is Criticality.SPARE


def test_the_critical_line_goes_last():
    plan = plan_rollout(PLANT)
    assert plan.waves[-1].criticality is Criticality.CRITICAL


def test_a_wave_never_mixes_criticalities():
    for wave in plan_rollout(PLANT):
        assert {machine.criticality for machine in wave} == {wave.criticality}


def test_a_wave_never_takes_two_machines_off_one_line():
    for wave in plan_rollout(PLANT):
        assert len(wave.lines) == len(wave)


def test_one_critical_machine_at_a_time():
    critical = [wave for wave in plan_rollout(PLANT) if wave.criticality is Criticality.CRITICAL]
    assert len(critical) == 3
    assert all(len(wave) == 1 for wave in critical)


def test_the_spares_share_a_wave_because_they_are_on_one_line():
    # Both bench machines are on the bench line, so they cannot.
    spares = [wave for wave in plan_rollout(PLANT) if wave.criticality is Criticality.SPARE]
    assert [len(wave) for wave in spares] == [1, 1]


def test_machines_on_different_lines_do_share_a_wave():
    mixed = roster(
        entry("A-01", "one", 1),
        entry("B-01", "two", 1),
        entry("C-01", "three", 1),
    )
    plan = plan_rollout(mixed)
    assert len(plan) == 1
    assert len(plan.waves[0]) == 3


def test_the_normal_limit_stops_a_fourth_line_joining():
    mixed = roster(*(entry(f"L{n}-01", f"line{n}", 1) for n in range(1, 6)))
    plan = plan_rollout(mixed)
    assert [len(wave) for wave in plan] == [3, 2]


def test_a_line_is_worked_upstream_first():
    plan = plan_rollout(PLANT)
    order = [name for name in plan.terminal_ids if name.startswith("SAMPLE")]
    assert order == sorted(order)


def test_a_batch_limit_narrows_the_waves():
    plan = plan_rollout(PLANT, batch_limit=1)
    assert all(len(wave) == 1 for wave in plan)
    assert len(plan) == 9


def test_a_batch_limit_cannot_widen_a_wave():
    plan = plan_rollout(PLANT, batch_limit=8)
    critical = [wave for wave in plan if wave.criticality is Criticality.CRITICAL]
    assert all(len(wave) == 1 for wave in critical)


def test_a_batch_limit_below_one_is_refused():
    with pytest.raises(PlanError, match="at least one machine"):
        plan_rollout(PLANT, batch_limit=0)


def test_planning_a_subset():
    plan = plan_rollout(PLANT, only=["EXPORT-02", "BENCH-01"])
    assert sorted(plan.terminal_ids) == ["BENCH-01", "EXPORT-02"]


def test_a_subset_still_runs_least_disruptive_first():
    plan = plan_rollout(PLANT, only=["EXPORT-02", "BENCH-01"])
    assert plan.terminal_ids == ["BENCH-01", "EXPORT-02"]


def test_a_subset_ignores_the_order_it_was_given():
    first = plan_rollout(PLANT, only=["BENCH-01", "EXPORT-02"]).terminal_ids
    second = plan_rollout(PLANT, only=["EXPORT-02", "BENCH-01"]).terminal_ids
    assert first == second


def test_a_repeated_terminal_in_the_subset_is_taken_once():
    plan = plan_rollout(PLANT, only=["BENCH-01", "BENCH-01"])
    assert plan.terminal_ids == ["BENCH-01"]


def test_a_subset_naming_something_unknown_says_which():
    with pytest.raises(PlanError) as caught:
        plan_rollout(PLANT, only=["BENCH-01", "GONE-01"])
    assert caught.value.context["missing"] == ["GONE-01"]


def test_a_subset_plan_checks_out_when_told_it_is_partial():
    plan = plan_rollout(PLANT, only=["BENCH-01"])
    assert check_rollout(PLANT, plan, whole_plant=False) == []


def test_a_subset_plan_is_reported_as_incomplete_by_default():
    plan = plan_rollout(PLANT, only=["BENCH-01"])
    problems = check_rollout(PLANT, plan)
    assert len(problems) == len(PLANT) - 1
    assert all("in no wave" in problem for problem in problems)


def test_finding_the_wave_a_machine_is_in():
    plan = plan_rollout(PLANT)
    assert "EXPORT-01" in plan.wave_of("EXPORT-01").terminal_ids


def test_asking_about_a_machine_that_is_not_in_the_plan():
    plan = plan_rollout(PLANT, only=["BENCH-01"])
    with pytest.raises(PlanError, match="does not cover this terminal"):
        plan.wave_of("EXPORT-01")


def test_a_continuous_line_needs_a_stoppage():
    plan = plan_rollout(PLANT)
    assert plan.wave_of("EXPORT-01").needs_stoppage


def test_a_day_line_does_not():
    plan = plan_rollout(PLANT)
    assert not plan.wave_of("SAMPLE-01").needs_stoppage


def test_counting_the_stoppages():
    assert plan_rollout(PLANT).stoppages == 3


def test_describing_a_wave():
    plan = plan_rollout(PLANT)
    text = plan.wave_of("EXPORT-01").describe()
    assert "1 critical machine on export" in text
    assert "needs a stoppage" in text


def test_describing_a_wave_with_several_machines():
    mixed = roster(entry("A-01", "one", 1), entry("B-01", "two", 1))
    assert "2 normal machines on one, two" in plan_rollout(mixed).waves[0].describe()


def test_describing_the_whole_plan():
    lines = plan_rollout(PLANT).describe()
    assert len(lines) == len(plan_rollout(PLANT))
    assert lines[0].startswith("wave 1:")


def test_the_check_catches_a_hand_edited_plan_that_drops_a_machine():
    plan = plan_rollout(PLANT)
    trimmed = Rollout(plant=plan.plant, waves=plan.waves[1:])
    assert any("in no wave" in problem for problem in check_rollout(PLANT, trimmed))


def test_the_check_catches_a_wave_that_is_too_wide():
    from v4t_setup.fleet.waves import Wave

    wide = Wave(
        number=1,
        entries=tuple(line_of("one", 1, Criticality.CRITICAL))
        + tuple(line_of("two", 1, Criticality.CRITICAL)),
        criticality=Criticality.CRITICAL,
    )
    problems = check_rollout(roster(), Rollout(plant="W", waves=(wide,)))
    assert any("critical allows 1" in problem for problem in problems)


def test_the_check_catches_two_machines_off_one_line():
    from v4t_setup.fleet.waves import Wave

    both = Wave(
        number=1,
        entries=tuple(line_of("sample", 2, Criticality.NORMAL)),
        criticality=Criticality.NORMAL,
    )
    problems = check_rollout(roster(), Rollout(plant="W", waves=(both,)))
    assert any("two machines off one line" in problem for problem in problems)


def test_the_check_catches_waves_in_the_wrong_order():
    plan = plan_rollout(PLANT)
    reversed_plan = Rollout(plant=plan.plant, waves=tuple(reversed(plan.waves)))
    problems = check_rollout(PLANT, reversed_plan)
    assert "waves do not run least disruptive first" in problems


def test_the_check_catches_a_machine_in_two_waves():
    plan = plan_rollout(PLANT)
    doubled = Rollout(plant=plan.plant, waves=(*plan.waves, plan.waves[0]))
    assert any("more than one wave" in problem for problem in check_rollout(PLANT, doubled))


def test_the_check_catches_a_mixed_wave():
    from v4t_setup.fleet.waves import Wave

    mixed = Wave(
        number=1,
        entries=(entry("A-01", "one", 1, Criticality.SPARE),),
        criticality=Criticality.NORMAL,
    )
    problems = check_rollout(roster(), Rollout(plant="W", waves=(mixed,)))
    assert any("mixes criticalities" in problem for problem in problems)


def test_a_wave_reports_its_lines_sorted():
    mixed = roster(entry("B-01", "two", 1), entry("A-01", "one", 1))
    assert plan_rollout(mixed).waves[0].lines == ["one", "two"]


def test_iterating_a_plan_yields_waves():
    plan = plan_rollout(PLANT)
    assert [wave.number for wave in plan] == [wave.number for wave in plan.waves]


def test_a_long_line_takes_one_wave_per_machine():
    long_line = roster(*line_of("filler", 6, Criticality.NORMAL))
    plan = plan_rollout(long_line)
    assert len(plan) == 6
