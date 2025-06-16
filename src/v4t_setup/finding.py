"""One shape for everything this toolkit reports about.

There were three of these. The GS1 validator had one, the template linter took
a copy, and preflight was about to take a third. They had already drifted: only
one of them carried a line number, and the reporters had grown a branch per
flavour.

A finding is a rule name, a severity, a sentence, and enough structured context
to locate it. Where a subsystem needs a field the others do not, it goes in
``context`` rather than into the class.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Severity(StrEnum):
    """How badly a rule was broken.

    ``ERROR`` stops the thing it was checking. ``WARNING`` does not, but
    somebody should look. ``INFO`` is for something worth writing down.
    """

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"

    @property
    def rank(self) -> int:
        """Lower is more serious. For sorting a report."""
        return {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}[self]


SEVERITY_ORDER = (Severity.ERROR, Severity.WARNING, Severity.INFO)


@dataclass(frozen=True, slots=True)
class Finding:
    """One rule result.

    :param rule: the rule that produced it, in kebab case.
    :param severity: how badly.
    :param message: one sentence, lower case, no trailing stop.
    :param ai: the GS1 application identifier, when there is one.
    :param line: the line in a data file, when there is one.
    :param subject: what the finding is about, such as a step id or a path.
    :param context: anything else, rendered as a table by the reporters.
    """

    rule: str
    severity: Severity
    message: str
    ai: str | None = None
    line: int | None = None
    subject: str | None = None
    context: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        where = f" ({self.ai})" if self.ai else ""
        at = f" line {self.line}" if self.line is not None else ""
        about = f" [{self.subject}]" if self.subject else ""
        return f"[{self.severity.value}] {self.rule}{where}{at}{about}: {self.message}"

    @property
    def is_error(self) -> bool:
        return self.severity is Severity.ERROR

    def as_dict(self) -> dict[str, Any]:
        """For the JSON reporter."""
        payload: dict[str, Any] = {
            "rule": self.rule,
            "severity": self.severity.value,
            "message": self.message,
        }
        for name in ("ai", "line", "subject"):
            value = getattr(self, name)
            if value is not None:
                payload[name] = value
        if self.context:
            payload["context"] = self.context
        return payload


def worst_severity(findings: Iterable[Finding]) -> Severity | None:
    """The most serious severity present, or None when there are none."""
    seen = {finding.severity for finding in findings}
    for severity in SEVERITY_ORDER:
        if severity in seen:
            return severity
    return None


def has_errors(findings: Iterable[Finding]) -> bool:
    """True when anything would stop the thing being checked."""
    return any(finding.is_error for finding in findings)


def count_by_severity(findings: Iterable[Finding]) -> dict[str, int]:
    """How many of each severity, in severity order, zeroes left out."""
    counts: dict[Severity, int] = {}
    for finding in findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
    return {
        severity.value: counts[severity] for severity in SEVERITY_ORDER if severity in counts
    }


def count_by_rule(findings: Iterable[Finding]) -> dict[str, int]:
    """How many findings each rule produced, highest first then by name."""
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.rule] = counts.get(finding.rule, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def sort_findings(findings: Iterable[Finding]) -> list[Finding]:
    """Most serious first, then by line, then by rule.

    This is the order a report reads in, which is not the order the rules ran.
    """
    return sorted(
        findings,
        key=lambda f: (f.severity.rank, f.line if f.line is not None else -1, f.rule),
    )


def only(findings: Iterable[Finding], severity: Severity) -> list[Finding]:
    """The findings at one severity, in the order given."""
    return [finding for finding in findings if finding.severity is severity]


__all__ = [
    "SEVERITY_ORDER",
    "Finding",
    "Severity",
    "count_by_rule",
    "count_by_severity",
    "has_errors",
    "only",
    "sort_findings",
    "worst_severity",
]
