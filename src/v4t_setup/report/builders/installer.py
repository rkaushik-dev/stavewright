"""Reports about a built MSI, and about whether it matches a project."""

from __future__ import annotations

from v4t_setup.finding import Finding
from v4t_setup.msi.compare import SetDifference
from v4t_setup.msi.tables import MsiDatabase
from v4t_setup.report.document import (
    Outcome,
    Report,
    findings_section,
    table_section,
)


def msi_report(database: MsiDatabase) -> Report:
    """What is inside an installer, as a document."""
    report = Report(
        title="Installer database",
        subtitle=database.describe(),
        facts={
            "product": database.property("ProductName") or "",
            "version": database.property("ProductVersion") or "",
            "upgrade_code": database.property("UpgradeCode") or "",
            "manufacturer": database.property("Manufacturer") or "",
            "codepage": str(database.pool.codepage),
        },
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
    "msi_report",
]
