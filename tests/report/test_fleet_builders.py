from __future__ import annotations

import json
from datetime import date

from v4t_setup.fleet.roster import Criticality, Roster, RosterEntry, ShiftPattern
from v4t_setup.fleet.waves import check_rollout, plan_rollout
from v4t_setup.model.profile import TerminalProfile
from v4t_setup.report.builders import rollout_report, roster_report
from v4t_setup.report.document import Outcome
from v4t_setup.report.render import to_json, to_text


def entry(
    terminal_id: str,
    line: str,
    position: int,
    criticality: Criticality = Criticality.NORMAL,
    shift: ShiftPattern = ShiftPattern.DAY,
    commissioned: date | None = None,
) -> RosterEntry:
    return RosterEntry(
        terminal_id=terminal_id,
        profile=TerminalProfile(terminal_id=terminal_id, fpga_revision="PCDC-1.4"),
        line=line,
        position=position,
        criticality=criticality,
        shift_pattern=shift,
        commissioned=commissioned,
    )


PLANT = Roster(
    plant="Wrexham",
    entries=(
        entry("EXPORT-01", "export", 1, Criticality.CRITICAL, ShiftPattern.CONTINUOUS,
              date(2024, 11, 3)),
        entry("EXPORT-02", "export", 2, Criticality.CRITICAL, ShiftPattern.CONTINUOUS),
        entry("SAMPLE-01", "sample", 1),
        entry("BENCH-01", "bench", 1, Criticality.SPARE),
    ),
)


def section(report, heading):
    return next(part for part in report.sections if part.heading == heading)


def test_the_roster_report_names_the_plant():
    report = roster_report(PLANT)
    assert report.title == "Plant roster"
    assert report.subtitle == "Wrexham"


def test_the_roster_report_counts_terminals_and_lines():
    facts = roster_report(PLANT).facts
    assert facts["terminals"] == "4"
    assert facts["lines"] == "3"


def test_the_roster_table_has_a_row_per_terminal():
    rows = section(roster_report(PLANT), "Terminals").rows
    assert len(rows) == 4


def test_the_roster_table_is_in_working_order():
    rows = section(roster_report(PLANT), "Terminals").rows
    assert [row[0] for row in rows] == ["BENCH-01", "SAMPLE-01", "EXPORT-01", "EXPORT-02"]


def test_the_roster_table_carries_the_machine_from_the_profile():
    rows = section(roster_report(PLANT), "Terminals").rows
    assert all(row[4] == "s-jet600" for row in rows)


def test_a_terminal_that_has_never_been_commissioned_says_never():
    rows = section(roster_report(PLANT), "Terminals").rows
    dates = {row[0]: row[5] for row in rows}
    assert dates["EXPORT-01"] == "2024-11-03"
    assert dates["EXPORT-02"] == "never"


def test_the_never_commissioned_list_is_there():
    lines = section(roster_report(PLANT), "Never commissioned").lines
    assert lines == ["BENCH-01", "SAMPLE-01", "EXPORT-02"]


def test_a_fully_commissioned_plant_has_no_such_section():
    done = Roster(
        plant="Wrexham",
        entries=(entry("A-01", "one", 1, commissioned=date(2025, 1, 1)),),
    )
    assert [part.heading for part in roster_report(done).sections] == ["Terminals"]


def test_a_roster_report_passes():
    assert roster_report(PLANT).outcome is Outcome.PASS


def test_the_roster_report_renders_as_text():
    text = to_text(roster_report(PLANT))
    assert "Plant roster" in text
    assert "EXPORT-01" in text


def test_the_rollout_report_names_the_plant():
    report = rollout_report(plan_rollout(PLANT))
    assert report.title == "Rollout plan"
    assert report.subtitle == "Wrexham"


def test_the_rollout_report_counts_the_waves():
    plan = plan_rollout(PLANT)
    facts = rollout_report(plan).facts
    assert facts["waves"] == str(len(plan))
    assert facts["terminals"] == "4"


def test_the_rollout_report_counts_the_stoppages():
    assert rollout_report(plan_rollout(PLANT)).facts["stoppages"] == "2"


def test_the_wave_table_has_a_row_per_wave():
    plan = plan_rollout(PLANT)
    rows = section(rollout_report(plan), "Waves").rows
    assert len(rows) == len(plan)


def test_the_wave_table_lists_the_terminals_in_each_wave():
    rows = section(rollout_report(plan_rollout(PLANT)), "Waves").rows
    assert rows[0][2] == "BENCH-01"


def test_the_wave_table_marks_the_stoppages():
    rows = section(rollout_report(plan_rollout(PLANT)), "Waves").rows
    assert [row[4] for row in rows] == ["no", "no", "yes", "yes"]


def test_a_sound_plan_passes():
    plan = plan_rollout(PLANT)
    report = rollout_report(plan, check_rollout(PLANT, plan))
    assert report.outcome is Outcome.PASS
    assert [part.heading for part in report.sections] == ["Waves"]


def test_problems_arrive_as_findings():
    report = rollout_report(plan_rollout(PLANT), ["EXPORT-01 is in the roster and in no wave"])
    findings = section(report, "Findings").findings
    assert len(findings) == 1
    assert findings[0].rule == "rollout"


def test_a_plan_with_problems_fails():
    report = rollout_report(plan_rollout(PLANT), ["something is wrong"])
    assert report.outcome is Outcome.FAIL


def test_the_rollout_report_renders_as_text():
    text = to_text(rollout_report(plan_rollout(PLANT)))
    assert "Rollout plan" in text
    assert "critical" in text


def test_the_rollout_report_renders_as_json():
    payload = json.loads(to_json(rollout_report(plan_rollout(PLANT))))
    assert payload["title"] == "Rollout plan"
    waves = next(part for part in payload["sections"] if part["heading"] == "Waves")
    assert len(waves["rows"]) == 4


def test_an_empty_plant_still_makes_a_report():
    empty = Roster(plant="Nowhere")
    report = rollout_report(plan_rollout(empty))
    assert report.facts["waves"] == "0"
    assert section(report, "Waves").rows == []
