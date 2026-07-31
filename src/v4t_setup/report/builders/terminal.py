"""Reports about one terminal: the plan, the run, and what drifted after."""

from __future__ import annotations

from v4t_setup.apply.executor import ApplyResult
from v4t_setup.apply.executor import Outcome as ApplyOutcome
from v4t_setup.finding import Finding, Severity
from v4t_setup.plan.cost import estimate_plan
from v4t_setup.plan.critical import bottlenecks, critical_path
from v4t_setup.plan.diff import Change, PlanDiff
from v4t_setup.plan.planner import PlanReport
from v4t_setup.plan.steps import Plan
from v4t_setup.preflight.bench import DEFAULT_BENCH_CHECKS
from v4t_setup.preflight.runner import PreflightReport
from v4t_setup.report import format as fmt
from v4t_setup.report.document import (
    Outcome,
    Report,
    findings_section,
    lines_section,
    table_section,
)
from v4t_setup.report.shared import truncate
from v4t_setup.stage.stager import StagingReport
from v4t_setup.verify.attest import Attestation
from v4t_setup.verify.drift import DriftReport
from v4t_setup.verify.history import History
from v4t_setup.verify.retention import RetentionPlan


def plan_report(result: PlanReport) -> Report:
    """A commissioning plan, as a document."""
    plan = result.plan
    estimate = estimate_plan(plan)
    report = Report(
        title="Commissioning plan",
        subtitle=plan.profile.describe(),
        facts={
            "plan": plan.digest(),
            "steps": len(plan),
            "terminal": plan.profile.terminal_id,
            "minutes": f"{estimate.minutes:.0f}",
            "estimate": estimate.describe(),
        },
    )
    rows = [
        [step.step_id, step.kind.value, step.target or "", "no" if not step.reversible else "yes"]
        for step in plan
    ]
    shown, note = truncate(rows)
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
    report.add(
        table_section(
            "Where the time goes",
            ["kind", "minutes"],
            [
                [kind, fmt.minutes(span)]
                for kind, span in sorted(
                    estimate.by_kind.items(), key=lambda pair: -pair[1].total_seconds()
                )
            ],
            note="Before the verify pass, which adds about a quarter.",
        )
    )
    path = critical_path(plan)
    if len(path):
        report.facts["critical_minutes"] = fmt.minutes(path.span, places=0)
        report.add(
            table_section(
                "The critical path",
                ["step", "seconds", "slack"],
                [
                    [
                        timing.step_id,
                        f"{timing.duration.total_seconds():.0f}",
                        f"{timing.slack.total_seconds():.0f}",
                    ]
                    for timing in bottlenecks(path, limit=10)
                ],
                note=(
                    f"{path.describe()}. Steps off this path could take longer "
                    "and change nothing."
                ),
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
            "plan": plan.digest(),
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


def attestation_report(
    attestation: Attestation,
    findings: list[Finding] | None = None,
) -> Report:
    """One terminal's commissioning record, as a document.

    The digest is a fact rather than a section, because the whole point of the
    document is that somebody can compare it against the copy in the audit file
    and this is the line they compare.
    """
    report = Report(
        title="Commissioning attestation",
        subtitle=attestation.describe(),
        facts={
            "terminal": attestation.terminal,
            "run": attestation.run_id,
            "plan": attestation.plan_digest,
            "outcome": attestation.outcome,
            "digest": attestation.digest(),
            "toolkit": attestation.toolkit_version,
        },
    )
    report.add(
        lines_section(
            "Record",
            [
                f"{attestation.steps_applied} steps changed the terminal",
                f"{attestation.steps_skipped} steps were already in place",
                f"started {attestation.started_at.isoformat(sep=' ')}"
                if attestation.started_at
                else "start time not recorded",
                f"finished {attestation.finished_at.isoformat(sep=' ')}"
                if attestation.finished_at
                else "finish time not recorded",
                {
                    True: "reconciled afterwards and matched",
                    False: f"reconciled afterwards and had {attestation.drift_records} problems",
                    None: "never reconciled afterwards",
                }[attestation.verified],
            ],
            note=attestation.notes,
        )
    )
    if attestation.changed_steps:
        report.add(
            table_section("Changed", ["step"], [[name] for name in attestation.changed_steps])
        )
    report.add_if(findings_section("Findings", findings or []))
    report.derive_outcome()
    return report


def bench_report(findings: list[Finding]) -> Report:
    """The bench checks, as a document.

    Its own builder rather than the preflight one because the two answer
    different questions, and a report headed Preflight that says nothing about
    a terminal is a report somebody misreads.
    """
    report = Report(
        title="Bench check",
        subtitle="the laptop and the visit, not the terminal",
        facts={"checks": str(len(DEFAULT_BENCH_CHECKS))},
    )
    report.add_if(findings_section("Findings", findings))
    if not findings:
        report.add(lines_section("Findings", ["the bench is ready"]))
    report.derive_outcome()
    return report


def retention_report(plan: RetentionPlan, *, applied: bool = False) -> Report:
    """What a journal clear out would remove, or did.

    Always passes. Deciding to keep an old journal is not a finding about
    anything, and a command that exits 1 because there is nothing to delete is
    a command somebody stops running.
    """
    counts = plan.counts()
    report = Report(
        title="Journal retention",
        subtitle="removed" if applied else "nothing removed yet",
        facts={
            "journals": len(plan),
            "keeping": len(plan.kept),
            "removing": len(plan.expired),
            **{name: str(count) for name, count in counts.items()},
        },
    )
    report.add(
        table_section(
            "Journals",
            ["journal", "terminal", "started", "fate", "why"],
            [
                [
                    decision.path.name,
                    decision.run.terminal,
                    fmt.moment(decision.run.started_at),
                    "keep" if decision.is_kept else "delete",
                    decision.keep.value,
                ]
                for decision in plan
            ],
        )
    )
    if plan.expired:
        report.add(
            lines_section(
                "Would be removed" if not applied else "Removed",
                [str(path) for path in plan.paths],
            )
        )
    report.outcome = Outcome.PASS
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
            "root": bundle.root,
            "items": result.item_count,
            "bytes": bundle.total_bytes(),
        },
    )
    rows = [
        [entry.item_id, entry.kind, str(entry.size), entry.source]
        for entry in sorted(bundle.entries.values(), key=lambda e: e.item_id)
    ]
    shown, note = truncate(rows)
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
                    fmt.moment(run.started_at),
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


__all__ = [
    "apply_report",
    "attestation_report",
    "bench_report",
    "drift_report",
    "history_report",
    "plan_diff_report",
    "plan_report",
    "preflight_report",
    "retention_report",
    "stage_report",
]
