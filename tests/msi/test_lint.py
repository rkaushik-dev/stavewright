from __future__ import annotations

from pathlib import Path

import pytest

from stavewright.finding import Severity
from stavewright.msi.cfb import read_compound_file
from stavewright.msi.lint import (
    RULE,
    check_directory_tree,
    check_media,
    check_properties,
    check_references,
    check_summary,
    lint_msi,
)
from stavewright.msi.summary import SummaryInformation
from stavewright.msi.tables import Column, MsiDatabase, StringPool, Table, read_database

MSI = Path(__file__).resolve().parents[2] / "msi-quattro" / "msi-Jet600.msi"

EMPTY_POOL = StringPool(codepage=1252, strings=("",), bytes_per_ref=2)

GOOD_PROPERTIES = {
    "ProductCode": "{62F57543-1C1F-434F-B14A-DFAE2CE509AB}",
    "ProductName": "S-Jet 600",
    "ProductVersion": "1.0.0",
    "ProductLanguage": "1033",
    "Manufacturer": "Sellenis",
    "UpgradeCode": "{73E3F36D-4DCA-4737-AF3A-6784EAFB3E68}",
}


def table(name: str, columns: list[str], rows: list[dict]) -> Table:
    return Table(
        name=name,
        columns=tuple(
            Column(number=index, name=column, type_word=0x0800 | 64)
            for index, column in enumerate(columns, start=1)
        ),
        rows=tuple(rows),
    )


def properties_table(**values: str) -> Table:
    return table(
        "Property",
        ["Property", "Value"],
        [{"Property": name, "Value": value} for name, value in values.items()],
    )


def database(**tables: Table) -> MsiDatabase:
    return MsiDatabase(pool=EMPTY_POOL, tables=dict(tables))


def messages(findings) -> str:
    return " | ".join(finding.message for finding in findings)


@pytest.fixture(scope="module")
def shipped():
    source = read_compound_file(MSI)
    return read_database(source), source


def test_the_shipped_installer_is_internally_consistent(shipped):
    from stavewright.msi.summary import read_summary

    db, source = shipped
    assert lint_msi(db, read_summary(source)) == []


def test_the_shipped_installer_passes_without_a_summary(shipped):
    db, _ = shipped
    assert lint_msi(db) == []


def test_a_good_property_table_produces_nothing():
    assert check_properties(database(Property=properties_table(**GOOD_PROPERTIES))) == []


@pytest.mark.parametrize(
    "missing", ["ProductCode", "ProductName", "ProductVersion", "ProductLanguage"]
)
def test_a_missing_required_property_is_an_error(missing):
    values = {name: value for name, value in GOOD_PROPERTIES.items() if name != missing}
    findings = check_properties(database(Property=properties_table(**values)))
    error = next(finding for finding in findings if finding.subject == missing)
    assert error.severity is Severity.ERROR
    assert "Windows Installer requires" in error.message


def test_a_missing_manufacturer_is_only_a_warning():
    values = {name: v for name, v in GOOD_PROPERTIES.items() if name != "Manufacturer"}
    findings = check_properties(database(Property=properties_table(**values)))
    warning = next(finding for finding in findings if finding.subject == "Manufacturer")
    assert warning.severity is Severity.WARNING


@pytest.mark.parametrize("name", ["ProductCode", "UpgradeCode"])
def test_a_code_that_is_not_a_guid(name):
    values = {**GOOD_PROPERTIES, name: "not-a-guid"}
    findings = check_properties(database(Property=properties_table(**values)))
    error = next(f for f in findings if f.subject == name and "GUID" in f.message)
    assert error.context["value"] == "not-a-guid"


def test_a_guid_without_braces_is_refused():
    values = {**GOOD_PROPERTIES, "ProductCode": "62F57543-1C1F-434F-B14A-DFAE2CE509AB"}
    assert "GUID in braces" in messages(
        check_properties(database(Property=properties_table(**values)))
    )


def test_a_version_windows_installer_will_not_take():
    values = {**GOOD_PROPERTIES, "ProductVersion": "1.0.0.0.0"}
    findings = check_properties(database(Property=properties_table(**values)))
    error = next(f for f in findings if f.subject == "ProductVersion")
    assert error.severity is Severity.ERROR
    assert error.context["value"] == "1.0.0.0.0"


def test_a_package_with_no_property_table_at_all():
    findings = check_properties(database())
    assert len(findings) == 5


def test_good_references_produce_nothing():
    db = database(
        Directory=table("Directory", ["Directory"], [{"Directory": "APPDIR"}]),
        Component=table(
            "Component",
            ["Component", "Directory_"],
            [{"Component": "main", "Directory_": "APPDIR"}],
        ),
        File=table("File", ["File", "Component_"], [{"File": "V4T.exe", "Component_": "main"}]),
    )
    assert check_references(db) == []


def test_a_component_pointing_at_a_missing_directory():
    db = database(
        Directory=table("Directory", ["Directory"], [{"Directory": "APPDIR"}]),
        Component=table(
            "Component",
            ["Component", "Directory_"],
            [{"Component": "main", "Directory_": "GONE"}],
        ),
    )
    finding = check_references(db)[0]
    assert finding.severity is Severity.ERROR
    assert finding.context["sample"] == ["GONE"]
    assert finding.context["target"] == "Directory"


def test_a_file_belonging_to_a_component_that_was_deleted():
    db = database(
        Component=table("Component", ["Component"], [{"Component": "main"}]),
        File=table("File", ["File", "Component_"], [{"File": "V4T.exe", "Component_": "old"}]),
    )
    assert "File.Component_ points at rows" in messages(check_references(db))


def test_a_dangling_feature_component_link():
    db = database(
        Feature=table("Feature", ["Feature"], [{"Feature": "Main"}]),
        Component=table("Component", ["Component"], [{"Component": "main"}]),
        FeatureComponents=table(
            "FeatureComponents",
            ["Feature_", "Component_"],
            [{"Feature_": "Gone", "Component_": "main"}],
        ),
    )
    assert "FeatureComponents.Feature_" in messages(check_references(db))


def test_a_table_that_is_not_there_is_not_a_reference_problem():
    assert check_references(database()) == []


def test_the_sample_is_capped():
    db = database(
        Directory=table("Directory", ["Directory"], [{"Directory": "APPDIR"}]),
        Component=table(
            "Component",
            ["Component", "Directory_"],
            [{"Component": f"c{n}", "Directory_": f"gone{n}"} for n in range(20)],
        ),
    )
    finding = check_references(db)[0]
    assert finding.context["count"] == 20
    assert len(finding.context["sample"]) == 5


def test_a_sound_directory_tree():
    db = database(
        Directory=table(
            "Directory",
            ["Directory", "Directory_Parent"],
            [
                {"Directory": "TARGETDIR", "Directory_Parent": None},
                {"Directory": "APPDIR", "Directory_Parent": "TARGETDIR"},
            ],
        )
    )
    assert check_directory_tree(db) == []


def test_a_package_with_no_directory_table():
    assert "no Directory table" in messages(check_directory_tree(database()))


def test_a_directory_table_with_no_root():
    db = database(
        Directory=table(
            "Directory",
            ["Directory", "Directory_Parent"],
            [{"Directory": "APPDIR", "Directory_Parent": "TARGETDIR"}],
        )
    )
    assert "no root row" in messages(check_directory_tree(db))


def test_a_directory_table_with_two_roots():
    db = database(
        Directory=table(
            "Directory",
            ["Directory", "Directory_Parent"],
            [
                {"Directory": "TARGETDIR", "Directory_Parent": None},
                {"Directory": "OTHER", "Directory_Parent": None},
            ],
        )
    )
    finding = next(f for f in check_directory_tree(db) if "more than one root" in f.message)
    assert finding.severity is Severity.WARNING
    assert finding.context["roots"] == ["OTHER", "TARGETDIR"]


def test_a_directory_loop_is_caught():
    db = database(
        Directory=table(
            "Directory",
            ["Directory", "Directory_Parent"],
            [
                {"Directory": "A", "Directory_Parent": "B"},
                {"Directory": "B", "Directory_Parent": "A"},
            ],
        )
    )
    finding = next(f for f in check_directory_tree(db) if "loop" in f.message)
    assert finding.severity is Severity.ERROR
    assert finding.context["sample"] == ["A", "B"]


def test_a_directory_that_is_its_own_parent():
    db = database(
        Directory=table(
            "Directory",
            ["Directory", "Directory_Parent"],
            [{"Directory": "A", "Directory_Parent": "A"}],
        )
    )
    assert "loop" in messages(check_directory_tree(db))


def test_a_package_with_no_files_needs_no_media():
    assert check_media(database()) == []


def test_files_with_no_media_table():
    db = database(File=table("File", ["File", "Sequence"], [{"File": "a", "Sequence": 1}]))
    assert "no Media table" in messages(check_media(db))


def test_files_past_the_last_media_sequence():
    db = database(
        File=table("File", ["File", "Sequence"], [{"File": "a", "Sequence": 9}]),
        Media=table("Media", ["DiskId", "LastSequence"], [{"DiskId": 1, "LastSequence": 3}]),
    )
    finding = check_media(db)[0]
    assert finding.context["highest_file"] == 9
    assert finding.context["highest_media"] == 3


def test_files_inside_the_media_sequence():
    db = database(
        File=table("File", ["File", "Sequence"], [{"File": "a", "Sequence": 3}]),
        Media=table("Media", ["DiskId", "LastSequence"], [{"DiskId": 1, "LastSequence": 3}]),
    )
    assert check_media(db) == []


def summary(*, name=None, maker=None, code=None, schema=None, flags=None) -> SummaryInformation:
    """A summary stream carrying only the fields these checks look at."""
    values = {2: name, 4: maker, 9: code, 14: schema, 15: flags}
    return SummaryInformation(properties={k: v for k, v in values.items() if v is not None})


def test_a_summary_that_agrees_with_the_table():
    db = database(Property=properties_table(**GOOD_PROPERTIES))
    entry = summary(name="S-Jet 600", maker="Sellenis", code=GOOD_PROPERTIES["ProductCode"])
    assert check_summary(db, entry) == []


def test_a_summary_that_disagrees_about_the_product_name():
    db = database(Property=properties_table(**GOOD_PROPERTIES))
    entry = summary(name="Something Else", code=GOOD_PROPERTIES["ProductCode"])
    finding = next(f for f in check_summary(db, entry) if f.subject == "ProductName")
    assert finding.severity is Severity.WARNING
    assert finding.context["summary"] == "Something Else"
    assert finding.context["table"] == "S-Jet 600"


def test_a_summary_that_disagrees_about_the_manufacturer():
    db = database(Property=properties_table(**GOOD_PROPERTIES))
    entry = summary(maker="Default Company Name", code=GOOD_PROPERTIES["ProductCode"])
    assert "disagree about the manufacturer" in messages(check_summary(db, entry))


def test_a_package_with_no_package_code():
    db = database(Property=properties_table(**GOOD_PROPERTIES))
    finding = next(f for f in check_summary(db, summary()) if f.subject == "PackageCode")
    assert finding.severity is Severity.ERROR


def test_a_package_code_that_is_not_a_guid():
    db = database(Property=properties_table(**GOOD_PROPERTIES))
    entry = summary(code="1234")
    assert "not a GUID" in messages(check_summary(db, entry))


def test_an_ancient_schema_version():
    db = database(Property=properties_table(**GOOD_PROPERTIES))
    entry = summary(code=GOOD_PROPERTIES["ProductCode"], schema=30)
    assert "older than anything still supported" in messages(check_summary(db, entry))


def test_an_administrative_image_is_flagged():
    db = database(Property=properties_table(**GOOD_PROPERTIES))
    entry = summary(code=GOOD_PROPERTIES["ProductCode"], flags=2)
    assert "administrative image" in messages(check_summary(db, entry))


def test_every_finding_carries_the_rule():
    db = database()
    assert all(finding.rule == RULE for finding in lint_msi(db))


def test_the_whole_lint_runs_every_check():
    db = database()
    assert len(lint_msi(db)) > 5


def test_the_summary_checks_are_skipped_without_one():
    db = database(Property=properties_table(**GOOD_PROPERTIES))
    assert not any(f.subject == "PackageCode" for f in lint_msi(db))
