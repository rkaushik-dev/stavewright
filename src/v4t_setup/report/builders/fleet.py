"""Reports about a plant rather than a machine: roster, waves, schedule."""

from __future__ import annotations

from datetime import timedelta

from v4t_setup.finding import Finding, Severity
from v4t_setup.fleet.roster import Roster
from v4t_setup.fleet.schedule import Schedule, Window
from v4t_setup.fleet.status import PlantStatus, Standing
from v4t_setup.fleet.waves import Rollout
from v4t_setup.report.document import (
    Report,
    findings_section,
    lines_section,
    table_section,
)


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
            "terminals": str(len(status)),
            "outstanding": str(len(status.outstanding)),
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
                    item.last_run_at.isoformat(sep=" ") if item.last_run_at else "",
                    item.note,
                ]
                for item in status
            ],
        )
    )
    findings = [
        Finding(
            rule="standing",
            severity=(
                Severity.ERROR if item.standing.blocks_others else Severity.WARNING
            ),
            message=item.note or f"this terminal is {item.standing.value}",
            subject=item.terminal_id,
            context={"standing": item.standing.value, "line": item.entry.line},
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
            "waves": str(len(rollout)),
            "terminals": str(len(machines)),
            "stoppages": str(rollout.stoppages),
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
    findings = [
        Finding(rule="rollout", severity=Severity.ERROR, message=problem)
        for problem in problems or []
    ]
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
            "booked": str(len(schedule)),
            "unplaced": str(len(schedule.unplaced)),
            "working_time": _hours(schedule.working_time),
            "finishes": schedule.finishes_at.isoformat(sep=" ") if schedule.finishes_at else "",
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
                        window.start.isoformat(sep=" "),
                        window.end.isoformat(sep=" "),
                        _hours(window.duration),
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
                    booking.start.isoformat(sep=" "),
                    booking.end.isoformat(sep=" "),
                    booking.window.label or "(unnamed)",
                    ", ".join(booking.wave.terminal_ids),
                ]
                for booking in schedule
            ],
        )
    )
    findings = [
        Finding(
            rule="schedule/unplaced",
            severity=Severity.ERROR,
            message="this wave does not fit any offered window",
            subject=", ".join(wave.terminal_ids),
            context={"wave": wave.number, "machines": len(wave)},
        )
        for wave in schedule.unplaced
    ]
    report.add_if(findings_section("Findings", findings))
    report.derive_outcome()
    return report


def _hours(span: timedelta) -> str:
    return f"{span.total_seconds() / 3600:.1f}"


__all__ = [
    "rollout_report",
    "roster_report",
    "schedule_report",
    "status_report",
]
