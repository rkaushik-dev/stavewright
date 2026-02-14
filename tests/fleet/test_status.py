from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from v4t_setup.clock import frozen, utc
from v4t_setup.fleet.roster import Criticality, Roster, RosterEntry, ShiftPattern
from v4t_setup.fleet.status import (
    DEFAULT_STALE_AFTER,
    PlantStatus,
    Standing,
    plant_status,
)
from v4t_setup.model.profile import TerminalProfile
from v4t_setup.verify.history import History, RunSummary

NOW = utc(2025, 6, 2, 12)


def entry(
    terminal_id: str,
    line: str = "export",
    commissioned: date | None = None,
    criticality: Criticality = Criticality.NORMAL,
) -> RosterEntry:
    return RosterEntry(
        terminal_id=terminal_id,
        profile=TerminalProfile(terminal_id=terminal_id, fpga_revision="PCDC-1.4"),
        line=line,
        position=1,
        criticality=criticality,
        shift_pattern=ShiftPattern.DAY,
        commissioned=commissioned,
    )


def run(
    terminal: str,
    started=NOW - timedelta(days=30),
    *,
    finished=True,
    completed: int = 12,
) -> RunSummary:
    return RunSummary(
        path=Path(f"{terminal}.jsonl"),
        run_id=f"run-{terminal}",
        terminal=terminal,
        started_at=started,
        finished_at=started + timedelta(minutes=20) if finished else None,
        outcome="complete" if finished else "",
        completed=completed,
    )


def status(roster: Roster, *runs: RunSummary, **kwargs) -> PlantStatus:
    return plant_status(roster, History(runs=list(runs)), moment=NOW, **kwargs)


def one(roster: Roster, *runs: RunSummary, **kwargs):
    return status(roster, *runs, **kwargs).entries[0]


def test_a_machine_with_neither_record_has_never_been_done():
    result = one(Roster(plant="W", entries=(entry("A-01"),)))
    assert result.standing is Standing.NEVER
    assert result.last_run is None
    assert result.note == ""


def test_a_machine_with_both_records_is_commissioned():
    roster = Roster(plant="W", entries=(entry("A-01", commissioned=date(2025, 5, 3)),))
    result = one(roster, run("A-01"))
    assert result.standing is Standing.COMMISSIONED
    assert result.last_run is not None
    assert result.note == ""


def test_a_run_older_than_the_stale_window():
    roster = Roster(plant="W", entries=(entry("A-01", commissioned=date(2020, 1, 1)),))
    result = one(roster, run("A-01", started=NOW - timedelta(days=500)))
    assert result.standing is Standing.STALE
    assert "500 days ago" in result.note


def test_a_run_just_inside_the_window_is_not_stale():
    roster = Roster(plant="W", entries=(entry("A-01", commissioned=date(2024, 7, 1)),))
    result = one(roster, run("A-01", started=NOW - DEFAULT_STALE_AFTER + timedelta(days=1)))
    assert result.standing is Standing.COMMISSIONED


def test_the_stale_window_can_be_narrowed():
    roster = Roster(plant="W", entries=(entry("A-01", commissioned=date(2025, 5, 3)),))
    result = one(roster, run("A-01"), stale_after=timedelta(days=7))
    assert result.standing is Standing.STALE


def test_a_run_that_never_ended_blocks_everything():
    roster = Roster(plant="W", entries=(entry("A-01", commissioned=date(2025, 5, 3)),))
    result = one(roster, run("A-01", finished=False))
    assert result.standing is Standing.UNFINISHED
    assert "never ended" in result.note
    assert result.standing.blocks_others


def test_an_unfinished_run_wins_over_everything_else():
    # Even against a roster that says nothing happened, and even when the run
    # is old enough to be stale.
    roster = Roster(plant="W", entries=(entry("A-01"),))
    result = one(roster, run("A-01", started=NOW - timedelta(days=900), finished=False))
    assert result.standing is Standing.UNFINISHED


def test_a_roster_date_with_no_run_is_disputed():
    roster = Roster(plant="W", entries=(entry("A-01", commissioned=date(2024, 3, 1)),))
    result = one(roster)
    assert result.standing is Standing.DISPUTED
    assert "no run in the journals" in result.note


def test_a_run_with_no_roster_date_is_disputed():
    roster = Roster(plant="W", entries=(entry("A-01"),))
    result = one(roster, run("A-01"))
    assert result.standing is Standing.DISPUTED
    assert "the roster says this was never done" in result.note


def test_the_latest_run_is_the_one_that_counts():
    roster = Roster(plant="W", entries=(entry("A-01", commissioned=date(2025, 5, 3)),))
    old = run("A-01", started=NOW - timedelta(days=400))
    recent = run("A-01", started=NOW - timedelta(days=2))
    result = one(roster, old, recent)
    assert result.standing is Standing.COMMISSIONED
    assert result.last_run.started_at == recent.started_at


def test_a_run_for_another_terminal_is_ignored():
    roster = Roster(plant="W", entries=(entry("A-01"),))
    assert one(roster, run("B-01")).standing is Standing.NEVER


def test_which_standings_mean_work():
    assert Standing.NEVER.needs_work
    assert Standing.STALE.needs_work
    assert Standing.UNFINISHED.needs_work
    assert not Standing.COMMISSIONED.needs_work
    assert not Standing.DISPUTED.needs_work


def test_only_an_unfinished_run_blocks():
    assert Standing.UNFINISHED.blocks_others
    assert not any(
        standing.blocks_others
        for standing in Standing
        if standing is not Standing.UNFINISHED
    )


def test_a_plant_is_read_in_working_order():
    roster = Roster(
        plant="Wrexham",
        entries=(
            entry("EXPORT-01", "export", criticality=Criticality.CRITICAL),
            entry("BENCH-01", "bench", criticality=Criticality.SPARE),
        ),
    )
    assert [item.terminal_id for item in status(roster)] == ["BENCH-01", "EXPORT-01"]


def test_the_plant_name_travels():
    assert status(Roster(plant="Wrexham")).plant == "Wrexham"


def test_an_empty_plant():
    result = status(Roster(plant="Nowhere"))
    assert len(result) == 0
    assert result.counts() == {}
    assert result.outstanding == []


def test_counting_by_standing():
    roster = Roster(
        plant="W",
        entries=(
            entry("A-01", "one"),
            entry("B-01", "two", commissioned=date(2025, 5, 3)),
            entry("C-01", "three", commissioned=date(2020, 1, 1)),
        ),
    )
    result = status(roster, run("B-01"), run("C-01", started=NOW - timedelta(days=800)))
    assert result.counts() == {"commissioned": 1, "never": 1, "stale": 1}


def test_selecting_by_standing():
    roster = Roster(plant="W", entries=(entry("A-01", "one"), entry("B-01", "two")))
    result = status(roster)
    assert len(result.of_standing(Standing.NEVER)) == 2
    assert result.of_standing(Standing.COMMISSIONED) == []


def test_what_is_still_outstanding():
    roster = Roster(
        plant="W",
        entries=(entry("A-01", "one"), entry("B-01", "two", commissioned=date(2025, 5, 3))),
    )
    assert status(roster, run("B-01")).outstanding == ["A-01"]


def test_what_blocks_the_rest():
    roster = Roster(
        plant="W",
        entries=(entry("A-01", "one", commissioned=date(2025, 5, 3)), entry("B-01", "two")),
    )
    result = status(roster, run("A-01", finished=False))
    assert [item.terminal_id for item in result.blocked] == ["A-01"]


def test_describing_a_terminal():
    roster = Roster(plant="W", entries=(entry("A-01", commissioned=date(2025, 5, 3)),))
    text = one(roster, run("A-01")).describe()
    assert text.startswith("A-01 on export: commissioned, 2025-05-03")


def test_describing_a_terminal_with_no_run():
    assert "no run" in one(Roster(plant="W", entries=(entry("A-01"),))).describe()


def test_describing_a_terminal_with_a_note():
    roster = Roster(plant="W", entries=(entry("A-01", commissioned=date(2024, 3, 1)),))
    assert "(the roster carries a date" in one(roster).describe()


def test_describing_a_plant():
    roster = Roster(plant="W", entries=(entry("A-01", "one"), entry("B-01", "two")))
    assert len(status(roster).describe()) == 2


def test_the_clock_is_used_when_no_moment_is_given():
    roster = Roster(plant="W", entries=(entry("A-01", commissioned=date(2025, 5, 3)),))
    history = History(runs=[run("A-01", started=utc(2025, 6, 1))])
    with frozen(utc(2027, 1, 1)):
        result = plant_status(roster, history)
    assert result.entries[0].standing is Standing.STALE


@pytest.mark.parametrize("standing", list(Standing))
def test_every_standing_has_a_readable_value(standing):
    assert standing.value.isalpha()
