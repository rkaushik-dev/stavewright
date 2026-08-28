from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from stavewright.errors import ManifestError
from stavewright.model.fileentry import (
    FileAttributes,
    FileEntry,
    duplicate_target_names,
    index_by_component,
)

# Real rows out of the MsiFilesComponent table.
OCX_WOW = FileEntry.from_cells(
    file="C1Sizer.ocx",
    component="C1Sizer.ocx_1",
    file_name="C1Sizer.ocx",
    source_path=r"ocx\SysWOW64\C1Sizer.ocx",
)
OCX_NATIVE = FileEntry.from_cells(
    file="C1Sizer.ocx_1",
    component="C1Sizer.ocx",
    file_name="C1Sizer.ocx",
    source_path=r"ocx\system32\C1Sizer.ocx",
)
GS1_DATA = FileEntry.from_cells(
    file="GS11.txt",
    component="GS11.txt",
    file_name="GS1-1.txt",
    source_path=r"Matricode\Quattro\Templates\GS1-1.txt",
)
LANG_CHINESE = FileEntry.from_cells(
    file="LangChinese.dll",
    component="LangChinese.dll",
    file_name="LANGCH~1.DLL|LangChinese.dll",
    source_path=r"..\Release\LangChinese.dll",
)
FPGA = FileEntry.from_cells(
    file="pcdc_top.rbf",
    component="pcdc_top.rbf",
    file_name="PCDC_T~1.RBF|pcdc_top.rbf",
    source_path=r"Matricode\Quattro\FpgaImage\pcdc_top.rbf",
    attributes=512,
)


def test_a_cell_with_both_forms_keeps_both():
    assert LANG_CHINESE.short_name == "LANGCH~1.DLL"
    assert LANG_CHINESE.target_name == "LangChinese.dll"
    assert LANG_CHINESE.has_long_name


def test_a_cell_with_one_form_uses_it_for_both():
    assert GS1_DATA.short_name == "GS1-1.txt"
    assert GS1_DATA.target_name == "GS1-1.txt"
    assert not GS1_DATA.has_long_name


@pytest.mark.parametrize(
    ("entry", "extension"),
    [
        (OCX_WOW, "ocx"),
        (GS1_DATA, "txt"),
        (LANG_CHINESE, "dll"),
        (FPGA, "rbf"),
    ],
)
def test_extension_is_lower_cased(entry, extension):
    assert entry.extension == extension


def test_a_name_without_an_extension_reports_empty():
    entry = FileEntry.from_cells(file="ocrb", component="ocrb.ttf", file_name="ocrb")
    assert entry.extension == ""


def test_vital_flag():
    assert FPGA.is_vital
    assert not GS1_DATA.is_vital


def test_combined_attributes():
    entry = FileEntry.from_cells(
        file="x", component="c", file_name="x.dll", attributes=512 | 1024
    )
    assert entry.attributes & FileAttributes.VITAL
    assert entry.attributes & FileAttributes.CHECKSUM


def test_versioned_files():
    versioned = FileEntry.from_cells(
        file="qu_imager.dll", component="qu_imager.dll", file_name="qu_imager.dll", version="2.1.0.0"
    )
    assert versioned.is_versioned
    assert not GS1_DATA.is_versioned


def test_an_empty_version_cell_is_treated_as_absent():
    entry = FileEntry.from_cells(file="x", component="c", file_name="x.dll", version="")
    assert not entry.is_versioned


@pytest.mark.parametrize("entry", [OCX_WOW, LANG_CHINESE])
def test_com_server_hint_is_true_for_binaries(entry):
    assert entry.looks_like_a_com_server


@pytest.mark.parametrize("entry", [GS1_DATA, FPGA])
def test_com_server_hint_is_false_for_data(entry):
    assert not entry.looks_like_a_com_server


def test_both_ocx_rows_say_self_reg_is_false():
    # This is why ocx/reg.bat exists. The planner cannot infer registration
    # from the file table for this package.
    assert not OCX_WOW.self_reg
    assert not OCX_NATIVE.self_reg


def test_source_relative_is_posix():
    assert GS1_DATA.source_relative() == PurePosixPath(
        "Matricode/Quattro/Templates/GS1-1.txt"
    )


def test_source_relative_handles_a_parent_segment():
    assert LANG_CHINESE.source_relative() == PurePosixPath("../Release/LangChinese.dll")


def test_source_relative_of_a_row_with_no_source_is_none():
    entry = FileEntry.from_cells(file="x", component="c", file_name="x.dll")
    assert entry.source_relative() is None


def test_a_file_row_with_no_key_is_refused():
    with pytest.raises(ManifestError, match="file row has no key"):
        FileEntry.from_cells(file="", component="c", file_name="x.dll")


def test_a_file_row_with_no_component_is_refused():
    with pytest.raises(ManifestError, match="names no component"):
        FileEntry.from_cells(file="x", component="", file_name="x.dll")


def test_describe_names_the_key_the_target_and_the_source():
    assert GS1_DATA.describe() == (
        "GS11.txt -> GS1-1.txt from Matricode/Quattro/Templates/GS1-1.txt"
    )


def test_describe_says_so_when_there_is_no_source():
    entry = FileEntry.from_cells(file="x", component="c", file_name="x.dll")
    assert entry.describe().endswith("(no source)")


def test_index_by_component():
    grouped = index_by_component([OCX_WOW, OCX_NATIVE, GS1_DATA])
    assert set(grouped) == {"C1Sizer.ocx", "C1Sizer.ocx_1", "GS11.txt"}
    assert [e.file for e in grouped["C1Sizer.ocx_1"]] == ["C1Sizer.ocx"]


def test_index_by_component_sorts_within_a_group():
    a = FileEntry.from_cells(file="zzz", component="shared", file_name="z.dll")
    b = FileEntry.from_cells(file="aaa", component="shared", file_name="a.dll")
    assert [e.file for e in index_by_component([a, b])["shared"]] == ["aaa", "zzz"]


def test_the_ocx_pair_shows_up_as_a_duplicate_target_name():
    found = duplicate_target_names([OCX_WOW, OCX_NATIVE, GS1_DATA])
    assert found == {"C1Sizer.ocx": ["C1Sizer.ocx", "C1Sizer.ocx_1"]}


def test_no_duplicates_when_every_name_is_distinct():
    assert duplicate_target_names([GS1_DATA, LANG_CHINESE, FPGA]) == {}


def test_duplicate_detection_uses_the_long_name():
    a = FileEntry.from_cells(file="a", component="c", file_name="AAAAAA~1.DLL|shared.dll")
    b = FileEntry.from_cells(file="b", component="c", file_name="BBBBBB~1.DLL|shared.dll")
    assert duplicate_target_names([a, b]) == {"shared.dll": ["a", "b"]}
