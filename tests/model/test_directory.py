from __future__ import annotations

from pathlib import PureWindowsPath

import pytest

from v4t_setup.errors import DirectoryCycleError, DirectoryResolutionError
from v4t_setup.model.directory import DirectoryRow, DirectoryTable


def win(text: str) -> PureWindowsPath:
    return PureWindowsPath(text)


def test_table_holds_every_shipped_row(directory_table, directory_rows):
    assert len(directory_table) == len(directory_rows)
    assert "Quattro_Dir" in directory_table
    assert "NoSuchDir" not in directory_table


def test_duplicate_keys_are_refused(directory_rows):
    with pytest.raises(DirectoryResolutionError, match="appears twice"):
        DirectoryTable.from_rows([*directory_rows, directory_rows[0]])


def test_getitem_reports_a_missing_row(directory_table):
    with pytest.raises(DirectoryResolutionError, match="no such directory row"):
        directory_table["NoSuchDir"]


def test_keys_are_sorted(directory_table):
    keys = directory_table.keys()
    assert keys == sorted(keys)


@pytest.mark.parametrize(
    ("directory", "expected"),
    [
        ("WindowsVolume", r"C:\ "),
        ("Matricode_Dir", r"C:\Sellenis"),
        ("Quattro_Dir", r"C:\Sellenis\S-jet600"),
        ("FpgaImage_Dir", r"C:\Sellenis\S-jet600\FpgaImage"),
        ("Templates_Dir", r"C:\Sellenis\S-jet600\Templates"),
        ("Matricode_1_Dir", r"C:\Sellenis\S-jet600\Templates\Sellenis"),
        ("SystemTest_Dir", r"C:\Sellenis\S-jet600\Templates\System Test"),
        ("System_Dir", r"C:\Sellenis\S-jet600\System"),
        ("Report_1_Dir", r"C:\Sellenis\S-jet600\Report"),
        ("log_1_Dir", r"C:\Sellenis\S-jet600\log"),
        ("ftdimfr_1_Dir", r"C:\Sellenis\S-jet600\ftdimfr"),
        ("temp_1_Dir", r"C:\Sellenis\temp"),
    ],
)
def test_the_product_tree_resolves_the_way_the_installer_builds_it(
    directory_table, profile, directory, expected
):
    assert directory_table.resolve(directory, profile) == win(expected.strip())


@pytest.mark.parametrize(
    ("directory", "expected"),
    [
        ("APPDIR", r"C:\Sellenis\S-jet600"),
        ("DataFiles_Dir", r"C:\Sellenis\S-jet600\Data Files"),
        ("Images_Dir", r"C:\Sellenis\S-jet600\Images"),
        ("JobFiles_Dir", r"C:\Sellenis\S-jet600\Job Files"),
        ("ftdimfr_Dir", r"C:\Sellenis\S-jet600\ftdimfr"),
        ("i386_Dir", r"C:\Sellenis\S-jet600\ftdimfr\i386"),
        ("log_Dir", r"C:\Sellenis\S-jet600\log"),
        ("temp_Dir", r"C:\Sellenis\S-jet600\temp"),
    ],
)
def test_the_appdir_branch_resolves(directory_table, profile, directory, expected):
    assert directory_table.resolve(directory, profile) == win(expected)


def test_long_names_win_over_short_ones(directory_table, profile):
    resolved = directory_table.resolve("DataFiles_Dir", profile)
    assert resolved.name == "Data Files"
    assert "DATAFI~1" not in str(resolved)


def test_system_folder_is_redirected_for_a_32_bit_component(directory_table, profile):
    assert directory_table.resolve("SystemFolder", profile) == win(r"C:\Windows\SysWOW64")


def test_system_folder_is_not_redirected_for_a_native_component(directory_table, profile):
    assert directory_table.resolve("SystemFolder", profile, native=True) == win(
        r"C:\Windows\System32"
    )


def test_system64_folder_ignores_the_native_flag(directory_table, profile):
    for native in (False, True):
        assert directory_table.resolve("System64Folder", profile, native=native) == win(
            r"C:\Windows\System32"
        )


def test_on_x86_both_system_roots_are_system32(directory_table, profile_x86):
    assert directory_table.resolve("SystemFolder", profile_x86) == win(r"C:\Windows\System32")
    assert directory_table.resolve("System64Folder", profile_x86) == win(r"C:\Windows\System32")


def test_the_ocx_pair_lands_in_two_places_on_x64(directory_table, profile):
    wow = directory_table.resolve("SystemFolder", profile) / "C1Sizer.ocx"
    native = directory_table.resolve("System64Folder", profile) / "C1Sizer.ocx"
    assert wow != native
    assert wow == win(r"C:\Windows\SysWOW64\C1Sizer.ocx")


def test_the_ocx_pair_collides_on_x86(directory_table, profile_x86):
    wow = directory_table.resolve("SystemFolder", profile_x86) / "C1Sizer.ocx"
    native = directory_table.resolve("System64Folder", profile_x86) / "C1Sizer.ocx"
    assert wow == native


def test_shortcut_and_desktop_roots_come_off_the_profile(directory_table, profile):
    assert directory_table.resolve("DesktopFolder", profile) == profile.desktop_folder
    assert directory_table.resolve("SHORTCUTDIR", profile) == profile.shortcut_folder


def test_a_different_windows_volume_moves_the_whole_tree(directory_table, profile):
    moved = profile.model_copy(
        update={
            "windows_volume": win("D:\\"),
            "windows_directory": win(r"D:\Windows"),
        }
    )
    assert directory_table.resolve("Quattro_Dir", moved) == win(r"D:\Sellenis\S-jet600")
    assert directory_table.resolve("SystemFolder", moved) == win(r"D:\Windows\SysWOW64")


def test_unknown_directory_is_reported(directory_table, profile):
    with pytest.raises(DirectoryResolutionError, match="parent directory is not in the table"):
        directory_table.resolve("NoSuchDir", profile)


def test_missing_parent_is_reported(profile):
    table = DirectoryTable.from_rows(
        [DirectoryRow.from_cells("Orphan_Dir", "Orphan", "Gone_Dir")]
    )
    with pytest.raises(DirectoryResolutionError) as excinfo:
        table.resolve("Orphan_Dir", profile)
    assert excinfo.value.context["missing"] == "Gone_Dir"


def test_a_chain_that_never_reaches_a_root_is_reported(profile):
    table = DirectoryTable.from_rows([DirectoryRow.from_cells("Lonely_Dir", "Lonely")])
    with pytest.raises(DirectoryResolutionError, match="without reaching a pseudo root"):
        table.resolve("Lonely_Dir", profile)


def test_a_two_row_cycle_is_caught(profile):
    table = DirectoryTable.from_rows(
        [
            DirectoryRow.from_cells("A_Dir", "A", "B_Dir"),
            DirectoryRow.from_cells("B_Dir", "B", "A_Dir"),
        ]
    )
    with pytest.raises(DirectoryCycleError, match="loops") as excinfo:
        table.resolve("A_Dir", profile)
    assert excinfo.value.context["cycle"] == "A_Dir -> B_Dir -> A_Dir"


def test_a_self_referencing_row_is_caught(profile):
    table = DirectoryTable.from_rows([DirectoryRow.from_cells("Loop_Dir", "Loop", "Loop_Dir")])
    with pytest.raises(DirectoryCycleError):
        table.resolve("Loop_Dir", profile)


def test_resolve_all_covers_every_row(directory_table, profile):
    resolved = directory_table.resolve_all(profile)
    assert set(resolved) == set(directory_table.keys())
    assert resolved["Quattro_Dir"] == win(r"C:\Sellenis\S-jet600")


def test_resolve_all_honours_the_native_flag(directory_table, profile):
    assert directory_table.resolve_all(profile)["SystemFolder"] == win(r"C:\Windows\SysWOW64")
    assert directory_table.resolve_all(profile, native=True)["SystemFolder"] == win(
        r"C:\Windows\System32"
    )


def test_is_pseudo_root_reads_the_flag_and_the_well_known_set(directory_table):
    assert directory_table.is_pseudo_root("APPDIR")
    assert directory_table.is_pseudo_root("TARGETDIR")
    assert not directory_table.is_pseudo_root("Quattro_Dir")


def test_children_of(directory_table):
    assert directory_table.children_of("Quattro_Dir") == [
        "FpgaImage_Dir",
        "Report_1_Dir",
        "System_Dir",
        "Templates_Dir",
        "ftdimfr_1_Dir",
        "log_1_Dir",
    ]


def test_children_of_a_leaf_is_empty(directory_table):
    assert directory_table.children_of("i386_Dir") == []


def test_the_shipped_table_has_no_orphans(directory_table):
    assert directory_table.orphans() == []


def test_orphans_finds_a_dangling_parent(directory_rows):
    table = DirectoryTable.from_rows(
        [*directory_rows, DirectoryRow.from_cells("Orphan_Dir", "Orphan", "Gone_Dir")]
    )
    assert table.orphans() == ["Orphan_Dir"]


def test_source_path_walks_the_build_tree(directory_table):
    root = win(r"D:\build\sjet600")
    assert directory_table.source_path("i386_Dir", root) == win(r"D:\build\sjet600\ftdimfr\i386")


def test_source_path_stops_at_the_project_root_sentinel(directory_table):
    root = win(r"D:\build\sjet600")
    assert directory_table.source_path("APPDIR", root) == root


def test_source_path_uses_source_names_not_target_names(directory_table):
    root = win(r"D:\build")
    assert directory_table.source_path("DataFiles_Dir", root) == win(r"D:\build\Data Files")
