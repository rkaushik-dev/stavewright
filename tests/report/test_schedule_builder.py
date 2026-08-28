from __future__ import annotations

import json
from datetime import timedelta

from stavewright.clock import utc
from stavewright.fleet.roster import Criticality, Roster, RosterEntry, ShiftPattern
from stavewright.fleet.schedule import Window, schedule_rollout
from stavewright.fleet.waves import Rollout, plan_rollout
from stavewright.model.profile import TerminalProfile
from stavewright.report.builders.fleet import schedule_report
from stavewright.report.document import Outcome
from stavewright.report.render import to_csv, to_json, to_text


def entry(terminal_id: str, line: str, criticality=Criticality.NORMAL):
    return RosterEntry(
        terminal_id=terminal_id,
        profile=TerminalProfile(terminal_id=terminal_id, fpga_revision="PCDC-1.4"),
        line=line,
        position=1,
        criticality=criticality,
        shift_pattern=ShiftPattern.DAY,
    )


PLANT = Roster(
    plant="Wrexham",
    entries=(
        entry("BENCH-01", "bench", Criticality.SPARE),
        entry("SAMPLE-01", "sample"),
        entry("EXPORT-01", "export", Criticality.CRITICAL),
    ),
)

NIGHT = Window(utc(2025, 6, 2, 22), utc(2025, 6, 3, 2), "monday night")
TIGHT = Window(utc(2025, 6, 2, 22), utc(2025, 6, 2, 22, 40), "tight")


def section(report, heading):
    return next(part for part in report.sections if part.heading == heading)


def full():
    return schedule_rollout(plan_rollout(PLANT), [NIGHT])


def test_the_report_names_the_plant():
    report = schedule_report(full())
    assert report.title == "Rollout schedule"
    assert report.subtitle == "Wrexham"


def test_the_facts_count_the_bookings():
    facts = schedule_report(full()).fact_lines()
    assert facts["booked"] == "3"
    assert facts["unplaced"] == "0"


def test_the_facts_carry_the_finish_time():
    schedule = full()
    assert schedule_report(schedule).fact_lines()["finishes"] == schedule.finishes_at.isoformat(sep=" ")


def test_a_schedule_with_nothing_in_it_has_no_finish_time():
    empty = schedule_rollout(Rollout(plant="W"), [NIGHT])
    assert schedule_report(empty).fact_lines()["finishes"] == ""


def test_the_working_time_is_in_hours():
    assert schedule_report(full()).fact_lines()["working_time"] == "1.8"


def test_the_bookings_table_has_a_row_per_wave():
    rows = section(schedule_report(full()), "Bookings").rows
    assert len(rows) == 3


def test_the_bookings_table_is_in_wave_order():
    rows = section(schedule_report(full()), "Bookings").rows
    assert [row[0] for row in rows] == ["1", "2", "3"]


def test_the_bookings_table_names_the_window():
    rows = section(schedule_report(full()), "Bookings").rows
    assert all(row[3] == "monday night" for row in rows)


def test_an_unnamed_window_still_reads():
    unnamed = Window(utc(2025, 6, 2, 22), utc(2025, 6, 3, 2))
    schedule = schedule_rollout(plan_rollout(PLANT), [unnamed])
    rows = section(schedule_report(schedule, [unnamed]), "Bookings").rows
    assert rows[0][3] == "(unnamed)"


def test_the_windows_table_is_optional():
    assert [part.heading for part in schedule_report(full()).sections] == ["Bookings"]


def test_the_windows_table_shows_how_much_was_booked_into_each():
    later = Window(utc(2025, 6, 3, 22), utc(2025, 6, 4, 2), "tuesday night")
    schedule = schedule_rollout(plan_rollout(PLANT), [TIGHT, later])
    rows = section(schedule_report(schedule, [TIGHT, later]), "Windows").rows
    booked = {row[0]: row[4] for row in rows}
    assert booked == {"tight": "1", "tuesday night": "2"}


def test_the_windows_table_shows_the_hours():
    rows = section(schedule_report(full(), [NIGHT]), "Windows").rows
    assert rows[0][3] == "4.0"


def test_a_complete_schedule_passes():
    assert schedule_report(full()).outcome is Outcome.PASS


def test_a_wave_that_fits_nowhere_is_an_error():
    tiny = Window(utc(2025, 6, 2, 22), utc(2025, 6, 2, 22, 5), "tiny")
    report = schedule_report(schedule_rollout(plan_rollout(PLANT), [tiny]), [tiny])
    assert report.outcome is Outcome.FAIL
    findings = section(report, "Findings").findings
    assert len(findings) == 3
    assert all(finding.rule == "schedule/unplaced" for finding in findings)


def test_an_unplaced_finding_names_the_machines():
    tiny = Window(utc(2025, 6, 2, 22), utc(2025, 6, 2, 22, 5), "tiny")
    report = schedule_report(schedule_rollout(plan_rollout(PLANT), [tiny]))
    subjects = {finding.subject for finding in section(report, "Findings").findings}
    assert subjects == {"BENCH-01", "SAMPLE-01", "EXPORT-01"}


def test_an_unplaced_finding_carries_the_wave_number():
    tiny = Window(utc(2025, 6, 2, 22), utc(2025, 6, 2, 22, 5), "tiny")
    report = schedule_report(schedule_rollout(plan_rollout(PLANT), [tiny]))
    numbers = sorted(f.context["wave"] for f in section(report, "Findings").findings)
    assert numbers == [1, 2, 3]


def test_a_partly_placed_schedule_still_fails():
    schedule = schedule_rollout(plan_rollout(PLANT), [TIGHT])
    report = schedule_report(schedule, [TIGHT])
    assert len(section(report, "Bookings").rows) == 1
    assert report.outcome is Outcome.FAIL


def test_the_report_renders_as_text():
    text = to_text(schedule_report(full(), [NIGHT]))
    assert "Rollout schedule" in text
    assert "monday night" in text


def test_the_report_renders_as_json():
    payload = json.loads(to_json(schedule_report(full(), [NIGHT])))
    assert payload["facts"]["booked"] == "3"
    assert len(payload["sections"]) == 2


def test_the_csv_carries_only_the_unplaced_waves():
    tiny = Window(utc(2025, 6, 2, 22), utc(2025, 6, 2, 22, 5), "tiny")
    rows = to_csv(schedule_report(schedule_rollout(plan_rollout(PLANT), [tiny]))).splitlines()
    assert len(rows) == 4


def test_a_longer_per_machine_time_shows_up_in_the_working_time():
    slow = schedule_rollout(plan_rollout(PLANT), [NIGHT], per_machine=timedelta(hours=1))
    assert schedule_report(slow).fact_lines()["working_time"] == "3.5"
