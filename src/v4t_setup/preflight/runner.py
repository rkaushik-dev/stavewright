"""Running the checks and deciding whether a plan may proceed.

The runner is separate from the checks so that the decision, which is a policy
question, is not buried inside any one of them. A site that wants to commission
a terminal with a mixed language set can lower that warning's severity here
without editing a check.

An error stops the run. Nothing else does.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from v4t_setup.errors import PreflightError
from v4t_setup.finding import (
    Finding,
    Severity,
    count_by_severity,
    has_errors,
    sort_findings,
    worst_severity,
)
from v4t_setup.preflight.checks import DEFAULT_CHECKS, Check, PreflightContext
from v4t_setup.ruleset import describe_rules, run_rules
from v4t_setup.target.base import TargetSystem


@dataclass
class PreflightReport:
    """What preflight found."""

    findings: list[Finding] = field(default_factory=list)
    ran: list[str] = field(default_factory=list)
    waived: dict[str, str] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        """True when nothing found would stop the run."""
        return not has_errors(self.findings)

    @property
    def errors(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.is_error]

    @property
    def worst(self) -> Severity | None:
        return worst_severity(self.findings)

    def counts(self) -> dict[str, int]:
        return count_by_severity(self.findings)

    def lines(self) -> list[str]:
        """Operator readable summary, most serious first."""
        verdict = "clear to proceed" if self.passed else "blocked"
        counts = self.counts()
        summary = ", ".join(f"{count} {name}" for name, count in counts.items()) or "nothing found"
        result = [f"preflight {verdict}: {summary}"]
        result.extend(str(finding) for finding in sort_findings(self.findings))
        result.extend(f"waived {name}: {reason}" for name, reason in sorted(self.waived.items()))
        return result

    def raise_if_blocked(self) -> None:
        """Stop the run when an error was found.

        :raises PreflightError: naming every check that failed.
        """
        if self.passed:
            return
        raise PreflightError(
            "preflight found problems that stop this run",
            context={
                "checks": sorted({finding.rule for finding in self.errors}),
                "count": len(self.errors),
            },
        )


def run_preflight(
    context: PreflightContext,
    target: TargetSystem,
    *,
    checks: tuple[Check, ...] = DEFAULT_CHECKS,
    waive: dict[str, str] | None = None,
) -> PreflightReport:
    """Run *checks* and collect what they found.

    :param waive: check names to skip, mapped to why. A waiver is recorded in
        the report so it appears in the commissioning log. Waiving a check is a
        decision somebody made and it should be visible afterwards.
    """
    waivers = waive or {}
    report = PreflightReport(waived=dict(waivers))
    report.ran = [check.name for check in checks if check.name not in waivers]
    report.findings = run_rules(checks, context, target, skip=set(waivers))
    return report


def describe_checks(checks: tuple[Check, ...] = DEFAULT_CHECKS) -> list[str]:
    """One line per check, for a help screen."""
    return describe_rules(checks)


__all__ = ["PreflightReport", "describe_checks", "run_preflight"]
