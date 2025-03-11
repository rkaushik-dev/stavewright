from __future__ import annotations

import pytest

from v4t_setup.aip.reader import AipRow, parse_aip, read_aip
from v4t_setup.errors import AipParseError, AipTableMissingError

MINIMAL = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<DOCUMENT Type="Advanced Installer" CreateVersion="12.2.1" version="12.6.1"
          Modules="professional" RootPath="." Language="en"
          Id="{CFADCC08-A325-4099-AD9A-08C87E146704}">
  <COMPONENT cid="caphyon.advinst.msicomp.MsiPropsComponent">
    <ROW Property="Manufacturer" Value="Sellenis"/>
    <ROW Property="ProductName" Value="S-Jet 600"/>
    <ROW Property="ProductVersion" Value="1.0.0" Type="32"/>
    <ROW Property="UpgradeCode" Value="{73E3F36D-4DCA-4737-AF3A-6784EAFB3E68}"/>
    <ROW Property="WindowsType9X" MultiBuildValue="Win32_Release:Windows 9x/ME" ValueLocId="-"/>
  </COMPONENT>
  <COMPONENT cid="caphyon.advinst.msicomp.MsiDirsComponent">
    <ROW Directory="TARGETDIR" DefaultDir="SourceDir"/>
    <ROW Directory="APPDIR" Directory_Parent="TARGETDIR" DefaultDir="APPDIR:." IsPseudoRoot="1"/>
    <ROW Directory="Quattro_Dir" Directory_Parent="Matricode_Dir" DefaultDir="S-jet600"/>
  </COMPONENT>
  <COMPONENT cid="caphyon.advinst.msicomp.MsiFeatsComponent">
    <ROW Feature="MainFeature" Title="MainFeature" Display="1" Level="1"
         Directory_="APPDIR" Attributes="0" Components="V4T.exe pcdc_top.rbf GS11.txt"/>
    <ATTRIBUTE name="CurrentFeature" value="MainFeature"/>
  </COMPONENT>
  <COMPONENT cid="caphyon.advinst.msicomp.MsiFilesComponent">
    <ROW File="GS11.txt" Component_="GS11.txt" FileName="GS1-1.txt" Attributes="0"
         SourcePath="Matricode\\Quattro\\Templates\\GS1-1.txt" SelfReg="false"/>
  </COMPONENT>
  <COMPONENT cid="caphyon.advinst.msicomp.BuildComponent">
    <ROW Name="Win32_Release" Type="1"/>
  </COMPONENT>
</DOCUMENT>
"""


@pytest.fixture
def project():
    return parse_aip(MINIMAL)


def test_document_attributes_are_kept(project):
    assert project.document["Type"] == "Advanced Installer"
    assert project.document["version"] == "12.6.1"
    assert project.document["Id"] == "{CFADCC08-A325-4099-AD9A-08C87E146704}"


def test_tables_are_addressable_by_alias(project):
    assert len(project.table("Property")) == 5
    assert len(project.table("Directory")) == 3


def test_tables_are_addressable_by_tail(project):
    assert project.table("MsiDirsComponent") is project.table("Directory")


def test_tables_are_addressable_by_full_cid(project):
    full = "caphyon.advinst.msicomp.MsiDirsComponent"
    assert project.table(full) is project.table("Directory")


def test_alias_is_reported_back(project):
    assert project.table("Directory").alias == "Directory"
    assert project.table("Directory").tail == "MsiDirsComponent"


def test_has_table(project):
    assert project.has_table("Property")
    assert not project.has_table("Shortcut")


def test_missing_table_raises_from_table(project):
    with pytest.raises(AipTableMissingError, match="no such table"):
        project.table("Shortcut")


def test_missing_table_is_empty_from_rows(project):
    assert project.rows("Shortcut") == ()


def test_table_names_are_sorted_tails(project):
    assert project.table_names == [
        "BuildComponent",
        "MsiDirsComponent",
        "MsiFeatsComponent",
        "MsiFilesComponent",
        "MsiPropsComponent",
    ]


def test_properties_flatten_to_a_mapping(project):
    props = project.properties
    assert props["Manufacturer"] == "Sellenis"
    assert props["ProductName"] == "S-Jet 600"
    assert props["UpgradeCode"] == "{73E3F36D-4DCA-4737-AF3A-6784EAFB3E68}"


def test_a_property_with_only_a_multi_build_value_flattens_to_empty(project):
    assert project.properties["WindowsType9X"] == ""


def test_build_names(project):
    assert project.build_names == ["Win32_Release"]


def test_component_level_attribute_elements_are_kept(project):
    assert project.table("Feature").attributes == {"CurrentFeature": "MainFeature"}


def test_rows_iterate(project):
    keys = [row.require("Directory") for row in project.table("Directory")]
    assert keys == ["TARGETDIR", "APPDIR", "Quattro_Dir"]


def test_find_by_column(project):
    row = project.table("Directory").find("Directory", "APPDIR")
    assert row is not None
    assert row.get("DefaultDir") == "APPDIR:."


def test_find_returns_none_when_absent(project):
    assert project.table("Directory").find("Directory", "Nope") is None


def test_index_by(project):
    indexed = project.table("Directory").index_by("Directory")
    assert set(indexed) == {"TARGETDIR", "APPDIR", "Quattro_Dir"}


def test_index_by_refuses_duplicate_keys():
    doc = parse_aip(
        '<DOCUMENT><COMPONENT cid="caphyon.advinst.msicomp.MsiDirsComponent">'
        '<ROW Directory="A" DefaultDir="A"/><ROW Directory="A" DefaultDir="B"/>'
        "</COMPONENT></DOCUMENT>"
    )
    with pytest.raises(AipParseError, match="two rows share a key"):
        doc.table("Directory").index_by("Directory")


def test_row_membership_and_get(project):
    row = project.table("Directory").find("Directory", "APPDIR")
    assert "IsPseudoRoot" in row
    assert "Nope" not in row
    assert row.get("Nope") is None
    assert row.get("Nope", "fallback") == "fallback"


def test_require_reports_the_missing_column(project):
    row = project.table("Directory").find("Directory", "TARGETDIR")
    with pytest.raises(AipParseError, match="missing a required column") as excinfo:
        row.require("Directory_Parent")
    assert excinfo.value.context["column"] == "Directory_Parent"
    assert excinfo.value.context["table"] == "MsiDirsComponent"


def test_get_int(project):
    row = project.table("Feature").find("Feature", "MainFeature")
    assert row.get_int("Level") == 1
    assert row.get_int("Attributes") == 0
    assert row.get_int("Nope") is None
    assert row.get_int("Nope", 7) == 7


def test_get_int_rejects_a_non_number():
    row = AipRow(values={"Level": "high"}, table="MsiFeatsComponent")
    with pytest.raises(AipParseError, match="not an integer"):
        row.get_int("Level")


def test_get_int_treats_a_blank_cell_as_absent():
    row = AipRow(values={"Level": "   "}, table="MsiFeatsComponent")
    assert row.get_int("Level", 3) == 3


@pytest.mark.parametrize(
    ("value", "expected"),
    [("true", True), ("True", True), ("yes", True), ("1", True),
     ("false", False), ("no", False), ("0", False), ("", False)],
)
def test_get_bool(value, expected):
    row = AipRow(values={"SelfReg": value}, table="MsiFilesComponent")
    assert row.get_bool("SelfReg") is expected


def test_get_bool_default(project):
    row = project.table("Directory").find("Directory", "TARGETDIR")
    assert row.get_bool("SelfReg") is False
    assert row.get_bool("SelfReg", default=True) is True


def test_get_bool_rejects_nonsense():
    row = AipRow(values={"SelfReg": "maybe"}, table="MsiFilesComponent")
    with pytest.raises(AipParseError, match="not a boolean"):
        row.get_bool("SelfReg")


def test_get_list_splits_the_packed_components_cell(project):
    row = project.table("Feature").find("Feature", "MainFeature")
    assert row.get_list("Components") == ["V4T.exe", "pcdc_top.rbf", "GS11.txt"]


def test_get_list_of_an_absent_column_is_empty(project):
    row = project.table("Feature").find("Feature", "MainFeature")
    assert row.get_list("Nope") == []


def test_get_list_collapses_runs_of_separators():
    row = AipRow(values={"Components": "a  b   c"}, table="MsiFeatsComponent")
    assert row.get_list("Components") == ["a", "b", "c"]


def test_bad_xml_is_reported():
    with pytest.raises(AipParseError, match="not well formed XML"):
        parse_aip("<DOCUMENT><COMPONENT></DOCUMENT>")


def test_wrong_root_element_is_reported():
    with pytest.raises(AipParseError, match="root element is not DOCUMENT"):
        parse_aip("<PROJECT/>")


def test_component_without_a_cid_is_reported():
    with pytest.raises(AipParseError, match="no cid attribute"):
        parse_aip("<DOCUMENT><COMPONENT/></DOCUMENT>")


def test_a_repeated_table_is_reported():
    with pytest.raises(AipParseError, match="same table twice"):
        parse_aip(
            "<DOCUMENT>"
            '<COMPONENT cid="caphyon.advinst.msicomp.MsiDirsComponent"/>'
            '<COMPONENT cid="caphyon.advinst.msicomp.MsiDirsComponent"/>'
            "</DOCUMENT>"
        )


def test_read_aip_reports_a_missing_file(tmp_path):
    with pytest.raises(AipParseError, match="cannot read project file"):
        read_aip(tmp_path / "nope.aip")


def test_read_aip_records_its_source(tmp_path):
    path = tmp_path / "setup.aip"
    path.write_text(MINIMAL, encoding="utf-8")
    assert read_aip(path).source == path
