"""Turning a report document into something a person or a machine reads.

Four renderers. Text goes on the bench screen, JSON goes into the ticket, CSV
goes into a spreadsheet because that is what quality departments run on, and
JUnit goes to whatever is watching the build.

Every renderer returns a string rather than writing anywhere. Deciding where
output goes belongs to the CLI, and a renderer that prints cannot be tested
without capturing stdout.

The text renderer does not use rich's console directly either. It builds plain
text with a width, so the same output lands in a log file, a terminal and a
pasted email without escape sequences in the middle of it.
"""

from __future__ import annotations

import csv
import io
import json
from xml.etree import ElementTree

from v4t_setup.finding import Severity
from v4t_setup.report.document import Outcome, Report, Section, SectionKind

DEFAULT_WIDTH = 96
MIN_COLUMN_WIDTH = 3
SEVERITY_MARK = {Severity.ERROR: "!!", Severity.WARNING: " !", Severity.INFO: "  "}
OUTCOME_MARK = {Outcome.PASS: "ok", Outcome.WARN: "warn", Outcome.FAIL: "FAIL"}


def _rule(width: int, character: str = "-") -> str:
    return character * width


def _wrap(text: str, width: int, indent: str = "") -> list[str]:
    if not text:
        return [""]
    words = text.split()
    lines: list[str] = []
    current = indent
    for word in words:
        candidate = f"{current}{word} "
        if len(candidate.rstrip()) > width and current.strip():
            lines.append(current.rstrip())
            current = f"{indent}{word} "
        else:
            current = candidate
    if current.strip():
        lines.append(current.rstrip())
    return lines or [""]


def _table(columns: list[str], rows: list[list[str]], width: int) -> list[str]:
    if not columns:
        return []
    widths = [len(column) for column in columns]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    # Shrink evenly from the widest column until the whole thing fits.
    while sum(widths) + 2 * (len(widths) - 1) > width and max(widths) > MIN_COLUMN_WIDTH:
        widest = widths.index(max(widths))
        widths[widest] -= 1

    def render(cells: list[str]) -> str:
        parts = []
        for index, cell in enumerate(cells):
            limit = widths[index]
            text = cell if len(cell) <= limit else cell[: limit - 1] + "…"
            parts.append(text.ljust(limit))
        return "  ".join(parts).rstrip()

    lines = [render(columns), render(["-" * w for w in widths])]
    lines.extend(render(row) for row in rows)
    return lines


def _section_lines(section: Section, width: int) -> list[str]:
    lines = [section.heading, _rule(min(len(section.heading), width))]
    if section.note:
        lines.extend(_wrap(section.note, width))
        lines.append("")
    match section.kind:
        case SectionKind.LINES:
            lines.extend(section.lines)
        case SectionKind.TABLE:
            lines.extend(_table(section.columns, section.rows, width))
        case SectionKind.FINDINGS:
            for finding in section.findings:
                mark = SEVERITY_MARK[finding.severity]
                where = f" ({finding.ai})" if finding.ai else ""
                at = f" line {finding.line}" if finding.line is not None else ""
                about = f" {finding.subject}" if finding.subject else ""
                lines.append(f"{mark} {finding.rule}{where}{at}{about}")
                lines.extend(_wrap(finding.message, width, indent="     "))
                for key in sorted(finding.context):
                    lines.append(f"       {key}: {finding.context[key]}")
    lines.append("")
    return lines


def to_text(report: Report, *, width: int = DEFAULT_WIDTH) -> str:
    """Render for a screen or a log file. Plain text, no escape sequences."""
    lines = [f"{report.title} [{OUTCOME_MARK[report.outcome]}]"]
    if report.subtitle:
        lines.append(report.subtitle)
    lines.append(_rule(width, "="))
    for key in sorted(report.facts):
        lines.append(f"{key}: {report.facts[key]}")
    counts = report.counts()
    if counts:
        lines.append(", ".join(f"{count} {name}" for name, count in counts.items()))
    lines.append("")
    for section in report.sections:
        lines.extend(_section_lines(section, width))
    return "\n".join(lines).rstrip() + "\n"


def to_json(report: Report, *, indent: int = 2) -> str:
    """Render for a ticket or another program."""
    return json.dumps(report.as_dict(), sort_keys=True, indent=indent) + "\n"


def to_csv(report: Report) -> str:
    """Render every finding as a row.

    Only the findings. A spreadsheet of prose sections is not useful, and the
    quality department opens these to sort and filter, not to read.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["section", "severity", "rule", "subject", "ai", "line", "message"])
    for section in report.sections:
        if section.kind is not SectionKind.FINDINGS:
            continue
        for finding in section.findings:
            writer.writerow(
                [
                    section.heading,
                    finding.severity.value,
                    finding.rule,
                    finding.subject or "",
                    finding.ai or "",
                    "" if finding.line is None else str(finding.line),
                    finding.message,
                ]
            )
    return buffer.getvalue()


def to_junit(report: Report) -> str:
    """Render for a build server.

    One test case per finding, plus one passing case per findings section that
    produced nothing, so a green run still shows what was checked. A suite with
    no test cases at all reads as "nothing ran" on most build servers, which is
    not what a clean commissioning report means.
    """
    findings = report.all_findings
    errors = sum(1 for finding in findings if finding.severity is Severity.ERROR)
    suite = ElementTree.Element(
        "testsuite",
        {
            "name": report.title,
            "tests": str(max(len(findings), 1)),
            "failures": str(errors),
            "errors": "0",
            "skipped": "0",
        },
    )
    if report.generated_at is not None:
        suite.set("timestamp", report.generated_at.isoformat())

    if not findings:
        ElementTree.SubElement(
            suite,
            "testcase",
            {"classname": report.title, "name": report.subtitle or "checks"},
        )
    for section in report.sections:
        if section.kind is not SectionKind.FINDINGS:
            continue
        for finding in section.findings:
            case = ElementTree.SubElement(
                suite,
                "testcase",
                {"classname": f"{report.title}.{section.heading}", "name": finding.rule},
            )
            if finding.severity is Severity.ERROR:
                failure = ElementTree.SubElement(
                    case, "failure", {"message": finding.message, "type": finding.rule}
                )
                failure.text = str(finding)
            elif finding.severity is Severity.WARNING:
                out = ElementTree.SubElement(case, "system-out")
                out.text = str(finding)
    return ElementTree.tostring(suite, encoding="unicode") + "\n"


RENDERERS = {
    "text": to_text,
    "json": to_json,
    "csv": to_csv,
    "junit": to_junit,
}
"""The formats a command's ``--format`` flag accepts."""


def render(report: Report, fmt: str = "text") -> str:
    """Render *report* in the named format.

    :raises ValueError: on an unknown format name.
    """
    try:
        renderer = RENDERERS[fmt]
    except KeyError:
        raise ValueError(
            f"unknown report format {fmt!r}, expected one of {', '.join(sorted(RENDERERS))}"
        ) from None
    return renderer(report)  # type: ignore[operator]


__all__ = [
    "DEFAULT_WIDTH",
    "RENDERERS",
    "render",
    "to_csv",
    "to_json",
    "to_junit",
    "to_text",
]
