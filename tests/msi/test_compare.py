"""The shipped MSI against the shipped project file.

They do not match, and that is the interesting part. ``msi-Jet600.msi`` was
built from a different project: different upgrade code, different product name,
and the manufacturer still on the Advanced Installer default. It is a useful
artefact to test against precisely because a comparator that says two unrelated
installers match is worse than none.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from stavewright.aip.normalize import normalise
from stavewright.aip.reader import read_aip
from stavewright.finding import Severity
from stavewright.msi.compare import (
    RULE,
    SAMPLE_SIZE,
    compare_identity,
    compare_project,
    compare_set,
    compare_tables,
    is_same_product,
)
from stavewright.msi.tables import read_msi

REPO_ROOT = Path(__file__).resolve().parents[2]
AIP = REPO_ROOT / "setup.aip"
MSI = REPO_ROOT / "msi-quattro" / "msi-Jet600.msi"

AIP_UPGRADE_CODE = "{73E3F36D-4DCA-4737-AF3A-6784EAFB3E68}"
MSI_UPGRADE_CODE = "{7418C8BB-3B78-4FF6-AC4A-665158841F31}"


@pytest.fixture(scope="module")
def project():
    return normalise(read_aip(AIP))


@pytest.fixture(scope="module")
def database():
    return read_msi(MSI)


def by_subject(findings):
    return {finding.subject: finding for finding in findings}


def test_the_shipped_msi_is_not_the_shipped_project(project, database):
    assert not is_same_product(project, database)


def test_the_upgrade_codes_are_the_reason(project, database):
    assert project.upgrade_code == AIP_UPGRADE_CODE
    assert database.property("UpgradeCode") == MSI_UPGRADE_CODE


def test_the_upgrade_code_mismatch_is_an_error(project, database):
    finding = by_subject(compare_identity(project, database))["UpgradeCode"]
    assert finding.severity is Severity.ERROR
    assert finding.context["project"] == AIP_UPGRADE_CODE
    assert finding.context["msi"] == MSI_UPGRADE_CODE


def test_the_other_identity_mismatches_are_warnings(project, database):
    findings = by_subject(compare_identity(project, database))
    for name in ("ProductName", "Manufacturer"):
        assert findings[name].severity is Severity.WARNING


def test_the_product_names_differ(project, database):
    finding = by_subject(compare_identity(project, database))["ProductName"]
    assert finding.context["project"] == "S-Jet 600"
    assert finding.context["msi"] == "msi-Jet600"


def test_the_manufacturer_is_still_the_tool_default(project, database):
    finding = by_subject(compare_identity(project, database))["Manufacturer"]
    assert finding.context["msi"] == "Default Company Name"


def test_the_versions_agree_and_produce_no_finding(project, database):
    # The one identity property that does match. It compares as text because
    # the project side is an MsiVersion and comparing the two objects directly
    # is always unequal.
    assert str(project.product_version) == database.property("ProductVersion") == "1.0.0"
    assert "ProductVersion" not in by_subject(compare_identity(project, database))


def test_a_project_that_matches_itself_produces_nothing(project, database):
    pretend = replace(
        project,
        properties={
            **project.properties,
            "ProductName": "msi-Jet600",
            "Manufacturer": "Default Company Name",
            "UpgradeCode": MSI_UPGRADE_CODE,
        },
    )
    assert compare_identity(pretend, database) == []
    assert is_same_product(pretend, database)


def test_every_table_is_compared(project, database):
    compared = [difference.what for difference in compare_tables(project, database)]
    assert compared == ["Directory", "Component", "Feature", "File", "Registry", "Upgrade"]


def test_the_counts_are_the_real_ones(project, database):
    counts = {d.what: (d.in_project, d.in_msi) for d in compare_tables(project, database)}
    assert counts["Directory"] == (38, 539)
    assert counts["Component"] == (58, 372)
    assert counts["File"] == (35, 74)
    assert counts["Registry"] == (9, 364)


def test_no_table_matches(project, database):
    assert not any(d.matches for d in compare_tables(project, database))


def test_a_difference_describes_itself(project, database):
    difference = compare_tables(project, database)[0]
    assert difference.describe().startswith("Directory: 38 in the project, 539 in the MSI")


def test_a_sample_is_capped(project, database):
    for difference in compare_tables(project, database):
        assert len(difference.missing) <= SAMPLE_SIZE
        assert len(difference.extra) <= SAMPLE_SIZE


def test_the_sample_names_real_rows(project, database):
    directory = compare_tables(project, database)[0]
    assert "APPDIR" in directory.missing


def test_the_extra_sample_shows_the_merge_module_shape(project, database):
    component = compare_tables(project, database)[1]
    assert all("." in name for name in component.extra)


def test_missing_rows_are_errors_and_extra_rows_are_warnings(project, database):
    findings = [f for f in compare_project(project, database) if f.subject == "Directory"]
    severities = {f.severity for f in findings}
    assert severities == {Severity.ERROR, Severity.WARNING}


def test_every_finding_carries_the_rule(project, database):
    assert all(finding.rule == RULE for finding in compare_project(project, database))


def test_the_whole_comparison_produces_findings_for_every_table(project, database):
    subjects = {finding.subject for finding in compare_project(project, database)}
    for table in ("Directory", "Component", "Feature", "File", "Registry", "Upgrade"):
        assert table in subjects


def test_two_identical_sets_match():
    difference = compare_set("Thing", {"a", "b"}, {"a", "b"})
    assert difference.matches
    assert difference.describe() == "Thing: 2 in the project, 2 in the MSI, 0 missing, 0 extra"


def test_two_empty_sets_match():
    assert compare_set("Thing", set(), set()).matches


def test_a_missing_name_is_reported():
    difference = compare_set("Thing", {"a", "b"}, {"a"})
    assert difference.missing == ("b",)
    assert difference.extra == ()


def test_an_extra_name_is_reported():
    difference = compare_set("Thing", {"a"}, {"a", "b"})
    assert difference.extra == ("b",)
    assert difference.missing == ()


def test_the_sample_is_sorted():
    difference = compare_set("Thing", set(), {"c", "a", "b"})
    assert difference.extra == ("a", "b", "c")


def test_a_sample_stops_at_the_cap():
    names = {f"name{n:02d}" for n in range(20)}
    difference = compare_set("Thing", set(), names)
    assert difference.extra == ("name00", "name01", "name02", "name03", "name04")


def test_a_table_the_msi_does_not_have_reads_as_empty(project, database):
    from stavewright.msi.compare import _column

    assert _column(database, "NotATable", "Anything") == set()
