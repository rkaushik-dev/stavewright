from __future__ import annotations

import json
from datetime import date

from stavewright.finding import Severity
from stavewright.fleet.audit import audit_roster
from stavewright.fleet.roster import Criticality, Roster, RosterEntry, ShiftPattern
from stavewright.fleet.waves import check_rollout, plan_rollout
from stavewright.model.profile import TerminalProfile
from stavewright.report.builders.fleet import (
    rollout_report,
    roster_audit_report,
    roster_report,
)
from stavewright.report.document import Outcome
from stavewright.report.render import to_json, to_text


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


BENT = Roster(
    plant="Wrexham",
    entries=(
        entry("EXPORT-01", "export", 1, commissioned=date(2044, 1, 1)),
        entry("EXPORT-03", "export", 3),
        entry("SAMPLE-01", "sample", 1),
    ),
)


def section(report, heading):
    return next(part for part in report.sections if part.heading == heading)


def test_the_roster_report_names_the_plant():
    report = roster_report(PLANT)
    assert report.title == "Plant roster"
    assert report.subtitle == "Wrexham"


def test_the_roster_report_counts_terminals_and_lines():
    facts = roster_report(PLANT).fact_lines()
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
    facts = rollout_report(plan).fact_lines()
    assert facts["waves"] == str(len(plan))
    assert facts["terminals"] == "4"


def test_the_rollout_report_counts_the_stoppages():
    assert rollout_report(plan_rollout(PLANT)).fact_lines()["stoppages"] == "2"


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
    assert report.fact_lines()["waves"] == "0"
    assert section(report, "Waves").rows == []


def test_the_audit_report_names_the_plant_when_it_is_clean():
    report = roster_audit_report(PLANT, [])
    assert report.title == "Roster audit"
    assert report.subtitle == "Wrexham"
    assert report.outcome is Outcome.PASS


def test_the_audit_report_names_the_worst_line_when_there_is_one():
    findings = audit_roster(BENT, None, today=date(2025, 6, 1))
    report = roster_audit_report(BENT, findings)
    assert report.subtitle == "Wrexham, worst line export"


def test_the_audit_report_lists_every_line():
    rows = section(roster_audit_report(PLANT, []), "Lines").rows
    assert [row[0] for row in rows] == ["bench", "export", "sample"]
    assert [row[1] for row in rows] == ["1", "2", "1"]


def test_the_audit_report_counts_findings_per_line():
    findings = audit_roster(BENT, None, today=date(2025, 6, 1))
    rows = section(roster_audit_report(BENT, findings), "Lines").rows
    counted = {row[0]: row[4] for row in rows}
    assert int(counted["export"]) >= 1


def test_the_audit_report_says_whether_it_read_the_profiles():
    checked = roster_audit_report(PLANT, [], profiles_checked=True)
    skipped = roster_audit_report(PLANT, [], profiles_checked=False)
    assert checked.fact_lines()["profiles_checked"] == "yes"
    assert skipped.fact_lines()["profiles_checked"] == "no"


def test_the_audit_report_says_out_loud_what_it_did_not_check():
    report = roster_audit_report(PLANT, [], profiles_checked=False)
    assert "Not checked" in [part.heading for part in report.sections]


def test_a_checked_audit_does_not_carry_the_disclaimer():
    report = roster_audit_report(PLANT, [], profiles_checked=True)
    assert "Not checked" not in [part.heading for part in report.sections]


def test_the_audit_report_fails_on_an_error():
    findings = audit_roster(BENT, None, today=date(2025, 6, 1))
    assert any(finding.severity is Severity.ERROR for finding in findings)
    assert roster_audit_report(BENT, findings).outcome is Outcome.FAIL


def test_the_audit_report_renders_as_text_and_json():
    findings = audit_roster(BENT, None, today=date(2025, 6, 1))
    report = roster_audit_report(BENT, findings)
    assert "Roster audit" in to_text(report)
    assert json.loads(to_json(report))["facts"]["plant"] == "Wrexham"
