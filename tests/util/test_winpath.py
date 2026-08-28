from __future__ import annotations

import pytest

from stavewright.model.registry import RegistryRoot
from stavewright.target.base import ShortcutSpec
from stavewright.util.winpath import (
    DEFAULT_VALUE_NAME,
    fold_key,
    fold_path,
    fold_value_name,
    registry_path,
    shortcut_key,
)


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (r"C:\Sellenis\S-jet600", "c:/sellenis/s-jet600"),
        ("C:/Sellenis/S-jet600", "c:/sellenis/s-jet600"),
        (r"C:\SELLENIS\s-JET600", "c:/sellenis/s-jet600"),
        (r"C:\Sellenis\S-jet600\\", "c:/sellenis/s-jet600"),
        ("C:/Sellenis/S-jet600/", "c:/sellenis/s-jet600"),
    ],
)
def test_paths_that_windows_treats_as_one_fold_to_one_key(given, expected):
    assert fold_path(given) == expected


def test_the_root_survives():
    assert fold_path("/") == "/"
    assert fold_path("\\") == "/"


def test_an_empty_path_does_not_fold_to_an_empty_key():
    # A blank dictionary key is a bug that only shows up as two unrelated
    # things sharing an entry.
    assert fold_path("") == "/"


def test_a_drive_letter_keeps_its_colon():
    assert fold_path("C:") == "c:"


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (r"Software\Matricode\V4T", r"software\matricode\v4t"),
        ("Software/Matricode/V4T", r"software\matricode\v4t"),
        ("\\Software\\Matricode\\V4T\\", r"software\matricode\v4t"),
        ("SOFTWARE\\MATRICODE", r"software\matricode"),
    ],
)
def test_registry_keys_fold_the_other_way(given, expected):
    assert fold_key(given) == expected


def test_an_empty_registry_key():
    assert fold_key("") == ""


def test_a_value_name_folds_to_lowercase():
    assert fold_value_name("Version") == "version"


def test_the_unnamed_value_folds_to_the_empty_string():
    assert fold_value_name(None) == ""
    assert fold_value_name("") == ""


def test_a_shortcut_is_keyed_on_its_directory_and_name():
    spec = ShortcutSpec(name="V4T", directory=r"C:\Users\Public\Desktop", target="x")
    assert shortcut_key(spec) == "c:/users/public/desktop/v4t"


def test_two_spellings_of_one_shortcut_agree():
    one = ShortcutSpec(name="V4T", directory=r"C:\Users\Public\Desktop", target="x")
    other = ShortcutSpec(name="v4t", directory="C:/USERS/Public/Desktop/", target="y")
    assert shortcut_key(one) == shortcut_key(other)


def test_a_registry_path_is_for_reading_not_for_comparing():
    written = registry_path(RegistryRoot.LOCAL_MACHINE, r"Software\Matricode\V4T", "Version")
    assert written.endswith(r"Software\Matricode\V4T\Version")
    assert written != written.lower()


def test_the_unnamed_value_is_written_out():
    written = registry_path(RegistryRoot.LOCAL_MACHINE, r"Software\Matricode", None)
    assert written.endswith(DEFAULT_VALUE_NAME)


def test_the_memory_target_and_the_snapshot_agree_on_a_path():
    # They had byte identical copies of this and a third would have appeared
    # the next time somebody wrote a target.
    from stavewright.target.memory import MemoryTarget
    from stavewright.verify.snapshot import Snapshot

    target = MemoryTarget()
    target.write_file(r"C:\Sellenis\S-jet600\V4T.exe", b"MZ")
    assert target.path_exists("c:/sellenis/s-jet600/v4t.exe")

    snapshot = Snapshot(files={fold_path(r"C:\Sellenis\S-jet600\V4T.exe"): "sha256:x"})
    assert snapshot.path_exists("C:/SELLENIS/S-JET600/V4T.EXE")
