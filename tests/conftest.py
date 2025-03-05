"""Shared fixtures.

Wherever a fixture stands in for something out of the shipped Advanced
Installer project, it is copied from ``setup.aip`` verbatim rather than
invented. A test that passes against made up rows tells you nothing about the
package we actually ship.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from v4t_setup.model.directory import DirectoryRow, DirectoryTable
from v4t_setup.model.profile import Architecture, TerminalModel, TerminalProfile

REPO_ROOT = Path(__file__).resolve().parents[1]
AIP_PATH = REPO_ROOT / "setup.aip"

# Directory, DefaultDir, Directory_Parent, IsPseudoRoot. Straight out of the
# MsiDirsComponent table in setup.aip.
AIP_DIRECTORY_ROWS: list[tuple[str, str, str | None, bool]] = [
    ("TARGETDIR", "SourceDir", None, False),
    ("APPDIR", "APPDIR:.", "TARGETDIR", True),
    ("DesktopFolder", "DESKTO~1|DesktopFolder", "TARGETDIR", True),
    ("SHORTCUTDIR", "SHORTC~1|SHORTCUTDIR", "TARGETDIR", True),
    ("System64Folder", "SYSTEM~1|System64Folder", "TARGETDIR", True),
    ("SystemFolder", "SYSTEM~2|SystemFolder", "TARGETDIR", True),
    ("WindowsVolume", "WINDOW~1|WindowsVolume", "TARGETDIR", True),
    ("Matricode_Dir", "Sellenis", "WindowsVolume", False),
    ("Quattro_Dir", "S-jet600", "Matricode_Dir", False),
    ("FpgaImage_Dir", "FPGAIM~1|FpgaImage", "Quattro_Dir", False),
    ("Report_1_Dir", "Report", "Quattro_Dir", False),
    ("System_Dir", "System", "Quattro_Dir", False),
    ("Templates_Dir", "TEMPLA~1|Templates", "Quattro_Dir", False),
    ("Matricode_1_Dir", "Sellenis", "Templates_Dir", False),
    ("SystemTest_Dir", "SYSTEM~1|System Test", "Templates_Dir", False),
    ("ftdimfr_1_Dir", "ftdimfr", "Quattro_Dir", False),
    ("log_1_Dir", "log", "Quattro_Dir", False),
    ("temp_1_Dir", "temp", "Matricode_Dir", False),
    ("DataFiles_Dir", "DATAFI~1|Data Files", "APPDIR", False),
    ("Images_Dir", "Images", "APPDIR", False),
    ("JobFiles_Dir", "JOBFIL~1|Job Files", "APPDIR", False),
    ("ftdimfr_Dir", "ftdimfr", "APPDIR", False),
    ("i386_Dir", "i386", "ftdimfr_Dir", False),
    ("log_Dir", "log", "APPDIR", False),
    ("temp_Dir", "temp", "APPDIR", False),
]


@pytest.fixture
def aip_path() -> Path:
    """Absolute path to the shipped project file."""
    return AIP_PATH


@pytest.fixture
def directory_rows() -> list[DirectoryRow]:
    """The shipped directory table as row objects."""
    return [
        DirectoryRow.from_cells(directory, default_dir, parent, pseudo)
        for directory, default_dir, parent, pseudo in AIP_DIRECTORY_ROWS
    ]


@pytest.fixture
def directory_table(directory_rows: list[DirectoryRow]) -> DirectoryTable:
    """The shipped directory table, ready to resolve."""
    return DirectoryTable.from_rows(directory_rows)


@pytest.fixture
def profile() -> TerminalProfile:
    """A plain 64 bit S-Jet 600 with one head, English only."""
    return TerminalProfile(
        terminal_id="LINE-PC-04",
        machine=TerminalModel.SJET_600,
        architecture=Architecture.X64,
        head_count=1,
        fpga_revision="PCDC-1.4",
    )


@pytest.fixture
def profile_x86() -> TerminalProfile:
    """The same terminal on a 32 bit operating system."""
    return TerminalProfile(
        terminal_id="LINE-PC-02",
        machine=TerminalModel.SJET_600,
        architecture=Architecture.X86,
        head_count=1,
        fpga_revision="PCDC-1.4",
    )


@pytest.fixture
def profile_mini() -> TerminalProfile:
    """A Mini, which has no FPGA controller and drives a single head."""
    return TerminalProfile(
        terminal_id="LINE-PC-09",
        machine=TerminalModel.SJET_MINI,
        architecture=Architecture.X64,
        head_count=1,
    )
