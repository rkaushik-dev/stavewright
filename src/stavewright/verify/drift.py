"""What it means for a terminal to have moved away from its manifest.

Drift is not one thing and treating it as one is how a report becomes useless.
A file that has been deleted, a file that has been replaced with a different
build, and a control that is present but no longer registered are three
different afternoons.

The severities here are about consequence rather than about how surprising the
difference is. A missing template is an error because the terminal will not
print. An unexpected extra file is information, because half the machines on a
line have somebody's diagnostic tool in the product directory and that has
never stopped anything.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum

from stavewright.finding import Finding, Severity, count_by_rule


class DriftKind(StrEnum):
    """How a terminal differs from what was applied to it."""

    MISSING = "missing"
    MODIFIED = "modified"
    EXTRA = "extra"
    UNREGISTERED = "unregistered"
    WRONG_VALUE = "wrong-value"
    WRONG_REVISION = "wrong-revision"
    NOT_APPLIED = "not-applied"
    UNCHECKABLE = "uncheckable"


DEFAULT_SEVERITY: dict[DriftKind, Severity] = {
    DriftKind.MISSING: Severity.ERROR,
    DriftKind.MODIFIED: Severity.ERROR,
    DriftKind.UNREGISTERED: Severity.ERROR,
    DriftKind.WRONG_VALUE: Severity.ERROR,
    DriftKind.WRONG_REVISION: Severity.ERROR,
    DriftKind.NOT_APPLIED: Severity.ERROR,
    DriftKind.EXTRA: Severity.INFO,
    DriftKind.UNCHECKABLE: Severity.WARNING,
}
"""What each kind means for whether the terminal will work.

``EXTRA`` is deliberately only information. Half the machines on a line have
somebody's diagnostic tool in the product directory.

``UNCHECKABLE`` is a warning rather than an error because it says the toolkit
could not tell, not that anything is wrong. Reporting a clean pass for
something that was never looked at would be worse.
"""


@dataclass(frozen=True, slots=True)
class DriftRecord:
    """One difference between a terminal and its manifest."""

    kind: DriftKind
    subject: str
    step_id: str | None = None
    expected: str = ""
    actual: str = ""
    severity: Severity | None = None
    detail: dict[str, object] = field(default_factory=dict)

    @property
    def effective_severity(self) -> Severity:
        """The severity, defaulted from the kind when none was given."""
        return self.severity or DEFAULT_SEVERITY[self.kind]

    @property
    def is_error(self) -> bool:
        return self.effective_severity is Severity.ERROR

    def message(self) -> str:
        """One sentence describing the difference."""
        match self.kind:
            case DriftKind.MISSING:
                return "expected on the terminal and is not there"
            case DriftKind.MODIFIED:
                return "is there but the content has changed"
            case DriftKind.EXTRA:
                return "is on the terminal and not in the manifest"
            case DriftKind.UNREGISTERED:
                return "is present but no longer registered"
            case DriftKind.WRONG_VALUE:
                return "holds a different value to the one that was written"
            case DriftKind.WRONG_REVISION:
                return "reports a different revision to the one that was programmed"
            case DriftKind.NOT_APPLIED:
                return "was never applied to this terminal"
            case _:
                return "could not be checked"

    def to_finding(self) -> Finding:
        """Render as a :class:`~stavewright.finding.Finding` for the reporters."""
        context: dict[str, object] = dict(self.detail)
        if self.expected:
            context["expected"] = self.expected
        if self.actual:
            context["actual"] = self.actual
        if self.step_id:
            context["step"] = self.step_id
        return Finding(
            rule=f"drift/{self.kind.value}",
            severity=self.effective_severity,
            message=self.message(),
            subject=self.subject,
            context=context,
        )

    def __str__(self) -> str:
        return f"[{self.effective_severity.value}] {self.subject}: {self.message()}"


@dataclass
class DriftReport:
    """Everything reconcile found on one terminal."""

    terminal: str = ""
    plan_digest: str = ""
    records: list[DriftRecord] = field(default_factory=list)
    checked: int = 0

    def __len__(self) -> int:
        return len(self.records)

    def __iter__(self) -> Iterator[DriftRecord]:
        return iter(self.records)

    @property
    def is_clean(self) -> bool:
        """True when nothing found would stop the terminal working."""
        return not any(record.is_error for record in self.records)

    @property
    def errors(self) -> list[DriftRecord]:
        return [record for record in self.records if record.is_error]

    def of_kind(self, kind: DriftKind) -> list[DriftRecord]:
        return [record for record in self.records if record.kind is kind]

    def counts(self) -> dict[str, int]:
        """How many records of each kind, in kind order."""
        counts: dict[str, int] = {}
        for record in self.records:
            counts[record.kind.value] = counts.get(record.kind.value, 0) + 1
        return {kind.value: counts[kind.value] for kind in DriftKind if kind.value in counts}

    def findings(self) -> list[Finding]:
        """Every record as a finding, most serious first."""
        from stavewright.finding import sort_findings

        return sort_findings(record.to_finding() for record in self.records)

    def rule_counts(self) -> dict[str, int]:
        return count_by_rule(self.findings())

    def lines(self) -> list[str]:
        """Operator readable summary."""
        verdict = "matches its manifest" if self.is_clean else "has drifted"
        summary = ", ".join(f"{count} {name}" for name, count in self.counts().items())
        head = f"{self.terminal or 'terminal'} {verdict}"
        if summary:
            head = f"{head}: {summary}"
        head = f"{head} ({self.checked} checked)"
        return [head, *(str(record) for record in self.records)]

    def as_rows(self) -> list[dict[str, str]]:
        """Flat rows, for the CSV reporter."""
        return [
            {
                "terminal": self.terminal,
                "kind": record.kind.value,
                "severity": record.effective_severity.value,
                "subject": record.subject,
                "step": record.step_id or "",
                "expected": record.expected,
                "actual": record.actual,
            }
            for record in self.records
        ]


__all__ = ["DEFAULT_SEVERITY", "DriftKind", "DriftRecord", "DriftReport"]
