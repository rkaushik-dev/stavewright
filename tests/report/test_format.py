from __future__ import annotations

from datetime import timedelta

import pytest

from v4t_setup.clock import utc
from v4t_setup.report import format as fmt

WHEN = utc(2025, 6, 3, 1, 30)


def test_a_moment_is_written_for_a_person():
    assert fmt.moment(WHEN) == "2025-06-03 01:30:00+00:00"


def test_a_moment_has_no_t_in_it():
    # The T form is for the JSON renderer, where a program is reading it.
    assert "T01:30" not in fmt.moment(WHEN)


def test_a_missing_moment_is_blank():
    assert fmt.moment(None) == ""


def test_a_day_drops_the_time():
    assert fmt.day(WHEN) == "2025-06-03"


def test_a_missing_day_is_blank():
    assert fmt.day(None) == ""


def test_minutes_to_one_place_by_default():
    assert fmt.minutes(timedelta(seconds=90)) == "1.5"


def test_minutes_can_be_whole():
    assert fmt.minutes(timedelta(seconds=90), places=0) == "2"


def test_minutes_of_nothing_is_zero_not_blank():
    assert fmt.minutes(timedelta()) == "0.0"


def test_a_missing_duration_is_blank():
    assert fmt.minutes(None) == ""
    assert fmt.hours(None) == ""


def test_hours_to_one_place():
    assert fmt.hours(timedelta(hours=4)) == "4.0"
    assert fmt.hours(timedelta(minutes=90)) == "1.5"


def test_hours_can_be_whole():
    assert fmt.hours(timedelta(minutes=90), places=0) == "2"


def test_a_count_of_zero_prints():
    assert fmt.count(0) == "0"


def test_a_count_nobody_took_is_blank():
    assert fmt.count(None) == ""


@pytest.mark.parametrize("value", [1, 42, 1000])
def test_a_count_is_the_number(value):
    assert fmt.count(value) == str(value)


def test_blank_is_the_empty_string():
    # Not "none", not "-", not "n/a". A table with three spellings of nothing
    # in it is a table somebody sorts and then cannot read.
    assert fmt.BLANK == ""


def test_two_tables_in_one_report_agree_about_a_duration():
    from v4t_setup.clock import utc as at
    from v4t_setup.fleet.roster import Criticality, Roster, RosterEntry, ShiftPattern
    from v4t_setup.fleet.schedule import Window, schedule_rollout
    from v4t_setup.fleet.waves import plan_rollout
    from v4t_setup.model.profile import TerminalProfile
    from v4t_setup.report.builders.fleet import schedule_report

    entry = RosterEntry(
        terminal_id="A-01",
        profile=TerminalProfile(terminal_id="A-01", fpga_revision="PCDC-1.4"),
        line="one",
        position=1,
        criticality=Criticality.NORMAL,
        shift_pattern=ShiftPattern.DAY,
    )
    window = Window(at(2025, 6, 2, 22), at(2025, 6, 3, 2), "night")
    schedule = schedule_rollout(plan_rollout(Roster(plant="W", entries=(entry,))), [window])
    report = schedule_report(schedule, [window])

    windows = next(part for part in report.sections if part.heading == "Windows")
    bookings = next(part for part in report.sections if part.heading == "Bookings")
    # Both were written by hand before and the hours column had one decimal
    # place while the facts had none.
    assert windows.rows[0][3] == fmt.hours(window.duration)
    assert bookings.rows[0][1] == fmt.moment(schedule.bookings[0].start)
