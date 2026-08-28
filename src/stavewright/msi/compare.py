"""Does this MSI come from that project file.

The question a release engineer asks and nothing here could answer. The build
server produces an MSI, somebody copies it to a share, and six months later
there are three of them with similar names and one project file.

Comparison is by identity and by table membership, not byte for byte. An MSI
built twice from the same AIP differs in its package code, its cabinet, its
sequence numbers and its timestamps, so a digest tells you nothing you wanted
to know. What holds across two builds of the same project is the upgrade code,
the product name and version, and which directories, components, features,
files and registry rows exist.

Membership is reported by count with a sample, never row by row. The shipped
installer carries 539 directory rows against the project file's 38, because the
merge modules bring their own tree, and printing five hundred lines of that
buries the one line that matters.
"""

from __future__ import annotations

from dataclasses import dataclass

from stavewright.aip.normalize import NormalisedProject
from stavewright.finding import Finding, Severity, reporter
from stavewright.msi.tables import MsiDatabase

RULE = "msi-vs-aip"
_finding = reporter(RULE)

SAMPLE_SIZE = 5
"""How many names to name when a set does not match. Enough to recognise the
shape of the difference, few enough to read."""

IDENTITY = (
    ("ProductName", "product_name"),
    ("ProductVersion", "product_version"),
    ("UpgradeCode", "upgrade_code"),
    ("Manufacturer", "manufacturer"),
)


@dataclass(frozen=True, slots=True)
class SetDifference:
    """What one table has that the other does not."""

    what: str
    in_project: int
    in_msi: int
    missing: tuple[str, ...] = ()
    extra: tuple[str, ...] = ()

    @property
    def matches(self) -> bool:
        return not self.missing and not self.extra

    def describe(self) -> str:
        return (
            f"{self.what}: {self.in_project} in the project, {self.in_msi} in the MSI, "
            f"{len(self.missing)} missing, {len(self.extra)} extra"
        )


def _sample(names: set[str]) -> tuple[str, ...]:
    return tuple(sorted(names)[:SAMPLE_SIZE])


def compare_set(
    what: str,
    project_names: set[str],
    msi_names: set[str],
) -> SetDifference:
    """Compare two sets of keys from the same table."""
    return SetDifference(
        what=what,
        in_project=len(project_names),
        in_msi=len(msi_names),
        missing=_sample(project_names - msi_names),
        extra=_sample(msi_names - project_names),
    )


def _column(database: MsiDatabase, table: str, column: str) -> set[str]:
    if table not in database:
        return set()
    return {value for value in database.get(table).column(column) if isinstance(value, str)}


def compare_identity(project: NormalisedProject, database: MsiDatabase) -> list[Finding]:
    """Check the four properties that survive a rebuild.

    The upgrade code is the one that decides it. Two installers with the same
    upgrade code are the same product and one will upgrade the other, whatever
    else differs. Two with different upgrade codes install side by side and
    neither knows about the other, which is what the property is for.
    """
    findings: list[Finding] = []
    for property_name, attribute in IDENTITY:
        # str() because product_version is an MsiVersion and the MSI stores it
        # as text. Comparing the two directly is always unequal and looks like
        # a real mismatch, which cost an afternoon.
        expected = str(getattr(project, attribute) or "")
        found = database.property(property_name) or ""
        if found == expected:
            continue
        findings.append(
            _finding(
                Severity.ERROR if property_name == "UpgradeCode" else Severity.WARNING,
                f"{property_name} does not match the project file",
                property_name,
                project=expected,
                msi=found,
            )
        )
    return findings


def compare_tables(project: NormalisedProject, database: MsiDatabase) -> list[SetDifference]:
    """Compare every table the project file has an opinion about."""
    return [
        compare_set(
            "Directory",
            set(project.directories.keys()),
            _column(database, "Directory", "Directory"),
        ),
        compare_set(
            "Component",
            {component.component for component in project.components},
            _column(database, "Component", "Component"),
        ),
        compare_set(
            "Feature",
            set(project.features.features),
            _column(database, "Feature", "Feature"),
        ),
        compare_set(
            "File",
            {entry.file for entry in project.files},
            _column(database, "File", "File"),
        ),
        compare_set(
            "Registry",
            {entry.registry for entry in project.registry},
            _column(database, "Registry", "Registry"),
        ),
        compare_set(
            "Upgrade",
            {row.upgrade_code for row in project.upgrades},
            _column(database, "Upgrade", "UpgradeCode"),
        ),
    ]


def compare_project(project: NormalisedProject, database: MsiDatabase) -> list[Finding]:
    """Everything that says this MSI was or was not built from this project.

    A row the project declares and the MSI does not have is an error: something
    was dropped. A row the MSI has and the project does not is a warning,
    because merge modules legitimately bring their own and that is most of the
    difference in any real installer.
    """
    findings = compare_identity(project, database)
    for difference in compare_tables(project, database):
        if difference.missing:
            findings.append(
                _finding(
                    Severity.ERROR,
                    "the MSI is missing rows the project file declares in "
                    f"{difference.what}",
                    difference.what,
                    project=difference.in_project,
                    msi=difference.in_msi,
                    sample=list(difference.missing),
                )
            )
        if difference.extra:
            findings.append(
                _finding(
                    Severity.WARNING,
                    "the MSI carries rows the project file does not declare in "
                    f"{difference.what}",
                    difference.what,
                    project=difference.in_project,
                    msi=difference.in_msi,
                    sample=list(difference.extra),
                )
            )
    return findings


def is_same_product(project: NormalisedProject, database: MsiDatabase) -> bool:
    """The one question that can be answered yes or no.

    Same upgrade code means the same product. Everything else in a comparison
    is a matter of degree.
    """
    return database.property("UpgradeCode") == project.upgrade_code


__all__ = [
    "IDENTITY",
    "RULE",
    "SAMPLE_SIZE",
    "SetDifference",
    "compare_identity",
    "compare_project",
    "compare_set",
    "compare_tables",
    "is_same_product",
]
