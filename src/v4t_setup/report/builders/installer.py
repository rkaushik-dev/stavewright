"""Reports about a built MSI, and about whether it matches a project."""

from __future__ import annotations

from v4t_setup.finding import Finding
from v4t_setup.msi.compare import SetDifference
from v4t_setup.msi.difference import DatabaseDifference, summarise
from v4t_setup.msi.summary import SummaryInformation
from v4t_setup.msi.tables import MsiDatabase
from v4t_setup.report.document import (
    Outcome,
    Report,
    findings_section,
    lines_section,
    table_section,
)


def msi_report(
    database: MsiDatabase,
    summary: SummaryInformation | None = None,
    findings: list[Finding] | None = None,
) -> Report:
    """What is inside an installer, as a document."""
    facts: dict[str, object] = {
        "product": database.property("ProductName") or "",
        "version": database.property("ProductVersion") or "",
        "upgrade_code": database.property("UpgradeCode") or "",
        "manufacturer": database.property("Manufacturer") or "",
        "codepage": database.pool.codepage,
    }
    if summary is not None:
        facts["package_code"] = summary.package_code or ""
        facts["platform"] = summary.platform or ""
        facts["schema"] = summary.schema_version or ""
        facts["built_by"] = summary.creating_application or ""
        facts["saved"] = summary.saved_at
    report = Report(
        title="Installer database",
        subtitle=database.describe(),
        facts=facts,
    )
    report.add(
        table_section(
            "Tables",
            ["table", "rows", "columns", "keys"],
            [
                [
                    name,
                    str(len(database.get(name))),
                    str(len(database.get(name).columns)),
                    ", ".join(database.get(name).key_columns),
                ]
                for name in database.table_names
            ],
        )
    )
    report.add_if(findings_section("Findings", findings or []))
    report.derive_outcome()
    return report


def msi_difference_report(
    difference: DatabaseDifference,
    before: str = "",
    after: str = "",
) -> Report:
    """Two installers held up against each other, as a document.

    It always passes. A difference between two builds is what was asked for,
    not a finding, and a command that exits 1 because a release changed
    something is a command nobody puts in a pipeline.
    """
    report = Report(
        title="Installer difference",
        subtitle=f"{before} against {after}" if before or after else "",
        facts={
            "changed_tables": len(difference.changed_tables),
            "changed_rows": len(difference),
            "identical": "yes" if difference.is_identical else "no",
        },
    )
    if difference.is_identical:
        report.add(lines_section("Difference", ["these two hold the same tables"]))
    else:
        report.add(
            table_section(
                "Tables",
                ["table", "added", "removed", "changed"],
                [
                    [
                        table.name,
                        str(len(table.added)),
                        str(len(table.removed)),
                        str(len(table.changed)),
                    ]
                    for table in difference
                ],
            )
        )
        report.add(lines_section("What changed", summarise(difference)))

    if difference.expected_properties:
        report.add(
            table_section(
                "Expected to differ",
                ["property", "before", "after"],
                [
                    [name, values[0], values[1]]
                    for name, values in sorted(difference.expected_properties.items())
                ],
                note="These change on every build, which is what they are for.",
            )
        )
    report.outcome = Outcome.PASS
    return report


def msi_compare_report(
    database: MsiDatabase,
    differences: list[SetDifference],
    findings: list[Finding],
) -> Report:
    """An MSI held up against the project file it should have come from."""
    report = Report(
        title="Installer against project",
        subtitle=database.describe(),
        facts={
            "product": database.property("ProductName") or "",
            "upgrade_code": database.property("UpgradeCode") or "",
        },
    )
    report.add(
        table_section(
            "Tables",
            ["table", "in the project", "in the MSI", "missing", "extra"],
            [
                [
                    difference.what,
                    str(difference.in_project),
                    str(difference.in_msi),
                    ", ".join(difference.missing) or "-",
                    ", ".join(difference.extra) or "-",
                ]
                for difference in differences
            ],
            note="Missing and extra are samples, not the whole list.",
        )
    )
    report.add_if(findings_section("Findings", findings))
    report.derive_outcome()
    return report


__all__ = [
    "msi_compare_report",
    "msi_difference_report",
    "msi_report",
]
