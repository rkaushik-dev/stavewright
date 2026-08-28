"""The shape every report has before a renderer gets hold of it.

A document is a title, some facts about what was looked at, and a list of
sections. A section holds one of three things: lines of prose, a table, or
findings. Nothing else, on purpose. Every time a subsystem has wanted a fourth
kind it has turned out to be a table.

Documents carry an outcome so a renderer can colour a heading and an exit code
can be derived without anybody re-deriving it from the findings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from stavewright import clock
from stavewright.finding import Finding, Severity, count_by_severity, sort_findings, worst_severity


class Outcome(StrEnum):
    """What a report says overall."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"

    @property
    def exit_code(self) -> int:
        """What a command should exit with. Warnings do not fail a build."""
        return 1 if self is Outcome.FAIL else 0


class SectionKind(StrEnum):
    LINES = "lines"
    TABLE = "table"
    FINDINGS = "findings"


@dataclass
class Section:
    """One part of a report."""

    heading: str
    kind: SectionKind = SectionKind.LINES
    lines: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    note: str = ""

    @property
    def is_empty(self) -> bool:
        return not (self.lines or self.rows or self.findings)

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"heading": self.heading, "kind": self.kind.value}
        if self.note:
            payload["note"] = self.note
        match self.kind:
            case SectionKind.LINES:
                payload["lines"] = list(self.lines)
            case SectionKind.TABLE:
                payload["columns"] = list(self.columns)
                payload["rows"] = [list(row) for row in self.rows]
            case SectionKind.FINDINGS:
                payload["findings"] = [finding.as_dict() for finding in self.findings]
        return payload


def lines_section(heading: str, lines: list[str], note: str = "") -> Section:
    """A section of prose."""
    return Section(heading=heading, kind=SectionKind.LINES, lines=list(lines), note=note)


def table_section(
    heading: str,
    columns: list[str],
    rows: list[list[str]],
    note: str = "",
) -> Section:
    """A section of tabular data.

    :raises ValueError: when a row does not have as many cells as there are
        columns, which would silently truncate in every renderer.
    """
    for index, row in enumerate(rows):
        if len(row) != len(columns):
            raise ValueError(
                f"row {index} has {len(row)} cells and there are {len(columns)} columns"
            )
    return Section(
        heading=heading,
        kind=SectionKind.TABLE,
        columns=list(columns),
        rows=[list(row) for row in rows],
        note=note,
    )


def findings_section(heading: str, findings: list[Finding], note: str = "") -> Section:
    """A section of findings, most serious first."""
    return Section(
        heading=heading,
        kind=SectionKind.FINDINGS,
        findings=sort_findings(findings),
        note=note,
    )


def fact_text(value: object) -> str:
    """One fact as text.

    None becomes the empty string rather than the word None, because a fact
    nobody could establish reads as blank and reads as a bug the other way.
    A datetime is written with a space rather than a T, because these lines go
    in front of people.
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return str(value)


@dataclass
class Report:
    """A whole report."""

    title: str
    subtitle: str = ""
    outcome: Outcome = Outcome.PASS
    generated_at: datetime | None = None
    facts: dict[str, object] = field(default_factory=dict)
    """What was looked at. Values are whatever the builder had.

    They were strings, and every builder wrapped every value in ``str()``.
    Thirty of those, all identical, all noise, and one of them wrapped an
    ``MsiVersion`` where the string was not what a reader wanted.

    Rendering is where a value becomes text, because that is where the format
    is known. A None renders as the empty string, which is what a fact nobody
    could establish should look like.
    """

    sections: list[Section] = field(default_factory=list)

    def add(self, section: Section) -> Report:
        """Append a section and return self, so builders can chain."""
        self.sections.append(section)
        return self

    def add_if(self, section: Section) -> Report:
        """Append a section only when it has something in it."""
        if not section.is_empty:
            self.sections.append(section)
        return self

    @property
    def all_findings(self) -> list[Finding]:
        """Every finding across every section."""
        return [
            finding
            for section in self.sections
            if section.kind is SectionKind.FINDINGS
            for finding in section.findings
        ]

    def derive_outcome(self) -> Outcome:
        """Set the outcome from the findings and return it.

        An error is a failure, a warning is a warning, anything else passes.
        Called by the builders rather than by the renderers, so a caller can
        override the outcome when it knows better.
        """
        worst = worst_severity(self.all_findings)
        if worst is Severity.ERROR:
            self.outcome = Outcome.FAIL
        elif worst is Severity.WARNING:
            self.outcome = Outcome.WARN
        else:
            self.outcome = Outcome.PASS
        return self.outcome

    def counts(self) -> dict[str, int]:
        """Findings by severity across the whole report."""
        return count_by_severity(self.all_findings)

    def fact_lines(self) -> dict[str, str]:
        """The facts as text, sorted, which is what every renderer wants."""
        return {key: fact_text(self.facts[key]) for key in sorted(self.facts)}

    def stamp(self) -> Report:
        """Set ``generated_at`` to now. Returns self."""
        self.generated_at = clock.now()
        return self

    def as_dict(self) -> dict[str, Any]:
        """The serialised form the JSON renderer writes."""
        payload: dict[str, Any] = {
            "title": self.title,
            "outcome": self.outcome.value,
            "sections": [section.as_dict() for section in self.sections],
        }
        if self.subtitle:
            payload["subtitle"] = self.subtitle
        if self.generated_at is not None:
            payload["generated_at"] = self.generated_at.isoformat()
        if self.facts:
            payload["facts"] = self.fact_lines()
        counts = self.counts()
        if counts:
            payload["counts"] = counts
        return payload


__all__ = [
    "Outcome",
    "Report",
    "Section",
    "SectionKind",
    "fact_text",
    "findings_section",
    "lines_section",
    "table_section",
]
