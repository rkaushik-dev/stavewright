from __future__ import annotations

from pathlib import PureWindowsPath

import pytest

from stavewright.__about__ import MANUFACTURER, PRODUCT_NAME, UPGRADE_CODE
from stavewright.aip.normalize import normalise
from stavewright.aip.reader import parse_aip, read_aip
from stavewright.errors import AipParseError, ManifestError
from stavewright.model.component import ComponentAttributes
from stavewright.model.registry import RegistryRoot, RegistryValueType
from stavewright.util.msiver import MsiVersion, UpgradeAttributes

SMALL = """<DOCUMENT>
  <COMPONENT cid="caphyon.advinst.msicomp.MsiPropsComponent">
    <ROW Property="Manufacturer" Value="Sellenis"/>
    <ROW Property="ProductName" Value="S-Jet 600"/>
    <ROW Property="ProductVersion" Value="1.0.0" Type="32"/>
    <ROW Property="ALLUSERS" Value="1"/>
    <ROW Property="UpgradeCode" Value="{73E3F36D-4DCA-4737-AF3A-6784EAFB3E68}" Type="16"/>
  </COMPONENT>
  <COMPONENT cid="caphyon.advinst.msicomp.MsiDirsComponent">
    <ROW Directory="TARGETDIR" DefaultDir="SourceDir"/>
    <ROW Directory="APPDIR" Directory_Parent="TARGETDIR" DefaultDir="APPDIR:." IsPseudoRoot="1"/>
  </COMPONENT>
  <COMPONENT cid="caphyon.advinst.msicomp.MsiCompsComponent">
    <ROW Component="V4T.exe" ComponentId="{A6B2E442-5247-4D76-BEFE-7100B893FAA8}"
         Directory_="APPDIR" Attributes="0" KeyPath="V4T.exe"/>
    <ROW Component="ProductInformation" ComponentId="{1F32B609-B83D-4795-BAF9-8F1FBEBF34A6}"
         Directory_="APPDIR" Attributes="4" KeyPath="Version"/>
  </COMPONENT>
  <COMPONENT cid="caphyon.advinst.msicomp.MsiFeatsComponent">
    <ROW Feature="MainFeature" Title="MainFeature" Display="1" Level="1"
         Directory_="APPDIR" Attributes="0" Components="V4T.exe ProductInformation"/>
  </COMPONENT>
  <COMPONENT cid="caphyon.advinst.msicomp.MsiFilesComponent">
    <ROW File="V4T.exe" Component_="V4T.exe" FileName="V4T.exe" Attributes="512"
         SourcePath="..\\Release\\V4T.exe" SelfReg="false"/>
  </COMPONENT>
  <COMPONENT cid="caphyon.advinst.msicomp.MsiRegsComponent">
    <ROW Registry="Version" Root="-1" Key="Software\\Matricode\\V4T" Name="Version"
         Value="[ProductVersion]" Component_="ProductInformation"/>
  </COMPONENT>
  <COMPONENT cid="caphyon.advinst.msicomp.MsiUpgradeComponent">
    <ROW UpgradeCode="[|UpgradeCode]" VersionMin="0.0.1" VersionMax="[|ProductVersion]"
         Attributes="257" ActionProperty="OLDPRODUCTS"/>
    <ROW UpgradeCode="[|UpgradeCode]" VersionMin="[|ProductVersion]" Attributes="2"
         ActionProperty="AI_NEWERPRODUCTFOUND"/>
  </COMPONENT>
</DOCUMENT>
"""


@pytest.fixture
def small():
    return normalise(parse_aip(SMALL))


@pytest.fixture
def shipped(aip_path):
    return normalise(read_aip(aip_path))


def test_product_identity(small):
    assert small.product_name == "S-Jet 600"
    assert small.manufacturer == "Sellenis"
    assert small.product_version == MsiVersion.parse("1.0.0")
    assert small.upgrade_code == UPGRADE_CODE
    assert small.all_users


def test_missing_product_version_is_reported():
    project = normalise(parse_aip("<DOCUMENT/>"))
    with pytest.raises(ManifestError, match="declares no ProductVersion"):
        _ = project.product_version


def test_unparseable_product_version_is_reported():
    project = normalise(
        parse_aip(
            '<DOCUMENT><COMPONENT cid="caphyon.advinst.msicomp.MsiPropsComponent">'
            '<ROW Property="ProductVersion" Value="v1"/></COMPONENT></DOCUMENT>'
        )
    )
    with pytest.raises(ManifestError, match="not an MSI version"):
        _ = project.product_version


def test_all_users_is_false_when_the_property_is_absent():
    assert not normalise(parse_aip("<DOCUMENT/>")).all_users


def test_directories_become_a_resolvable_table(small, profile):
    assert small.directories.resolve("APPDIR", profile) == PureWindowsPath(
        r"C:\Sellenis\S-jet600"
    )


def test_pseudo_root_flag_survives(small):
    assert small.directories["APPDIR"].is_pseudo_root
    assert not small.directories["TARGETDIR"].is_pseudo_root


def test_component_attributes_are_typed(small):
    by_key = {c.component: c for c in small.components}
    assert by_key["ProductInformation"].attributes is ComponentAttributes.REGISTRY_KEY_PATH
    assert by_key["ProductInformation"].is_registry_key_path
    assert not by_key["V4T.exe"].is_registry_key_path


def test_the_packed_components_cell_becomes_a_tuple(small):
    assert small.features["MainFeature"].components == ("V4T.exe", "ProductInformation")


def test_file_attributes_are_typed(small):
    assert small.files[0].is_vital
    assert small.files[0].target_name == "V4T.exe"


def test_registry_root_is_typed(small):
    assert small.registry[0].root is RegistryRoot.USER_OR_MACHINE
    assert small.registry[0].hive(all_users=True) is RegistryRoot.LOCAL_MACHINE


def test_registry_value_is_left_as_a_formatted_field(small):
    # Expansion of a registry value happens at apply time against the terminal's
    # own properties, not here.
    assert small.registry[0].value == "[ProductVersion]"
    assert small.registry[0].decoded().type is RegistryValueType.STRING


def test_upgrade_rows_have_their_formatted_fields_expanded(small):
    old, newer = small.upgrades
    assert old.upgrade_code == UPGRADE_CODE
    assert old.version_min == MsiVersion.parse("0.0.1")
    assert old.version_max == MsiVersion.parse("1.0.0")
    assert newer.upgrade_code == UPGRADE_CODE
    assert newer.version_min == MsiVersion.parse("1.0.0")
    assert newer.version_max is None


def test_upgrade_attributes_are_decoded(small):
    old, newer = small.upgrades
    assert old.attributes == (
        UpgradeAttributes.VERSION_MIN_INCLUSIVE | UpgradeAttributes.MIGRATE_FEATURES
    )
    assert old.min_inclusive
    assert not old.detect_only
    assert newer.detect_only


def test_an_upgrade_row_with_a_bad_version_is_reported():
    with pytest.raises(AipParseError, match="version that will not parse"):
        normalise(
            parse_aip(
                '<DOCUMENT><COMPONENT cid="caphyon.advinst.msicomp.MsiUpgradeComponent">'
                '<ROW UpgradeCode="{73E3F36D-4DCA-4737-AF3A-6784EAFB3E68}" VersionMin="alpha"'
                ' ActionProperty="OLDPRODUCTS"/></COMPONENT></DOCUMENT>'
            )
        )


def test_an_upgrade_row_with_no_code_is_reported():
    with pytest.raises(AipParseError, match="no UpgradeCode"):
        normalise(
            parse_aip(
                '<DOCUMENT><COMPONENT cid="caphyon.advinst.msicomp.MsiUpgradeComponent">'
                '<ROW ActionProperty="OLDPRODUCTS"/></COMPONENT></DOCUMENT>'
            )
        )


def test_an_unknown_registry_root_is_reported():
    with pytest.raises(AipParseError, match="unknown root"):
        normalise(
            parse_aip(
                '<DOCUMENT><COMPONENT cid="caphyon.advinst.msicomp.MsiRegsComponent">'
                '<ROW Registry="R" Root="9" Key="Software" Component_="C"/>'
                "</COMPONENT></DOCUMENT>",
            ),
            check_references=False,
        )


def test_a_feature_naming_a_missing_component_is_reported():
    broken = SMALL.replace('Components="V4T.exe ProductInformation"', 'Components="Ghost"')
    with pytest.raises(ManifestError, match="feature names a component"):
        normalise(parse_aip(broken))


def test_a_file_naming_a_missing_component_is_reported():
    broken = SMALL.replace('File="V4T.exe" Component_="V4T.exe"', 'File="V4T.exe" Component_="Gh"')
    with pytest.raises(ManifestError, match="file names a component"):
        normalise(parse_aip(broken))


def test_a_registry_row_naming_a_missing_component_is_reported():
    broken = SMALL.replace('Component_="ProductInformation"', 'Component_="Ghost"')
    with pytest.raises(ManifestError, match="registry row names a component"):
        normalise(parse_aip(broken))


def test_a_component_naming_a_missing_directory_is_reported():
    broken = SMALL.replace('Directory_="APPDIR" Attributes="0"', 'Directory_="Ghost" Attributes="0"')
    with pytest.raises(ManifestError, match="component names a directory"):
        normalise(parse_aip(broken))


def test_reference_checking_can_be_turned_off():
    broken = SMALL.replace('Components="V4T.exe ProductInformation"', 'Components="Ghost"')
    assert normalise(parse_aip(broken), check_references=False).features["MainFeature"]


def test_lookups_by_component(small):
    assert [entry.file for entry in small.files_for("V4T.exe")] == ["V4T.exe"]
    assert small.files_for("ProductInformation") == []
    assert [r.registry for r in small.registry_for("ProductInformation")] == ["Version"]


def test_an_empty_project_normalises_to_nothing():
    empty = normalise(parse_aip("<DOCUMENT/>"))
    assert empty.components == []
    assert len(empty.directories) == 0
    assert len(empty.features) == 0
    assert empty.upgrades == []


# The shipped project. These numbers change only when someone edits setup.aip,
# and if they do the change should be deliberate.


def test_the_shipped_project_normalises(shipped):
    assert shipped.product_name == PRODUCT_NAME
    assert shipped.manufacturer == MANUFACTURER
    assert shipped.upgrade_code == UPGRADE_CODE
    assert shipped.all_users


def test_the_shipped_project_row_counts(shipped):
    assert len(shipped.directories) == 38
    assert len(shipped.components) == 58
    assert len(shipped.files) == 35
    assert len(shipped.registry) == 9
    assert len(shipped.features) == 1
    assert len(shipped.upgrades) == 2


def test_the_shipped_project_has_one_feature_holding_everything(shipped):
    assert shipped.features.roots() == ["MainFeature"]
    assert shipped.features.components_for(100) == shipped.component_keys()


def test_the_shipped_project_has_no_orphan_components(shipped):
    assert shipped.features.orphan_components(shipped.components) == []


def test_the_shipped_upgrade_table_brackets_the_current_version(shipped):
    old, newer = shipped.upgrades
    assert old.action_property == "OLDPRODUCTS"
    assert old.version_max == shipped.product_version
    assert newer.action_property == "AI_NEWERPRODUCTFOUND"
    assert newer.version_min == shipped.product_version
    assert newer.detect_only


def test_the_shipped_project_puts_the_product_block_under_matricode(shipped):
    keys = {row.key for row in shipped.registry}
    assert r"Software\Matricode\V4T" in keys


def test_every_shipped_component_resolves_to_a_path(shipped, profile):
    for component in shipped.components:
        resolved = shipped.directories.resolve(
            component.directory, profile, native=component.is_64bit
        )
        assert resolved.is_absolute()


def test_the_shipped_ocx_rows_land_in_different_hives(shipped, profile):
    by_key = {c.component: c for c in shipped.components}
    wow = shipped.directories.resolve("SystemFolder", profile, native=by_key["C1Sizer.ocx"].is_64bit)
    native = shipped.directories.resolve(
        "System64Folder", profile, native=by_key["C1Sizer.ocx_1"].is_64bit
    )
    assert wow == PureWindowsPath(r"C:\Windows\SysWOW64")
    assert native == PureWindowsPath(r"C:\Windows\System32")
