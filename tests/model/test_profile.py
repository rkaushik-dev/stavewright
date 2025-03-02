from __future__ import annotations

from pathlib import PureWindowsPath

import pytest
from pydantic import ValidationError

from v4t_setup.model.locale import resolve
from v4t_setup.model.profile import Architecture, TerminalModel, TerminalProfile


def make_profile(**kwargs) -> TerminalProfile:
    base = {"terminal_id": "LINE-PC-04", "fpga_revision": "PCDC-1.4"}
    base.update(kwargs)
    return TerminalProfile(**base)


def test_defaults_describe_a_64_bit_s_jet_600():
    profile = make_profile()
    assert profile.machine is TerminalModel.SJET_600
    assert profile.architecture is Architecture.X64
    assert profile.head_count == 1
    assert profile.languages == ()


def test_profiles_are_frozen():
    profile = make_profile()
    with pytest.raises(ValidationError):
        profile.terminal_id = "LINE-PC-05"


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        make_profile(printer_ip="10.0.0.4")


@pytest.mark.parametrize("value", ["", "   ", "LINE PC 04", "LINE\tPC"])
def test_terminal_id_must_be_a_single_token(value):
    with pytest.raises(ValidationError):
        make_profile(terminal_id=value)


def test_terminal_id_is_stripped():
    assert make_profile(terminal_id="  LINE-PC-04  ").terminal_id == "LINE-PC-04"


def test_install_root_comes_from_the_aip_directory_rows():
    profile = make_profile()
    assert profile.install_root == PureWindowsPath(r"C:\Sellenis\S-jet600")


def test_install_root_follows_a_different_windows_volume():
    profile = make_profile(
        windows_volume=PureWindowsPath("D:\\"),
        windows_directory=PureWindowsPath(r"D:\Windows"),
    )
    assert profile.install_root == PureWindowsPath(r"D:\Sellenis\S-jet600")


def test_system_folder_is_redirected_on_x64():
    profile = make_profile(architecture=Architecture.X64)
    assert profile.system_folder == PureWindowsPath(r"C:\Windows\SysWOW64")
    assert profile.system64_folder == PureWindowsPath(r"C:\Windows\System32")


def test_system_folder_is_not_redirected_on_x86():
    profile = make_profile(architecture=Architecture.X86)
    assert profile.system_folder == PureWindowsPath(r"C:\Windows\System32")
    assert profile.system64_folder == PureWindowsPath(r"C:\Windows\System32")


def test_has_wow64_only_on_x64():
    assert Architecture.X64.has_wow64
    assert not Architecture.X86.has_wow64


@pytest.mark.parametrize(
    ("machine", "heads"),
    [
        (TerminalModel.SJET_600, 4),
        (TerminalModel.SJET_600_TIJ, 2),
        (TerminalModel.SJET_MINI, 1),
    ],
)
def test_head_limits_per_machine(machine, heads):
    assert machine.max_heads == heads


def test_head_count_within_the_limit_is_accepted():
    assert make_profile(machine=TerminalModel.SJET_600, head_count=4).head_count == 4


def test_head_count_above_the_machine_limit_is_rejected():
    with pytest.raises(ValidationError, match="drives at most 2 head"):
        make_profile(machine=TerminalModel.SJET_600_TIJ, head_count=3)


def test_head_count_above_the_field_ceiling_is_rejected():
    with pytest.raises(ValidationError):
        make_profile(head_count=5)


def test_head_count_below_one_is_rejected():
    with pytest.raises(ValidationError):
        make_profile(head_count=0)


def test_the_mini_has_no_fpga():
    assert not TerminalModel.SJET_MINI.has_fpga
    assert TerminalModel.SJET_600.has_fpga
    assert TerminalModel.SJET_600_TIJ.has_fpga


def test_mini_profile_must_not_carry_a_bitstream_revision():
    with pytest.raises(ValidationError, match="no FPGA controller"):
        TerminalProfile(
            terminal_id="LINE-PC-09",
            machine=TerminalModel.SJET_MINI,
            fpga_revision="PCDC-1.4",
        )


def test_mini_profile_is_fine_without_one():
    profile = TerminalProfile(terminal_id="LINE-PC-09", machine=TerminalModel.SJET_MINI)
    assert profile.fpga_revision is None


def test_a_machine_with_a_controller_must_declare_a_revision():
    with pytest.raises(ValidationError, match="needs an fpga_revision"):
        TerminalProfile(terminal_id="LINE-PC-04", machine=TerminalModel.SJET_600)


@pytest.mark.parametrize(
    ("machine", "dots"),
    [
        (TerminalModel.SJET_600, 384),
        (TerminalModel.SJET_600_TIJ, 300),
        (TerminalModel.SJET_MINI, 128),
    ],
)
def test_swath_height_per_machine(machine, dots):
    assert machine.swath_dots == dots


def test_languages_are_resolved_from_loose_input():
    profile = make_profile(languages=["ru", 1045, "LangTurkish"])
    assert profile.language_ids == (1045, 1049, 1055)


def test_language_input_is_deduplicated_and_ordered():
    profile = make_profile(languages=["tr-TR", "ru-RU", "ru", 1055])
    assert profile.language_ids == (1049, 1055)


def test_language_entries_pass_through_untouched():
    entries = (resolve("pl-PL"), resolve("de-DE"))
    profile = make_profile(languages=entries)
    assert profile.languages == entries


def test_no_languages_is_allowed():
    assert make_profile(languages=[]).languages == ()


def test_unknown_language_is_rejected():
    with pytest.raises(ValidationError):
        make_profile(languages=["kl-KL"])


def test_with_languages_returns_a_new_profile():
    profile = make_profile(languages=["ru-RU"])
    other = profile.with_languages(["pl-PL", "de-DE"])
    assert profile.language_ids == (1049,)
    assert other.language_ids == (1031, 1045)
    assert other.terminal_id == profile.terminal_id


def test_needs_base_language_only():
    assert make_profile(languages=[1033]).needs_base_language_only
    assert make_profile(languages=[]).needs_base_language_only
    assert not make_profile(languages=[1033, "pl-PL"]).needs_base_language_only


def test_os_build_floor():
    with pytest.raises(ValidationError):
        make_profile(os_build=2000)


def test_describe_reads_as_a_report_header():
    profile = make_profile(head_count=2)
    assert profile.describe() == "LINE-PC-04 (s-jet600, x64, 2 heads)"


def test_describe_uses_the_singular_for_one_head():
    assert make_profile().describe().endswith("1 head)")
