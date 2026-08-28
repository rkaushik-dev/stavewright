"""Checks over one installer, with nothing to compare it against.

The ones that matter are all references. An MSI is a relational database with
no foreign keys enforced: a component can name a directory that is not in the
Directory table, a file can belong to a component that was deleted, and the
build succeeds. Windows finds out at install time, on the customer's line.

The other half is the four properties every package must carry, and the two
places the product identity is written down. Summary information and the
Property table can disagree, and when they do the installer believes the
Property table while Explorer, the file dialog and the support engineer looking
at the file all believe the summary.

These do not overlap with :mod:`stavewright.msi.compare`. That one answers
whether an MSI came from a project file. This one answers whether the MSI makes
sense at all, and it is the one worth running on an installer somebody sent you.
"""

from __future__ import annotations

import re

from stavewright.finding import Finding, Severity, reporter
from stavewright.msi.summary import SummaryInformation
from stavewright.msi.tables import MsiDatabase
from stavewright.util.msiver import MsiVersion

RULE = "msi"
_finding = reporter(RULE)

GUID = re.compile(r"^\{[0-9A-Fa-f]{8}-(?:[0-9A-Fa-f]{4}-){3}[0-9A-Fa-f]{12}\}$")

REQUIRED_PROPERTIES = ("ProductCode", "ProductName", "ProductVersion", "ProductLanguage")
"""What Windows Installer refuses a package without. Manufacturer is not on
this list, which surprises people, but a package without one installs."""

MINIMUM_SCHEMA = 100

REFERENCES = (
    # table, column, table it points at, its key column
    ("Component", "Directory_", "Directory", "Directory"),
    ("File", "Component_", "Component", "Component"),
    ("FeatureComponents", "Feature_", "Feature", "Feature"),
    ("FeatureComponents", "Component_", "Component", "Component"),
    ("Registry", "Component_", "Component", "Component"),
    ("Feature", "Directory_", "Directory", "Directory"),
    ("MsiAssembly", "Component_", "Component", "Component"),
    ("MsiAssemblyName", "Component_", "Component", "Component"),
)

SAMPLE_SIZE = 5


def _values(database: MsiDatabase, table: str, column: str) -> set[str]:
    if table not in database or column not in database.get(table).column_names:
        return set()
    return {value for value in database.get(table).column(column) if isinstance(value, str)}


def check_properties(database: MsiDatabase) -> list[Finding]:
    """The four a package must have, and the shape of the ones that have one."""
    findings: list[Finding] = []
    for name in REQUIRED_PROPERTIES:
        if not database.property(name):
            findings.append(
                _finding(
                    Severity.ERROR,
                    "the package is missing a property Windows Installer requires",
                    name,
                )
            )

    for name in ("ProductCode", "UpgradeCode"):
        value = database.property(name)
        if value and not GUID.match(value):
            findings.append(
                _finding(
                    Severity.ERROR,
                    "this property has to be a GUID in braces",
                    name,
                    value=value,
                )
            )

    version = database.property("ProductVersion")
    if version:
        try:
            MsiVersion.parse(version)
        except ValueError as exc:
            findings.append(
                _finding(
                    Severity.ERROR,
                    "ProductVersion is not a version Windows Installer accepts",
                    "ProductVersion",
                    value=version,
                    detail=str(exc),
                )
            )
    if not database.property("Manufacturer"):
        findings.append(
            _finding(
                Severity.WARNING,
                "the package has no Manufacturer, so Add or Remove Programs shows none",
                "Manufacturer",
            )
        )
    return findings


def check_references(database: MsiDatabase) -> list[Finding]:
    """Every foreign key in the tables this toolkit cares about.

    Nothing enforces these at build time. A component pointing at a directory
    that is not there is a 2727 at install time, on the line, at night.
    """
    findings: list[Finding] = []
    for table, column, target, key in REFERENCES:
        if table not in database or target not in database:
            continue
        referenced = _values(database, table, column)
        available = _values(database, target, key)
        dangling = sorted(referenced - available)
        if dangling:
            findings.append(
                _finding(
                    Severity.ERROR,
                    f"{table}.{column} points at rows the {target} table does not have",
                    table,
                    column=column,
                    target=target,
                    count=len(dangling),
                    sample=dangling[:SAMPLE_SIZE],
                )
            )
    return findings


def check_directory_tree(database: MsiDatabase) -> list[Finding]:
    """The Directory table has to be a tree with exactly one root."""
    if "Directory" not in database:
        return [_finding(Severity.ERROR, "the package has no Directory table", "Directory")]

    parents: dict[str, str | None] = {}
    for row in database.get("Directory"):
        name = row.get("Directory")
        parent = row.get("Directory_Parent")
        if isinstance(name, str):
            parents[name] = parent if isinstance(parent, str) else None

    findings: list[Finding] = []
    roots = sorted(name for name, parent in parents.items() if parent is None)
    if not roots:
        findings.append(
            _finding(Severity.ERROR, "the Directory table has no root row", "Directory")
        )
    elif len(roots) > 1:
        findings.append(
            _finding(
                Severity.WARNING,
                "the Directory table has more than one root",
                "Directory",
                roots=roots,
            )
        )

    looping: list[str] = []
    for name in parents:
        seen: set[str] = set()
        current: str | None = name
        while current is not None and current in parents:
            if current in seen:
                looping.append(name)
                break
            seen.add(current)
            current = parents[current]
    if looping:
        findings.append(
            _finding(
                Severity.ERROR,
                "the Directory table has rows whose parents form a loop",
                "Directory",
                count=len(looping),
                sample=sorted(looping)[:SAMPLE_SIZE],
            )
        )
    return findings


def check_media(database: MsiDatabase) -> list[Finding]:
    """A package with files needs a Media row covering their sequence numbers."""
    if "File" not in database or not len(database.get("File")):
        return []
    if "Media" not in database or not len(database.get("Media")):
        return [
            _finding(
                Severity.ERROR,
                "the package has files and no Media table to put them on",
                "Media",
            )
        ]
    sequences = [
        value for value in database.get("File").column("Sequence") if isinstance(value, int)
    ]
    last = [
        value for value in database.get("Media").column("LastSequence") if isinstance(value, int)
    ]
    if sequences and last and max(sequences) > max(last):
        return [
            _finding(
                Severity.ERROR,
                "files run past the last sequence any Media row covers",
                "Media",
                highest_file=max(sequences),
                highest_media=max(last),
            )
        ]
    return []


def check_summary(database: MsiDatabase, summary: SummaryInformation) -> list[Finding]:
    """The two places the identity is written down have to agree."""
    findings: list[Finding] = []
    pairs = (
        ("ProductName", summary.product_name, "the product name"),
        ("Manufacturer", summary.manufacturer, "the manufacturer"),
    )
    for property_name, from_summary, what in pairs:
        from_table = database.property(property_name)
        if from_summary and from_table and from_summary != from_table:
            findings.append(
                _finding(
                    Severity.WARNING,
                    f"the summary stream and the Property table disagree about {what}",
                    property_name,
                    summary=from_summary,
                    table=from_table,
                )
            )
    if not summary.package_code:
        findings.append(
            _finding(
                Severity.ERROR,
                "the package has no package code, so Windows cannot tell two builds apart",
                "PackageCode",
            )
        )
    elif not GUID.match(summary.package_code):
        findings.append(
            _finding(
                Severity.ERROR,
                "the package code is not a GUID in braces",
                "PackageCode",
                value=summary.package_code,
            )
        )
    if summary.schema_version is not None and summary.schema_version < MINIMUM_SCHEMA:
        findings.append(
            _finding(
                Severity.WARNING,
                "the schema version is older than anything still supported",
                "SchemaVersion",
                value=summary.schema_version,
            )
        )
    if summary.is_admin_image:
        findings.append(
            _finding(
                Severity.WARNING,
                "this is an administrative image rather than a package to install",
                "SourceFlags",
            )
        )
    return findings


def lint_msi(
    database: MsiDatabase,
    summary: SummaryInformation | None = None,
) -> list[Finding]:
    """Every check, in the order a reader wants them.

    :param summary: the summary information stream, when it is available. The
        identity checks are skipped without it rather than guessed at.
    """
    findings = check_properties(database)
    findings.extend(check_references(database))
    findings.extend(check_directory_tree(database))
    findings.extend(check_media(database))
    if summary is not None:
        findings.extend(check_summary(database, summary))
    return findings


__all__ = [
    "GUID",
    "MINIMUM_SCHEMA",
    "REFERENCES",
    "REQUIRED_PROPERTIES",
    "RULE",
    "SAMPLE_SIZE",
    "check_directory_tree",
    "check_media",
    "check_properties",
    "check_references",
    "check_summary",
    "lint_msi",
]
