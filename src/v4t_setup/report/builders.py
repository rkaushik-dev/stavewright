"""Turning each subsystem's result into a report document.

One builder per thing a command produces. They live together rather than beside
the subsystem that produces them, because what a report looks like is a
presentation decision and keeping it out of the planner and the executor keeps
those two about their own job.

Every builder derives the outcome from the findings unless the result carries
one of its own. An apply that failed is a failure even when it produced no
findings, and a plan is never a failure by itself.
"""

from __future__ import annotations

from datetime import timedelta

from v4t_setup.apply.executor import ApplyResult
from v4t_setup.apply.executor import Outcome as ApplyOutcome
from v4t_setup.finding import Finding, Severity
from v4t_setup.fleet.roster import Roster
from v4t_setup.fleet.schedule import Schedule, Window
from v4t_setup.fleet.waves import Rollout
from v4t_setup.gs1.dataset import Dataset
from v4t_setup.plan.diff import Change, PlanDiff
from v4t_setup.plan.planner import PlanReport
from v4t_setup.plan.steps import Plan
from v4t_setup.preflight.runner import PreflightReport
from v4t_setup.report.document import (
    Outcome,
    Report,
    findings_section,
    lines_section,
    table_section,
)
from v4t_setup.stage.stager import StagingReport
from v4t_setup.templates.spf import SpfTemplate
from v4t_setup.verify.drift import DriftReport
from v4t_setup.verify.history import History

MAX_TABLE_ROWS = 200
"""Beyond this a text table stops being readable. JSON keeps everything."""


def _truncate(rows: list[list[str]], limit: int = MAX_TABLE_ROWS) -> tuple[list[list[str]], str]:
    if len(rows) <= limit:
        return rows, ""
    return rows[:limit], f"showing the first {limit} of {len(rows)} rows"


def plan_report(result: PlanReport) -> Report:
    """A commissioning plan, as a document."""
    plan = result.plan
    report = Report(
        title="Commissioning plan",
        subtitle=plan.profile.describe(),
        facts={
            "plan": str(plan.digest()),
            "steps": str(len(plan)),
            "terminal": plan.profile.terminal_id,
        },
    )
    rows = [
        [step.step_id, step.kind.value, step.target or "", "no" if not step.reversible else "yes"]
        for step in plan
    ]
    shown, note = _truncate(rows)
    report.add(table_section("Steps", ["id", "kind", "target", "reversible"], shown, note))

    if result.skipped:
        report.add(
            table_section(
                "Not applicable",
                ["item", "why"],
                [[item, reason] for item, reason in sorted(result.skipped.items())],
            )
        )
    if result.missing_requirements:
        report.add(
            table_section(
                "Unsatisfied requirements",
                ["item", "needs"],
                [
                    [item, ", ".join(tags)]
                    for item, tags in sorted(result.missing_requirements.items())
                ],
            )
        )
    irreversible = plan.irreversible_steps
    if irreversible:
        report.add(
            lines_section(
                "Cannot be rolled back",
                [f"{step.step_id}: {step.title}" for step in irreversible],
                note="If the run fails after these, the terminal needs a person.",
            )
        )
    report.outcome = Outcome.PASS
    return report


def preflight_report(result: PreflightReport, terminal: str = "") -> Report:
    """A preflight run, as a document."""
    report = Report(
        title="Preflight",
        subtitle=terminal,
        facts={"checks": str(len(result.ran))},
    )
    report.add_if(findings_section("Findings", result.findings))
    if result.waived:
        report.add(
            table_section(
                "Waived",
                ["check", "why"],
                [[name, reason] for name, reason in sorted(result.waived.items())],
            )
        )
    report.derive_outcome()
    return report


def apply_report(result: ApplyResult, plan: Plan) -> Report:
    """An apply run, as a document."""
    report = Report(
        title="Commissioning run",
        subtitle=plan.profile.describe(),
        facts={
            "run": result.run_id,
            "plan": str(plan.digest()),
            "outcome": result.outcome.value,
            "journal": str(result.journal_path) if result.journal_path else "",
        },
    )
    report.add(
        lines_section(
            "Summary",
            [
                f"{len(result.changed_steps)} steps changed the terminal",
                f"{len(result.skipped_steps)} steps were already in place",
            ],
        )
    )
    if result.changed_steps:
        report.add(
            table_section(
                "Changed",
                ["step", "kind"],
                [[step_id, plan.require(step_id).kind.value] for step_id in result.changed_steps],
            )
        )
    if result.failed_step:
        report.add(
            lines_section(
                "Failure",
                [f"{result.failed_step}: {result.failure}"],
            )
        )
    if result.rolled_back:
        report.add(lines_section("Rolled back", list(result.rolled_back)))
    if result.rollback_stopped_at:
        report.add(
            lines_section(
                "Rollback did not complete",
                [f"stopped at {result.rollback_stopped_at}"],
                note="The terminal is part commissioned and needs a person.",
            )
        )
    report.outcome = (
        Outcome.PASS if result.outcome is ApplyOutcome.COMPLETE else Outcome.FAIL
    )
    return report


def drift_report(result: DriftReport) -> Report:
    """A reconcile run, as a document."""
    report = Report(
        title="Reconcile",
        subtitle=result.terminal,
        facts={"plan": result.plan_digest, "checked": str(result.checked)},
    )
    findings = result.findings()
    report.add_if(findings_section("Drift", findings))
    if not findings:
        report.add(lines_section("Drift", ["the terminal matches its manifest"]))
    report.derive_outcome()
    return report


def stage_report(result: StagingReport) -> Report:
    """A staging run, as a document."""
    bundle = result.bundle
    report = Report(
        title="Staging",
        subtitle=bundle.describe(),
        facts={
            "root": str(bundle.root),
            "items": str(result.item_count),
            "bytes": str(bundle.total_bytes()),
        },
    )
    rows = [
        [entry.item_id, entry.kind, str(entry.size), entry.source]
        for entry in sorted(bundle.entries.values(), key=lambda e: e.item_id)
    ]
    shown, note = _truncate(rows)
    report.add(table_section("Staged", ["item", "kind", "bytes", "source"], shown, note))
    if result.skipped:
        report.add(
            table_section(
                "Nothing to stage",
                ["item", "why"],
                [[item, reason] for item, reason in sorted(result.skipped.items())],
            )
        )
    report.outcome = Outcome.PASS
    return report


def gs1_report(dataset: Dataset, findings: list[Finding]) -> Report:
    """A GS1 data file validation, as a document."""
    report = Report(
        title="GS1 data",
        subtitle=dataset.describe(),
        facts={
            "rows": str(len(dataset)),
            "ragged": str(len(dataset.ragged_rows)),
        },
    )
    report.add_if(findings_section("Findings", findings))
    if not findings:
        report.add(lines_section("Findings", ["nothing to report"]))
    report.derive_outcome()
    return report


def plan_diff_report(result: PlanDiff, crossed: list[str] | None = None) -> Report:
    """A comparison of two plans, as a document."""
    report = Report(
        title="Plan difference",
        facts={"before": result.before_digest, "after": result.after_digest},
    )
    if result.is_identical:
        report.add(lines_section("Difference", ["the two plans are the same"]))
    else:
        report.add(
            table_section(
                "Difference",
                ["step", "change", "before", "after", "fields"],
                [
                    [
                        entry.step_id,
                        entry.change.value,
                        "" if entry.before is None else str(entry.before),
                        "" if entry.after is None else str(entry.after),
                        ", ".join(entry.fields),
                    ]
                    for entry in result
                    if entry.change is not Change.SAME
                ],
            )
        )
    report.add(
        lines_section(
            "Effect",
            [
                "applying the new plan would change this terminal"
                if result.has_work
                else "nothing on this terminal would change"
            ],
        )
    )
    if crossed:
        report.add(
            lines_section(
                "Rollback changed",
                list(crossed),
                note=(
                    "These steps moved across a step that cannot be rolled back, "
                    "so a failure now leaves the terminal differently."
                ),
            )
        )
    report.outcome = Outcome.WARN if crossed else Outcome.PASS
    return report


def history_report(found: History, terminal: str | None = None) -> Report:
    """A commissioning history, as a document."""
    runs = found.for_terminal(terminal) if terminal else list(found)
    report = Report(
        title="Commissioning history",
        subtitle=terminal or f"{len(found.terminals)} terminals",
        facts={"runs": str(len(runs)), "journals": str(found.root or "")},
    )
    report.add(
        table_section(
            "Runs",
            ["started", "terminal", "run", "outcome", "steps"],
            [
                [
                    run.started_at.isoformat(sep=" ") if run.started_at else "",
                    run.terminal,
                    run.run_id,
                    run.outcome or ("unreadable" if not run.is_readable else "never finished"),
                    str(run.completed),
                ]
                for run in runs
            ],
        )
    )
    findings: list[Finding] = []
    for run in runs:
        if not run.is_readable:
            findings.append(
                Finding(
                    rule="journal/damaged",
                    severity=Severity.ERROR,
                    message=run.damaged,
                    subject=run.path.name,
                )
            )
        elif run.is_unfinished:
            findings.append(
                Finding(
                    rule="journal/unfinished",
                    severity=Severity.WARNING,
                    message="this run never wrote an end record",
                    subject=run.run_id or run.path.name,
                    context={"terminal": run.terminal, "completed": run.completed},
                )
            )
    report.add_if(findings_section("Findings", findings))
    report.derive_outcome()
    return report


def template_report(template: SpfTemplate, findings: list[Finding]) -> Report:
    """A template lint, as a document."""
    report = Report(
        title="Template",
        subtitle=template.describe(),
        facts={"blocks": str(len(template)), "bytes": str(template.size)},
    )
    report.add(
        table_section(
            "Blocks",
            ["kind", "instance", "offset", "bytes"],
            [
                [
                    block.kind,
                    "" if block.index is None else str(block.index),
                    str(block.offset),
                    str(block.size),
                ]
                for block in template
            ],
        )
    )
    report.add_if(findings_section("Findings", findings))
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
    "MAX_TABLE_ROWS",
    "apply_report",
    "drift_report",
    "gs1_report",
    "history_report",
    "plan_diff_report",
    "plan_report",
    "preflight_report",
    "rollout_report",
    "roster_report",
    "schedule_report",
    "stage_report",
    "template_report",
]
