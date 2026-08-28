from __future__ import annotations

import pytest

from stavewright.errors import ManifestError
from stavewright.model.registry import (
    DecodedValue,
    KeyOperation,
    RegistryEntry,
    RegistryRoot,
    RegistryValueType,
    decode_value,
    encode_value,
)

# Real rows out of the MsiRegsComponent table.
ENABLE_LUA = RegistryEntry(
    registry="EnableLUA",
    root=RegistryRoot.LOCAL_MACHINE,
    key=r"Software\Microsoft\Windows\CurrentVersion\Policies\System",
    name="EnableLUA",
    value="#0",
    component="EnableLUA",
)
PRODUCT_PATH = RegistryEntry(
    registry="Path",
    root=RegistryRoot.USER_OR_MACHINE,
    key=r"Software\Matricode\V4T",
    name="Path",
    value=r"C:\Sellenis\S-jet600",
    component="ProductInformation",
)
PRODUCT_VERSION = RegistryEntry(
    registry="Version",
    root=RegistryRoot.USER_OR_MACHINE,
    key=r"Software\Matricode\V4T",
    name="Version",
    value="1.0.0",
    component="ProductInformation",
)
SOFTWARE_KEY = RegistryEntry(
    registry="Software_1",
    root=RegistryRoot.CURRENT_USER,
    key="Software",
    name="+",
    component="Software",
)


@pytest.mark.parametrize(
    ("root", "name"),
    [
        (RegistryRoot.USER_OR_MACHINE, "HKMU"),
        (RegistryRoot.CLASSES_ROOT, "HKCR"),
        (RegistryRoot.CURRENT_USER, "HKCU"),
        (RegistryRoot.LOCAL_MACHINE, "HKLM"),
        (RegistryRoot.USERS, "HKU"),
    ],
)
def test_hive_names(root, name):
    assert root.hive_name == name


def test_user_or_machine_follows_allusers():
    assert RegistryRoot.USER_OR_MACHINE.resolve(True) is RegistryRoot.LOCAL_MACHINE
    assert RegistryRoot.USER_OR_MACHINE.resolve(False) is RegistryRoot.CURRENT_USER


@pytest.mark.parametrize(
    "root",
    [
        RegistryRoot.CLASSES_ROOT,
        RegistryRoot.CURRENT_USER,
        RegistryRoot.LOCAL_MACHINE,
        RegistryRoot.USERS,
    ],
)
def test_a_concrete_root_ignores_allusers(root):
    assert root.resolve(True) is root
    assert root.resolve(False) is root


def test_plain_text_is_a_string():
    decoded = decode_value("S-Jet 600")
    assert decoded.type is RegistryValueType.STRING
    assert decoded.data == "S-Jet 600"


def test_none_decodes_to_an_empty_string():
    assert decode_value(None) == DecodedValue(RegistryValueType.STRING, "")


def test_empty_cell_decodes_to_an_empty_string():
    assert decode_value("") == DecodedValue(RegistryValueType.STRING, "")


def test_the_enable_lua_row_is_a_dword_zero_not_the_text_hash_zero():
    decoded = ENABLE_LUA.decoded()
    assert decoded.type is RegistryValueType.DWORD
    assert decoded.data == 0
    assert decoded.data != "#0"


def test_a_larger_dword():
    assert decode_value("#65535") == DecodedValue(RegistryValueType.DWORD, 65535)


def test_a_negative_dword():
    assert decode_value("#-1") == DecodedValue(RegistryValueType.DWORD, -1)


def test_a_number_that_is_not_a_number_is_reported():
    with pytest.raises(ManifestError, match="not a number"):
        decode_value("#abc")


@pytest.mark.parametrize("cell", ["#x1F2A", "#X1f2a"])
def test_binary_values(cell):
    decoded = decode_value(cell)
    assert decoded.type is RegistryValueType.BINARY
    assert decoded.data == b"\x1f\x2a"


def test_an_odd_length_binary_run_is_reported():
    with pytest.raises(ManifestError, match="odd number of hex digits"):
        decode_value("#x1F2")


def test_a_binary_run_that_is_not_hex_is_reported():
    with pytest.raises(ManifestError, match="not hexadecimal"):
        decode_value("#xZZ")


def test_an_empty_binary_run_is_allowed():
    assert decode_value("#x") == DecodedValue(RegistryValueType.BINARY, b"")


def test_expandable_strings():
    # The marker is the whole `#%`, so a value that itself starts with a percent
    # sign needs three characters of prefix before the data begins.
    decoded = decode_value("#%%SystemRoot%\\System32")
    assert decoded.type is RegistryValueType.EXPAND_STRING
    assert decoded.data == "%SystemRoot%\\System32"


def test_an_expandable_string_that_does_not_start_with_a_percent():
    assert decode_value("#%C:\\Sellenis").data == "C:\\Sellenis"


def test_a_doubled_hash_escapes_to_a_literal():
    decoded = decode_value("##0")
    assert decoded.type is RegistryValueType.STRING
    assert decoded.data == "#0"


def test_multi_strings_split_on_nulls():
    decoded = decode_value("C:\\Sellenis\0C:\\Sellenis\\S-jet600")
    assert decoded.type is RegistryValueType.MULTI_STRING
    assert decoded.data == ("C:\\Sellenis", "C:\\Sellenis\\S-jet600")


@pytest.mark.parametrize(
    "cell",
    ["S-Jet 600", "#0", "#65535", "#x1F2A", "#%PATH%", "##0", "a\0b\0c", ""],
)
def test_encode_is_the_inverse_of_decode(cell):
    assert encode_value(decode_value(cell)) == cell


def test_str_of_a_binary_value_is_upper_hex():
    assert str(decode_value("#x1f2a")) == "1F2A"


def test_str_of_a_multi_string_joins_with_bars():
    assert str(decode_value("a\0b")) == "a | b"


def test_str_of_a_dword_is_the_number():
    assert str(decode_value("#42")) == "42"


def test_name_plus_means_create_the_key():
    assert SOFTWARE_KEY.operation is KeyOperation.CREATE_KEY
    assert SOFTWARE_KEY.value_name is None


def test_name_minus_means_remove_on_uninstall():
    entry = RegistryEntry(
        registry="R", root=RegistryRoot.LOCAL_MACHINE, key="Software\\X", name="-", component="C"
    )
    assert entry.operation is KeyOperation.REMOVE_KEY_ON_UNINSTALL
    assert entry.value_name is None


def test_name_star_means_create_key_and_default_value():
    entry = RegistryEntry(
        registry="R", root=RegistryRoot.LOCAL_MACHINE, key="Software\\X", name="*", component="C"
    )
    assert entry.operation is KeyOperation.CREATE_KEY_AND_DEFAULT
    assert entry.is_default_value
    assert entry.value_name is None


def test_an_ordinary_name_writes_that_value():
    assert PRODUCT_VERSION.operation is KeyOperation.WRITE_VALUE
    assert PRODUCT_VERSION.value_name == "Version"
    assert not PRODUCT_VERSION.is_default_value


def test_a_missing_name_writes_the_default_value():
    entry = RegistryEntry(
        registry="R", root=RegistryRoot.LOCAL_MACHINE, key="Software\\X", value="v", component="C"
    )
    assert entry.is_default_value
    assert entry.value_name is None


def test_a_row_with_no_key_is_refused():
    with pytest.raises(ManifestError, match="no key"):
        RegistryEntry(registry="R", root=RegistryRoot.LOCAL_MACHINE, key="", component="C")


def test_the_product_block_lands_in_hklm_because_allusers_is_one():
    assert PRODUCT_PATH.hive() is RegistryRoot.LOCAL_MACHINE
    assert PRODUCT_PATH.full_path() == r"HKLM\Software\Matricode\V4T"


def test_the_same_row_would_land_in_hkcu_for_a_per_user_install():
    assert PRODUCT_PATH.hive(all_users=False) is RegistryRoot.CURRENT_USER
    assert PRODUCT_PATH.full_path(all_users=False) == r"HKCU\Software\Matricode\V4T"


def test_describe_a_written_value():
    assert PRODUCT_VERSION.describe() == (
        r"HKLM\Software\Matricode\V4T\Version = 1.0.0 [REG_SZ]"
    )


def test_describe_a_dword():
    assert ENABLE_LUA.describe() == (
        r"HKLM\Software\Microsoft\Windows\CurrentVersion\Policies\System"
        r"\EnableLUA = 0 [REG_DWORD]"
    )


def test_describe_a_create_key_row():
    assert SOFTWARE_KEY.describe() == r"HKCU\Software (create key)"


def test_describe_a_remove_key_row():
    entry = RegistryEntry(
        registry="R", root=RegistryRoot.LOCAL_MACHINE, key="Software\\X", name="-", component="C"
    )
    assert entry.describe() == r"HKLM\Software\X (remove on uninstall)"


def test_describe_a_default_value_row():
    entry = RegistryEntry(
        registry="R", root=RegistryRoot.CLASSES_ROOT, key="V4T.Job", value="Job", component="C"
    )
    assert entry.describe() == r"HKCR\V4T.Job\(Default) = Job [REG_SZ]"
