"""Tests for the Windows target.

The parts that need a real registry are marked ``windows`` and skipped when the
suite runs off a bench. Everything else runs anywhere, because path handling,
the dry run redirect and the platform guards are where the bugs actually are.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stavewright.model.registry import DecodedValue, RegistryRoot, RegistryValueType
from stavewright.target.base import AssemblyIdentity, ShortcutSpec, TargetError, TargetSystem
from stavewright.target.windows import (
    IS_WINDOWS,
    WindowsTarget,
    hive_handle,
    require_windows,
    value_type_name,
)

needs_windows = pytest.mark.skipif(not IS_WINDOWS, reason="needs a real Windows registry")

MFC = AssemblyIdentity(
    name="Microsoft.VC80.MFC", version="8.0.50727.6195", public_key_token="1fc8b3b9a1e18e3b"
)


@pytest.fixture
def sandbox(tmp_path) -> WindowsTarget:
    """A target whose file writes are redirected under tmp_path."""
    return WindowsTarget(dry_run_root=tmp_path)


def test_the_windows_target_satisfies_the_protocol(sandbox):
    assert isinstance(sandbox, TargetSystem)


def test_the_dry_run_root_strips_the_drive(tmp_path, sandbox):
    sandbox.write_file(r"C:\Sellenis\S-jet600\V4T.exe", b"MZ")
    assert (tmp_path / "Sellenis" / "S-jet600" / "V4T.exe").read_bytes() == b"MZ"


def test_a_redirected_write_creates_parents(tmp_path, sandbox):
    sandbox.write_file(r"C:\Sellenis\S-jet600\Templates\T1.spf", b"spf")
    assert (tmp_path / "Sellenis" / "S-jet600" / "Templates").is_dir()


def test_a_redirected_target_reads_back_what_it_wrote(sandbox):
    sandbox.write_file(r"C:\Sellenis\S-jet600\V4T.exe", b"MZ")
    assert sandbox.read_file(r"C:\Sellenis\S-jet600\V4T.exe") == b"MZ"


def test_rewriting_the_same_bytes_reports_no_change(sandbox):
    assert sandbox.write_file(r"C:\Sellenis\V4T.exe", b"MZ")
    assert not sandbox.write_file(r"C:\Sellenis\V4T.exe", b"MZ")


def test_rewriting_different_bytes_reports_a_change(sandbox):
    sandbox.write_file(r"C:\Sellenis\V4T.exe", b"MZ")
    assert sandbox.write_file(r"C:\Sellenis\V4T.exe", b"MZ\x90")


def test_reading_something_that_is_not_there_is_an_error(sandbox):
    with pytest.raises(TargetError, match="cannot read"):
        sandbox.read_file(r"C:\Sellenis\missing.exe")


def test_making_and_removing_a_directory(sandbox):
    assert sandbox.make_directory(r"C:\Sellenis\S-jet600\log")
    assert not sandbox.make_directory(r"C:\Sellenis\S-jet600\log")
    assert sandbox.is_directory(r"C:\Sellenis\S-jet600\log")
    assert sandbox.remove_directory(r"C:\Sellenis\S-jet600\log")
    assert not sandbox.remove_directory(r"C:\Sellenis\S-jet600\log")


def test_a_directory_cannot_replace_a_file(sandbox):
    sandbox.write_file(r"C:\Sellenis\V4T.exe", b"MZ")
    with pytest.raises(TargetError, match="a file already exists"):
        sandbox.make_directory(r"C:\Sellenis\V4T.exe")


def test_a_file_cannot_replace_a_directory(sandbox):
    sandbox.make_directory(r"C:\Sellenis\log")
    with pytest.raises(TargetError, match="a directory already exists"):
        sandbox.write_file(r"C:\Sellenis\log", b"MZ")


def test_removing_a_file(sandbox):
    sandbox.write_file(r"C:\Sellenis\V4T.exe", b"MZ")
    assert sandbox.remove_file(r"C:\Sellenis\V4T.exe")
    assert not sandbox.remove_file(r"C:\Sellenis\V4T.exe")


def test_removing_a_directory_through_remove_file_does_nothing(sandbox):
    sandbox.make_directory(r"C:\Sellenis\log")
    assert not sandbox.remove_file(r"C:\Sellenis\log")
    assert sandbox.is_directory(r"C:\Sellenis\log")


def test_file_digest(sandbox):
    from stavewright.util.digest import digest_bytes

    assert sandbox.file_digest(r"C:\Sellenis\V4T.exe") is None
    sandbox.write_file(r"C:\Sellenis\V4T.exe", b"MZ")
    assert sandbox.file_digest(r"C:\Sellenis\V4T.exe") == digest_bytes(b"MZ")


def test_listing_a_directory(sandbox):
    sandbox.write_file(r"C:\Sellenis\b.dll", b"x")
    sandbox.write_file(r"C:\Sellenis\a.dll", b"x")
    assert sandbox.list_directory(r"C:\Sellenis") == ["a.dll", "b.dll"]


def test_listing_something_that_is_not_a_directory_is_empty(sandbox):
    assert sandbox.list_directory(r"C:\Nowhere") == []


def test_a_target_without_a_dry_run_root_uses_the_real_path():
    target = WindowsTarget()
    assert target._local(r"C:\Sellenis\V4T.exe") == Path(r"C:\Sellenis\V4T.exe")


def test_the_view_defaults_to_64_bit():
    assert WindowsTarget().view_64bit
    assert not WindowsTarget(view_64bit=False).view_64bit


def test_hkmu_cannot_be_opened():
    with pytest.raises(TargetError, match="must be resolved"):
        hive_handle(RegistryRoot.USER_OR_MACHINE)


@pytest.mark.skipif(IS_WINDOWS, reason="this is the off bench behaviour")
def test_windows_only_operations_refuse_elsewhere(sandbox):
    with pytest.raises(TargetError, match="needs a Windows terminal"):
        require_windows("something")
    with pytest.raises(TargetError, match="needs a Windows terminal"):
        sandbox.register_com(r"C:\Windows\SysWOW64\C1Sizer.ocx")
    with pytest.raises(TargetError, match="needs a Windows terminal"):
        sandbox.stage_driver("src", "ftdibus.inf")


@pytest.mark.skipif(IS_WINDOWS, reason="this is the off bench behaviour")
def test_the_registry_refuses_elsewhere():
    with pytest.raises(TargetError, match="needs a Windows terminal"):
        WindowsTarget().key_exists(RegistryRoot.LOCAL_MACHINE, "Software")


def test_programming_the_controller_says_what_to_use_instead(sandbox):
    with pytest.raises(TargetError, match="qu_imager"):
        sandbox.program_controller("src", "PCDC-1.4")


def test_the_assembly_directory_name_follows_the_store_layout():
    target = WindowsTarget()
    directory = target._assembly_dir(MFC, policy=False)
    assert directory.name == (
        "x86_assembly_Microsoft.VC80.MFC_1fc8b3b9a1e18e3b_8.0.50727.6195"
    )


def test_the_policy_directory_differs_from_the_assembly_one():
    target = WindowsTarget()
    assert target._assembly_dir(MFC, policy=True) != target._assembly_dir(MFC, policy=False)
    assert "policy" in target._assembly_dir(MFC, policy=True).name


def test_a_policy_without_its_assembly_is_refused(tmp_path, monkeypatch):
    target = WindowsTarget()
    monkeypatch.setattr(target, "has_assembly", lambda identity: False)
    with pytest.raises(TargetError, match="no assembly for policy"):
        target.install_policy(MFC, str(tmp_path))


def test_shortcut_paths_get_the_lnk_suffix(tmp_path, sandbox):
    spec = ShortcutSpec(
        name="S-jet 600", directory=r"C:\Users\Public\Desktop", target=r"C:\Sellenis\V4T.exe"
    )
    assert sandbox._shortcut_path(spec).name == "S-jet 600.lnk"
    assert not sandbox.has_shortcut(spec)


def test_removing_a_shortcut_that_is_not_there(sandbox):
    spec = ShortcutSpec(
        name="S-jet 600", directory=r"C:\Users\Public\Desktop", target=r"C:\Sellenis\V4T.exe"
    )
    assert not sandbox.remove_shortcut(spec)


@pytest.mark.parametrize(
    ("value_type", "name"),
    [
        (RegistryValueType.STRING, "REG_SZ"),
        (RegistryValueType.EXPAND_STRING, "REG_EXPAND_SZ"),
        (RegistryValueType.DWORD, "REG_DWORD"),
        (RegistryValueType.BINARY, "REG_BINARY"),
        (RegistryValueType.MULTI_STRING, "REG_MULTI_SZ"),
    ],
)
def test_value_type_names(value_type, name):
    assert value_type_name(DecodedValue(value_type, "")) == name


def test_running_something_that_is_not_there_is_reported(sandbox):
    with pytest.raises(TargetError, match="cannot "):
        sandbox._run(["definitely-not-a-real-command-4f2a"], "run the thing")


def test_a_failing_command_can_be_tolerated(sandbox):
    if IS_WINDOWS:
        result = sandbox._run(["cmd.exe", "/c", "exit 3"], "fail", check=False)
    else:
        result = sandbox._run(["sh", "-c", "exit 3"], "fail", check=False)
    assert result.returncode == 3


def test_a_failing_command_raises_when_checked(sandbox):
    command = ["cmd.exe", "/c", "exit 3"] if IS_WINDOWS else ["sh", "-c", "exit 3"]
    with pytest.raises(TargetError, match="exit 3"):
        sandbox._run(command, "fail")


@needs_windows
@pytest.mark.windows
def test_reading_the_facts_off_a_real_machine():
    facts = WindowsTarget().facts()
    assert facts.os_build > 0
    assert facts.architecture in {"x86", "x64"}
    assert facts.free_bytes > 0
    assert facts.computer_name


@needs_windows
@pytest.mark.windows
def test_a_key_that_always_exists():
    assert WindowsTarget().key_exists(RegistryRoot.LOCAL_MACHINE, "SOFTWARE")


@needs_windows
@pytest.mark.windows
def test_a_key_that_never_exists():
    assert not WindowsTarget().key_exists(
        RegistryRoot.LOCAL_MACHINE, r"SOFTWARE\NoSuchKey4f2a"
    )


@needs_windows
@pytest.mark.windows
def test_reading_a_value_that_is_not_there_is_none():
    assert (
        WindowsTarget().read_value(RegistryRoot.LOCAL_MACHINE, "SOFTWARE", "NoSuchValue4f2a")
        is None
    )


@needs_windows
@pytest.mark.windows
def test_a_round_trip_through_the_real_registry():
    target = WindowsTarget()
    key = r"Software\Sellenis\StavewrightTest"
    try:
        assert target.write_value(
            RegistryRoot.CURRENT_USER, key, "Version", DecodedValue(RegistryValueType.STRING, "1.0.0")
        )
        assert not target.write_value(
            RegistryRoot.CURRENT_USER, key, "Version", DecodedValue(RegistryValueType.STRING, "1.0.0")
        )
        read = target.read_value(RegistryRoot.CURRENT_USER, key, "Version")
        assert read == DecodedValue(RegistryValueType.STRING, "1.0.0")
        assert target.list_values(RegistryRoot.CURRENT_USER, key) == ["Version"]
    finally:
        target.remove_key(RegistryRoot.CURRENT_USER, r"Software\Sellenis")


@needs_windows
@pytest.mark.windows
def test_a_dword_round_trips_through_the_real_registry():
    target = WindowsTarget()
    key = r"Software\Sellenis\StavewrightTest"
    try:
        target.write_value(
            RegistryRoot.CURRENT_USER, key, "Heads", DecodedValue(RegistryValueType.DWORD, 4)
        )
        read = target.read_value(RegistryRoot.CURRENT_USER, key, "Heads")
        assert read.type is RegistryValueType.DWORD
        assert read.data == 4
    finally:
        target.remove_key(RegistryRoot.CURRENT_USER, r"Software\Sellenis")
