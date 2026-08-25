"""The cross document checks on a roster that parsed.

Everything here is built by hand rather than parsed, because several of the
states being checked cannot be written in a roster file at all. A terminal on
two lines is one: the parser sees two valid tables and has nothing to object
to. Building the Roster directly is the only way to get one.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from v4t_setup.finding import Severity
from v4t_setup.fleet.audit import (
    STALE_AFTER,
    audit_roster,
    check_critical_lines,
    check_dates,
    check_line_hardware,
    check_one_line_each,
    check_positions,
    check_profile_identity,
    check_unclaimed_profiles,
    describe_entry,
    worst_line,
)
from v4t_setup.fleet.roster import Criticality, Roster, RosterEntry, ShiftPattern
from v4t_setup.model.profile import TerminalModel, TerminalProfile

TODAY = date(2025, 6, 1)


def profile(terminal_id: str, **kwargs) -> TerminalProfile:
    fields = {"fpga_revision": "PCDC-1.4", **kwargs}
    return TerminalProfile(terminal_id=terminal_id, **fields)


def entry(
    terminal_id: str,
    line: str = "export",
    position: int = 1,
    criticality: Criticality = Criticality.NORMAL,
    commissioned: date | None = None,
    profile_for: TerminalProfile | None = None,
) -> RosterEntry:
    return RosterEntry(
        terminal_id=terminal_id,
        profile=profile_for or profile(terminal_id),
        line=line,
        position=position,
        criticality=criticality,
        shift_pattern=ShiftPattern.DAY,
        commissioned=commissioned,
    )


def roster(*entries: RosterEntry, plant: str = "Wrexham") -> Roster:
    return Roster(plant=plant, entries=entries)


def rules(findings) -> set[str]:
    return {finding.rule for finding in findings}


def subjects(findings) -> list[str]:
    return [finding.subject for finding in findings]


def test_a_terminal_on_two_lines_is_an_error():
    found = check_one_line_each(
        roster(entry("A-01", "export", 1), entry("A-01", "sample", 1))
    )
    assert len(found) == 1
    assert found[0].severity is Severity.ERROR
    assert found[0].context["lines"] == ["export", "sample"]


def test_a_terminal_on_one_line_is_not():
    assert check_one_line_each(roster(entry("A-01"), entry("A-02", position=2))) == []


def test_the_parser_cannot_catch_the_two_line_case():
    """Why this check is here and not in parse_roster.

    Terminal ids are keys inside a line's own table, so the same id under two
    lines is two well formed tables and the parser has nothing to object to.
    """
    import tomllib

    from v4t_setup.fleet.roster import parse_roster

    document = """
    plant = "Wrexham"
    [line.export.terminal.A-01]
    position = 1
    [line.sample.terminal.A-01]
    position = 1
    """
    parsed = parse_roster(tomllib.loads(document), {"A-01": profile("A-01")})
    assert len(parsed) == 2
    assert check_one_line_each(parsed)


def test_a_gap_in_the_positions_is_a_warning():
    found = check_positions(
        roster(entry("A-01", position=1), entry("A-02", position=2), entry("A-03", position=5))
    )
    assert len(found) == 1
    assert found[0].severity is Severity.WARNING
    assert found[0].context["missing"] == [3, 4]


def test_positions_that_run_straight_through_are_fine():
    assert check_positions(
        roster(entry("A-01", position=1), entry("A-02", position=2), entry("A-03", position=3))
    ) == []


def test_a_line_that_does_not_start_at_one_says_so():
    found = check_positions(roster(entry("A-02", position=2), entry("A-03", position=3)))
    assert len(found) == 1
    assert found[0].context["first"] == 2
    assert found[0].context["terminal"] == "A-02"


def test_a_line_starting_late_with_a_gap_reports_both():
    found = check_positions(roster(entry("A-02", position=2), entry("A-04", position=4)))
    assert len(found) == 2


def test_one_machine_on_a_line_at_position_one_is_fine():
    assert check_positions(roster(entry("A-01", position=1))) == []


def test_each_line_is_judged_on_its_own():
    found = check_positions(
        roster(
            entry("A-01", "export", 1),
            entry("A-03", "export", 3),
            entry("B-01", "sample", 1),
            entry("B-02", "sample", 2),
        )
    )
    assert subjects(found) == ["export"]


def test_a_profile_no_line_claims_is_a_warning():
    known = {"A-01": profile("A-01"), "SPARE-09": profile("SPARE-09")}
    found = check_unclaimed_profiles(roster(entry("A-01")), known)
    assert subjects(found) == ["SPARE-09"]
    assert found[0].severity is Severity.WARNING


def test_every_profile_claimed_reports_nothing():
    assert check_unclaimed_profiles(roster(entry("A-01")), {"A-01": profile("A-01")}) == []


def test_a_profile_that_names_a_different_terminal_is_an_error():
    found = check_profile_identity(roster(entry("A-01", profile_for=profile("A-99"))))
    assert len(found) == 1
    assert found[0].severity is Severity.ERROR
    assert found[0].context["profile"] == "A-99"


def test_a_line_mixing_fpga_revisions_is_a_warning():
    found = check_line_hardware(
        roster(
            entry("A-01", position=1),
            entry("A-02", position=2, profile_for=profile("A-02", fpga_revision="PCDC-1.2")),
        )
    )
    assert len(found) == 1
    assert found[0].context["found"] == ["PCDC-1.2", "PCDC-1.4"]


def test_a_line_of_one_hardware_kind_is_quiet():
    assert check_line_hardware(roster(entry("A-01"), entry("A-02", position=2))) == []


def test_a_line_mixing_machines_says_so_separately():
    found = check_line_hardware(
        roster(
            entry("A-01", position=1),
            entry(
                "A-02",
                position=2,
                profile_for=profile("A-02", machine=TerminalModel.SJET_600_TIJ),
            ),
        )
    )
    assert len(found) == 1
    assert "machine" in found[0].message


def test_a_date_in_the_future_is_an_error():
    found = check_dates(roster(entry("A-01", commissioned=date(2025, 12, 1))), today=TODAY)
    assert len(found) == 1
    assert found[0].severity is Severity.ERROR


def test_today_itself_is_not_the_future():
    assert check_dates(roster(entry("A-01", commissioned=TODAY)), today=TODAY) == []


def test_a_date_older_than_two_years_is_a_warning():
    old = TODAY - STALE_AFTER - timedelta(days=1)
    found = check_dates(roster(entry("A-01", commissioned=old)), today=TODAY)
    assert len(found) == 1
    assert found[0].severity is Severity.WARNING
    assert found[0].context["days"] == (STALE_AFTER + timedelta(days=1)).days


def test_a_date_exactly_two_years_old_is_not_stale_yet():
    assert check_dates(roster(entry("A-01", commissioned=TODAY - STALE_AFTER)), today=TODAY) == []


def test_a_terminal_nobody_has_commissioned_has_no_date_to_judge():
    assert check_dates(roster(entry("A-01")), today=TODAY) == []


def test_a_long_critical_line_needs_more_than_one_window():
    found = check_critical_lines(
        roster(
            entry("A-01", position=1, criticality=Criticality.CRITICAL),
            entry("A-02", position=2, criticality=Criticality.CRITICAL),
            entry("A-03", position=3, criticality=Criticality.CRITICAL),
        )
    )
    assert len(found) == 1
    assert found[0].severity is Severity.INFO
    assert found[0].context["windows"] == 3


def test_a_normal_line_inside_its_allowance_says_nothing():
    assert check_critical_lines(
        roster(entry("A-01", position=1), entry("A-02", position=2))
    ) == []


def test_the_window_count_rounds_up():
    found = check_critical_lines(
        roster(*[entry(f"A-0{n}", position=n) for n in range(1, 6)])
    )
    assert found[0].context["at_once"] == 3
    assert found[0].context["windows"] == 2


def test_the_audit_runs_every_check():
    known = {"A-01": profile("A-01"), "SPARE-09": profile("SPARE-09")}
    found = audit_roster(
        roster(
            entry("A-01", position=1, commissioned=date(2025, 12, 1)),
            entry("A-03", position=3),
        ),
        known,
        today=TODAY,
    )
    assert rules(found) == {"roster"}
    messages = " ".join(finding.message for finding in found)
    assert "future" in messages
    assert "gaps" in messages
    assert "no line claims" in messages


def test_the_audit_skips_the_profile_check_when_it_has_no_profiles():
    found = audit_roster(roster(entry("A-01")), None, today=TODAY)
    assert all("no line claims" not in finding.message for finding in found)


def test_a_clean_roster_audits_clean():
    known = {"A-01": profile("A-01"), "A-02": profile("A-02")}
    found = audit_roster(
        roster(entry("A-01", position=1), entry("A-02", position=2)), known, today=TODAY
    )
    assert found == []


def test_the_audit_uses_todays_date_when_it_is_given_none():
    """The default has to be the clock, or the stale check never fires."""
    old = date.today() - STALE_AFTER - timedelta(days=30)
    found = audit_roster(roster(entry("A-01", commissioned=old)), None)
    assert len(found) == 1


def test_the_worst_line_is_the_one_with_the_most_wrong_with_it():
    bad = roster(
        entry("A-02", "export", 2),
        entry("A-04", "export", 4),
        entry("B-02", "sample", 2),
    )
    found = audit_roster(bad, None, today=TODAY)
    assert worst_line(bad, found) == "export"


def test_there_is_no_worst_line_when_nothing_is_wrong():
    clean = roster(entry("A-01", position=1), entry("A-02", position=2))
    assert worst_line(clean, audit_roster(clean, None, today=TODAY)) is None


def test_an_info_finding_does_not_make_a_line_the_worst():
    """Needing two windows is a fact about the line, not a fault in it."""
    long_line = roster(
        *[
            entry(f"A-0{n}", position=n, criticality=Criticality.CRITICAL)
            for n in range(1, 4)
        ]
    )
    found = audit_roster(long_line, None, today=TODAY)
    assert found
    assert worst_line(long_line, found) is None


def test_a_terminal_describes_itself_for_a_report():
    assert describe_entry(entry("A-01", "export", 2)) == "A-01 on export at position 2"


@pytest.mark.parametrize(
    "check",
    [
        check_one_line_each,
        check_profile_identity,
        check_positions,
        check_line_hardware,
        check_critical_lines,
    ],
)
def test_every_check_survives_an_empty_plant(check):
    assert check(roster()) == []


def test_the_date_check_survives_an_empty_plant():
    assert check_dates(roster(), today=TODAY) == []


def test_the_profile_check_survives_an_empty_plant():
    assert check_unclaimed_profiles(roster(), {}) == []
