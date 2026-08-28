"""What is wrong with a roster that reading it cannot tell you.

:mod:`stavewright.fleet.roster` refuses a document it cannot turn into a roster:
an unnamed plant, a terminal with no profile, two machines claiming one
position. Those are errors because there is no roster on the other side of
them.

Everything here is about a roster that parsed. The document is well formed and
still says something nobody meant, and the way you find out is a shift that
goes wrong. A machine listed on two lines. Positions 1, 2 and 5, which means
either three machines and a typo or four machines and one missing. A profile
sitting in the directory that no line claims, so it is never commissioned and
nobody notices for a year.

These are cross document checks. Each one needs the roster and something else:
the profiles beside it, the calendar, or the date. That is why they are not in
the parser, which sees one document.

Severity is about the shift. An error is something that makes the rollout plan
wrong, and the plan is what people work to. A warning is something that is
probably a mistake and is survivable if it is not.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta

from stavewright.finding import Finding, Severity, reporter
from stavewright.fleet.roster import Roster, RosterEntry
from stavewright.model.profile import TerminalProfile

RULE = "roster"
_finding = reporter(RULE)

STALE_AFTER = timedelta(days=730)
"""Two years. Not a hard rule anywhere, but a terminal nobody has touched in
two years is running a bundle whose build machine has probably been rebuilt,
and that is worth knowing before the window opens rather than during it."""


def check_one_line_each(roster: Roster) -> list[Finding]:
    """A terminal belongs to one line.

    The parser cannot catch this. Terminal ids are keys inside a line's own
    table, so the same id under two lines is two valid tables. It matters
    because every ordering question in this package is answered per line: a
    machine on two lines is upstream of itself and gets planned twice.
    """
    where: defaultdict[str, list[str]] = defaultdict(list)
    for entry in roster:
        where[entry.terminal_id].append(entry.line)
    return [
        _finding(
            Severity.ERROR,
            "this terminal is on more than one line",
            terminal_id,
            lines=sorted(lines),
        )
        for terminal_id, lines in sorted(where.items())
        if len(lines) > 1
    ]


def check_positions(roster: Roster) -> list[Finding]:
    """Positions on a line should run 1, 2, 3 with nothing missing.

    A gap is a machine that is on the line and not in the roster, and the whole
    point of the position is to say what feeds what. Ordering around a hole
    puts the bench on the wrong machine first.

    Starting at 2 is its own finding rather than a gap, because it is the
    common one: somebody counts from the machine they care about.
    """
    findings: list[Finding] = []
    for line in roster.lines:
        entries = roster.on_line(line)
        taken = [entry.position for entry in entries]
        if taken and taken[0] != 1:
            findings.append(
                _finding(
                    Severity.WARNING,
                    "the first machine on this line is not at position one",
                    line,
                    first=taken[0],
                    terminal=entries[0].terminal_id,
                )
            )
        missing = sorted(set(range(taken[0], taken[-1])) - set(taken)) if taken else []
        if missing:
            findings.append(
                _finding(
                    Severity.WARNING,
                    "this line has gaps in its positions, so a machine is missing",
                    line,
                    missing=missing,
                    machines=len(entries),
                )
            )
    return findings


def check_unclaimed_profiles(
    roster: Roster,
    profiles: dict[str, TerminalProfile],
) -> list[Finding]:
    """A profile the roster never names is a machine nobody will commission.

    This is the check that pays for the module. The roster is edited when a
    line changes and the profiles when a bundle changes, by different people,
    and a machine added to one and not the other is silent in both directions.
    """
    unclaimed = sorted(set(profiles) - {entry.terminal_id for entry in roster})
    if not unclaimed:
        return []
    return [
        _finding(
            Severity.WARNING,
            "this terminal has a profile but no line claims it",
            terminal_id,
            plant=roster.plant,
        )
        for terminal_id in unclaimed
    ]


def check_profile_identity(roster: Roster) -> list[Finding]:
    """The roster's key and the profile's own terminal id have to agree.

    They are looked up by the key, so a profile carrying a different id inside
    it installs under one name and is reported under another.
    """
    return [
        _finding(
            Severity.ERROR,
            "the profile in this slot names a different terminal",
            entry.terminal_id,
            profile=entry.profile.terminal_id,
            line=entry.line,
        )
        for entry in roster
        if entry.profile.terminal_id != entry.terminal_id
    ]


def check_line_hardware(roster: Roster) -> list[Finding]:
    """Machines on one line are usually the same machine.

    Not always, and this is a warning for that reason. A line running one
    terminal on an older FPGA revision is real, and it is also how a bundle
    that was fine on four machines fails on the fifth. Saying it once per line
    beats finding out per machine.
    """
    findings: list[Finding] = []
    for line in roster.lines:
        entries = roster.on_line(line)
        for what, values in (
            ("machine", [entry.profile.machine.value for entry in entries]),
            ("fpga_revision", [entry.profile.fpga_revision for entry in entries]),
        ):
            counted = Counter(value for value in values if value)
            if len(counted) > 1:
                findings.append(
                    _finding(
                        Severity.WARNING,
                        f"this line mixes more than one {what.replace('_', ' ')}",
                        line,
                        found=sorted(counted),
                        machines=len(entries),
                    )
                )
    return findings


def check_dates(roster: Roster, *, today: date) -> list[Finding]:
    """Commissioning dates that cannot be right, and ones that are too old."""
    findings: list[Finding] = []
    for entry in roster:
        when = entry.commissioned
        if when is None:
            continue
        if when > today:
            findings.append(
                _finding(
                    Severity.ERROR,
                    "this terminal was commissioned in the future",
                    entry.terminal_id,
                    commissioned=when.isoformat(),
                    today=today.isoformat(),
                )
            )
        elif today - when > STALE_AFTER:
            findings.append(
                _finding(
                    Severity.WARNING,
                    "nobody has commissioned this terminal in two years",
                    entry.terminal_id,
                    commissioned=when.isoformat(),
                    days=(today - when).days,
                )
            )
    return findings


def check_critical_lines(roster: Roster) -> list[Finding]:
    """A critical line takes one machine at a time, so its length is its cost.

    Nothing is wrong with a long critical line. What is wrong is finding out at
    the planning meeting that it needs six separate out of hours windows.
    """
    findings: list[Finding] = []
    for line in roster.lines:
        entries = roster.on_line(line)
        concurrent = min(entry.criticality.max_concurrent for entry in entries)
        if concurrent >= len(entries):
            continue
        findings.append(
            _finding(
                Severity.INFO,
                "this line needs more than one window because of its criticality",
                line,
                machines=len(entries),
                at_once=concurrent,
                windows=-(-len(entries) // concurrent),
            )
        )
    return findings


def audit_roster(
    roster: Roster,
    profiles: dict[str, TerminalProfile] | None = None,
    *,
    today: date | None = None,
) -> list[Finding]:
    """Every check, in the order somebody would want to read them.

    :param profiles: what was loaded from the profile directory. Without it the
        unclaimed profile check is skipped rather than reporting nothing, which
        is a different answer and the caller cannot tell them apart otherwise.
    :param today: the date to measure commissioning against. Injected because a
        test that depends on the wall clock passes until it does not.
    """
    findings = check_one_line_each(roster)
    findings.extend(check_profile_identity(roster))
    findings.extend(check_positions(roster))
    findings.extend(check_line_hardware(roster))
    findings.extend(check_dates(roster, today=today or date.today()))
    if profiles is not None:
        findings.extend(check_unclaimed_profiles(roster, profiles))
    findings.extend(check_critical_lines(roster))
    return findings


def worst_line(roster: Roster, findings: list[Finding]) -> str | None:
    """The line with the most to answer for, or None when there is nothing.

    Used for the report subtitle. A plant with forty machines and one bad line
    should say which line in the first sentence.
    """
    lines = set(roster.lines)
    counted = Counter(
        finding.subject
        for finding in findings
        if finding.subject in lines and finding.severity is not Severity.INFO
    )
    if not counted:
        return None
    return counted.most_common(1)[0][0]


def describe_entry(entry: RosterEntry) -> str:
    """One line about a terminal, for a report that is listing them."""
    return f"{entry.terminal_id} on {entry.line} at position {entry.position}"


__all__ = [
    "RULE",
    "STALE_AFTER",
    "audit_roster",
    "check_critical_lines",
    "check_dates",
    "check_line_hardware",
    "check_one_line_each",
    "check_positions",
    "check_profile_identity",
    "check_unclaimed_profiles",
    "describe_entry",
    "worst_line",
]
