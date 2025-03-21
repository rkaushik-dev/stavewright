"""Every constant in __about__ is checked against the shipped project file.

These constants are copied by hand out of setup.aip, which means they can drift
away from it silently. REGISTRY_PRODUCT_KEY did exactly that: it was written as
SOFTWARE\\Sellenis\\S-jet600 when the package actually writes to
Software\\Matricode\\V4T, and the effect was that reconcile looked in a key
nothing had ever created and reported every commissioned terminal as untouched.

Nothing here should ever be relaxed to make it pass. If a test in this file
fails, either setup.aip changed on purpose and the constant follows it, or the
constant was wrong.
"""

from __future__ import annotations

import pytest

from v4t_setup import __about__
from v4t_setup.aip.normalize import normalise
from v4t_setup.aip.reader import read_aip


@pytest.fixture(scope="module")
def shipped(request):
    return normalise(read_aip(request.path.parent.parent / "setup.aip"))


def test_manufacturer(shipped):
    assert shipped.properties["Manufacturer"] == __about__.MANUFACTURER


def test_product_name(shipped):
    assert shipped.properties["ProductName"] == __about__.PRODUCT_NAME


def test_product_version(shipped):
    assert shipped.properties["ProductVersion"] == __about__.PRODUCT_VERSION


def test_upgrade_code(shipped):
    assert shipped.properties["UpgradeCode"] == __about__.UPGRADE_CODE


def test_product_language(shipped):
    assert str(__about__.PRODUCT_LANGUAGE) == shipped.properties["ProductLanguage"]


def test_product_code_matches_the_typed_property(shipped):
    # The ProductCode property carries a language prefix, as `1033:{GUID} `.
    raw = shipped.properties["ProductCode"]
    assert __about__.PRODUCT_CODE in raw
    assert raw.split(":", 1)[0] == str(__about__.PRODUCT_LANGUAGE)


def test_install_directories_come_from_the_directory_rows(shipped):
    assert shipped.directories["Matricode_Dir"].target_name == __about__.INSTALL_VENDOR_DIR
    assert shipped.directories["Quattro_Dir"].target_name == __about__.INSTALL_PRODUCT_DIR


def test_the_terminal_executable_has_a_file_row(shipped):
    names = {entry.target_name for entry in shipped.files}
    assert __about__.TERMINAL_EXECUTABLE in names


def test_the_registry_product_key_is_the_one_the_package_writes(shipped):
    keys = {row.key for row in shipped.registry}
    assert __about__.REGISTRY_PRODUCT_KEY in keys


def test_the_account_key_is_the_one_the_package_writes(shipped):
    keys = {row.key for row in shipped.registry}
    assert __about__.REGISTRY_ACCOUNT_KEY in keys


def test_the_product_key_is_not_under_the_vendor_name(shipped):
    # Guards the exact mistake this file was written for.
    assert __about__.INSTALL_VENDOR_DIR not in __about__.REGISTRY_PRODUCT_KEY


def test_the_product_information_component_writes_to_the_product_key(shipped):
    rows = shipped.registry_for("ProductInformation")
    assert rows
    assert all(row.key == __about__.REGISTRY_PRODUCT_KEY for row in rows)
    assert {row.value_name for row in rows} == {"Path", "Version"}


def test_the_version_row_writes_the_product_version(shipped):
    rows = {row.value_name: row for row in shipped.registry_for("ProductInformation")}
    assert rows["Version"].value == "[ProductVersion]"


def test_the_base_install_level_selects_the_whole_package(shipped):
    selected = shipped.features.components_for(__about__.BASE_INSTALL_LEVEL)
    assert selected == shipped.component_keys()


def test_version_is_a_three_field_string():
    parts = __about__.__version__.split(".")
    assert len(parts) == 3
    assert all(part.isdigit() for part in parts)


def test_schema_versions_start_at_one():
    assert __about__.MANIFEST_SCHEMA_VERSION >= 1
    assert __about__.JOURNAL_SCHEMA_VERSION >= 1


def test_every_exported_name_exists():
    for name in __about__.__all__:
        assert hasattr(__about__, name), name


def test_nothing_public_is_left_out_of_all():
    public = {
        name
        for name in vars(__about__)
        if not name.startswith("_") or name == "__version__"
    }
    public -= {"annotations", "Final"}
    assert public <= set(__about__.__all__)
