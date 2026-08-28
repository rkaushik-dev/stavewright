"""Reports the quality group reads: GS1 data and print templates."""

from __future__ import annotations

from stavewright.finding import Finding
from stavewright.gs1.dataset import Dataset
from stavewright.report.document import (
    Report,
    findings_section,
    lines_section,
    table_section,
)
from stavewright.templates.pack import TemplateSet
from stavewright.templates.spf import SpfTemplate


def gs1_report(dataset: Dataset, findings: list[Finding]) -> Report:
    """A GS1 data file validation, as a document."""
    report = Report(
        title="GS1 data",
        subtitle=dataset.describe(),
        facts={
            "rows": len(dataset),
            "ragged": len(dataset.ragged_rows),
        },
    )
    report.add_if(findings_section("Findings", findings))
    if not findings:
        report.add(lines_section("Findings", ["nothing to report"]))
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


def template_set_report(templates: TemplateSet, findings: list[Finding]) -> Report:
    """A whole template directory, as a document."""
    longest = templates.longest_element_string()
    report = Report(
        title="Template set",
        subtitle=templates.describe(),
        facts={
            "templates": len(templates),
            "data_file": templates.data_path.name if templates.data_path else "",
            "rows": str(len(templates.dataset)) if templates.dataset else "0",
            "longest_element_string": str(longest) if longest is not None else "",
        },
    )
    by_template: dict[str, int] = {}
    for finding in findings:
        if finding.subject:
            by_template[finding.subject] = by_template.get(finding.subject, 0) + 1
    report.add(
        table_section(
            "Templates",
            ["template", "blocks", "bytes", "fonts", "findings"],
            [
                [
                    entry.name,
                    str(len(entry.template)),
                    str(entry.template.size),
                    ", ".join(sorted(entry.fonts)),
                    str(by_template.get(entry.name, 0)),
                ]
                for entry in templates
            ],
        )
    )
    if templates.fonts:
        report.add(
            lines_section(
                "Fonts the terminal needs",
                sorted(templates.fonts),
                note="Preflight checks these against what is installed.",
            )
        )
    report.add_if(findings_section("Findings", findings))
    report.derive_outcome()
    return report


__all__ = [
    "gs1_report",
    "template_report",
    "template_set_report",
]
