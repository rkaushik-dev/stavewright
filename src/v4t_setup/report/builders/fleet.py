"""Reports about a plant rather than a machine: roster, waves, schedule."""

from __future__ import annotations

from v4t_setup.finding import Finding, Severity, reporter
from v4t_setup.fleet.roster import Roster
from v4t_setup.fleet.schedule import Schedule, Window
from v4t_setup.fleet.status import PlantStatus, Standing
from v4t_setup.fleet.sweep import Sweep
from v4t_setup.fleet.waves import Rollout
from v4t_setup.report import format as fmt
from v4t_setup.report.document import (
    Report,
    findings_section,
    lines_section,
    table_section,
)


def sweep_report(result: Sweep) -> Report:
    """A whole plant reconciled from snapshots, as a document.

    The unseen count is a fact rather than only a section, because it is the
    number that makes the other two mean anything. Three clean out of thirty
    reads very differently once you know twenty seven were never opened.
    """
    counts = result.counts()
    report = Report(
        title="Plant sweep",
        subtitle=result.plant,
        facts={
            "terminals": len(result),
            **counts,
            "snapshots_unmatched": len(result.unmatched),
        },
    )
    report.add(
        table_section(
            "Terminals",
            ["terminal", "line", "state", "differences", "snapshot"],
            [
                [
                    item.terminal_id,
                    item.entry.line,
                    "clean"
                    if item.is_clean
                    else ("drifted" if item.was_looked_at else "not looked at"),
                    str(len(item.drift)) if item.drift else "",
                    item.snapshot_path.name if item.snapshot_path else "",
                ]
                for item in result
            ],
        )
    )
    report.add_if(findings_section("Findings", result.findings()))
    if result.unseen:
        report.add(
            lines_section(
                "Nobody has looked at these",
                [item.terminal_id for item in result.unseen],
                note="A sweep says nothing about a terminal it has no snapshot of.",
            )
        )
    report.derive_outcome()
    return report


def status_report(status: PlantStatus) -> Report:
    """The roster read against the journals, as a document.

    A disputed terminal is a warning rather than an error. The two records
    disagreeing is a bookkeeping problem, not a machine problem, and putting it
    at the same level as a half commissioned terminal would bury the one that
    matters.
    """
    counts = status.counts()
    report = Report(
        title="Plant standing",
        subtitle=status.plant,
        facts={
            "terminals": len(status),
            "outstanding": len(status.outstanding),
            **{name: str(count) for name, count in counts.items()},
        },
    )
    report.add(
        table_section(
            "Terminals",
            ["terminal", "line", "standing", "last run", "note"],
            [
                [
                    item.terminal_id,
                    item.entry.line,
                    item.standing.value,
                    fmt.moment(item.last_run_at),
                    item.note,
                ]
                for item in status
            ],
        )
    )
    standing_of = reporter("standing")
    findings = [
        standing_of(
            Severity.ERROR if item.standing.blocks_others else Severity.WARNING,
            item.note or f"this terminal is {item.standing.value}",
            item.terminal_id,
            standing=item.standing.value,
            line=item.entry.line,
        )
        for item in status
        if item.standing in (Standing.UNFINISHED, Standing.DISPUTED)
    ]
    report.add_if(findings_section("Findings", findings))
    if status.outstanding:
        report.add(
            lines_section(
                "Still to do",
                status.outstanding,
                note="In the order the rollout planner would work them.",
            )
        )
    report.derive_outcome()
    return report


def roster_report(roster: Roster) -> Report:
    """A plant roster, as a document."""
    report = Report(
        title="Plant roster",
        subtitle=roster.plant,
        facts={"terminals": str(len(roster)), "lines": str(len(roster.lines))},
    )
    report.add(
        table_section(
            "Terminals",
            ["terminal", "line", "position", "criticality", "machine", "commissioned"],
            [
                [
                    entry.terminal_id,
                    entry.line,
                    str(entry.position),
                    entry.criticality.value,
                    entry.profile.machine.value,
                    entry.commissioned.isoformat() if entry.commissioned else "never",
                ]
                for entry in roster.in_working_order()
            ],
        )
    )
    never = roster.uncommissioned()
    if never:
        report.add(
            lines_section(
                "Never commissioned",
                [entry.terminal_id for entry in never],
                note="These have no run in the history and nothing to compare against.",
            )
        )
    report.derive_outcome()
    return report


def roster_audit_report(
    roster: Roster,
    findings: list[Finding],
    *,
    profiles_checked: bool = True,
) -> Report:
    """A roster audited against the profiles beside it, as a document.

    The subtitle names the worst line rather than the plant, because the plant
    is on the roster the reader already has open and the line is the thing they
    are about to go and look at.

    ``profiles_checked`` is in the facts on purpose. A clean audit that never
    opened the profile directory is a different clean from one that did, and a
    report that cannot tell you which is not evidence of anything.
    """
    from v4t_setup.fleet.audit import worst_line

    bad = worst_line(roster, findings)
    report = Report(
        title="Roster audit",
        subtitle=f"{roster.plant}, worst line {bad}" if bad else roster.plant,
        facts={
            "plant": roster.plant,
            "terminals": str(len(roster)),
            "lines": str(len(roster.lines)),
            "profiles_checked": "yes" if profiles_checked else "no",
        },
    )
    report.add(
        table_section(
            "Lines",
            ["line", "machines", "criticality", "shift", "findings"],
            [
                [
                    line,
                    str(len(roster.on_line(line))),
                    roster.on_line(line)[0].criticality.value,
                    roster.on_line(line)[0].shift_pattern.value,
                    str(sum(1 for finding in findings if finding.subject == line)),
                ]
                for line in roster.lines
            ],
        )
    )
    report.add_if(findings_section("Findings", findings))
    if not profiles_checked:
        report.add(
            lines_section(
                "Not checked",
                ["profiles nobody claims, because no profile directory was read"],
            )
        )
    report.derive_outcome()
    return report


def rollout_report(rollout: Rollout, problems: list[str] | None = None) -> Report:
    """A rollout plan, as a document.

    :param problems: what :func:`check_rollout` said. They arrive as findings
        rather than as a table, because a plan that does not hold is the one
        thing in this report somebody has to act on.
    """
    machines = rollout.terminal_ids
    report = Report(
        title="Rollout plan",
        subtitle=rollout.plant,
        facts={
            "waves": len(rollout),
            "terminals": len(machines),
            "stoppages": rollout.stoppages,
        },
    )
    report.add(
        table_section(
            "Waves",
            ["wave", "criticality", "terminals", "lines", "stoppage"],
            [
                [
                    str(wave.number),
                    wave.criticality.value,
                    ", ".join(wave.terminal_ids),
                    ", ".join(wave.lines),
                    "yes" if wave.needs_stoppage else "no",
                ]
                for wave in rollout
            ],
        )
    )
    problem_of = reporter("rollout")
    findings = [problem_of(Severity.ERROR, problem) for problem in problems or []]
    report.add_if(findings_section("Findings", findings))
    report.derive_outcome()
    return report


def schedule_report(schedule: Schedule, windows: list[Window] | None = None) -> Report:
    """A dated rollout, as a document.

    A wave that fits nowhere is an error rather than a note. The whole reason
    to date a rollout is to find out what the offered windows will not hold,
    and that answer is no use if it renders as a line somebody scrolls past.
    """
    report = Report(
        title="Rollout schedule",
        subtitle=schedule.plant,
        facts={
            "booked": len(schedule),
            "unplaced": len(schedule.unplaced),
            "working_time": fmt.hours(schedule.working_time),
            "finishes": fmt.moment(schedule.finishes_at),
        },
    )
    if windows:
        report.add(
            table_section(
                "Windows",
                ["window", "opens", "closes", "hours", "booked"],
                [
                    [
                        window.label or "(unnamed)",
                        fmt.moment(window.start),
                        fmt.moment(window.end),
                        fmt.hours(window.duration),
                        str(len(schedule.in_window(window.label))),
                    ]
                    for window in windows
                ],
            )
        )
    report.add(
        table_section(
            "Bookings",
            ["wave", "starts", "ends", "window", "terminals"],
            [
                [
                    str(booking.wave.number),
                    fmt.moment(booking.start),
                    fmt.moment(booking.end),
                    booking.window.label or "(unnamed)",
                    ", ".join(booking.wave.terminal_ids),
                ]
                for booking in schedule
            ],
        )
    )
    unplaced = reporter("schedule/unplaced")
    findings = [
        unplaced(
            Severity.ERROR,
            "this wave does not fit any offered window",
            ", ".join(wave.terminal_ids),
            wave=wave.number,
            machines=len(wave),
        )
        for wave in schedule.unplaced
    ]
    report.add_if(findings_section("Findings", findings))
    report.derive_outcome()
    return report


__all__ = [
    "rollout_report",
    "roster_report",
    "schedule_report",
    "status_report",
    "sweep_report",
]
