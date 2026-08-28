from __future__ import annotations

import pytest

from stavewright.util.shortname import (
    DefaultDir,
    ShortNameAllocator,
    format_default_dir,
    is_valid_long_name,
    is_valid_short_name,
    parse_default_dir,
    to_short_name,
)

# Every one of these is a real cell from the S-Jet 600 AIP directory table.
AIP_CELLS = [
    "APPDIR:.",
    "DATAFI~1|Data Files",
    "DESKTO~1|DesktopFolder",
    "FPGAIM~1|FpgaImage",
    "Images",
    "JOBFIL~1|Job Files",
    "Sellenis",
    "S-jet600",
    "SHORTC~1|SHORTCUTDIR",
    "SourceDir",
    "SYSTEM~1|System Test",
    "SYSTEM~1|System64Folder",
    "SYSTEM~2|SystemFolder",
    "TEMPLA~1|Templates",
    "WINDOW~1|WindowsVolume",
    "log",
    "temp",
    "i386",
    "ftdimfr",
]


@pytest.mark.parametrize("cell", AIP_CELLS)
def test_every_shipped_cell_round_trips(cell):
    assert format_default_dir(parse_default_dir(cell)) == cell


def test_bare_name_is_target_and_source():
    entry = parse_default_dir("Images")
    assert entry.target_short == "Images"
    assert entry.target_long is None
    assert entry.target_name == "Images"
    assert entry.source_name == "Images"
    assert not entry.has_source_override


def test_short_and_long_target():
    entry = parse_default_dir("DATAFI~1|Data Files")
    assert entry.target_short == "DATAFI~1"
    assert entry.target_long == "Data Files"
    assert entry.target_name == "Data Files"
    assert entry.source_name == "Data Files"


def test_source_root_sentinel():
    entry = parse_default_dir("APPDIR:.")
    assert entry.target_name == "APPDIR"
    assert entry.source_short == "."
    assert entry.is_source_root
    assert entry.has_source_override


def test_full_four_part_cell():
    entry = parse_default_dir("TEMPLA~1|Templates:TMPL~1|Template Source")
    assert entry.target_name == "Templates"
    assert entry.source_name == "Template Source"
    assert entry.has_source_override
    assert not entry.is_source_root


def test_source_with_only_a_long_name():
    entry = parse_default_dir("Images:|Image Sources")
    assert entry.source_name == "Image Sources"
    assert format_default_dir(entry) == "Images:|Image Sources"


@pytest.mark.parametrize(
    "cell",
    [
        "",
        ":Images",
        "Images:",
        "Images|",
        "A:B:C",
    ],
)
def test_malformed_cells_are_rejected(cell):
    with pytest.raises(ValueError):
        parse_default_dir(cell)


def test_str_uses_the_formatter():
    entry = DefaultDir(target_short="SYSTEM~1", target_long="System Test")
    assert str(entry) == "SYSTEM~1|System Test"


@pytest.mark.parametrize(
    ("long_name", "expected"),
    [
        ("Data Files", "DATAFI~1"),
        ("System Test", "SYSTEM~1"),
        ("FpgaImage", "FPGAIM~1"),
        ("Job Files", "JOBFIL~1"),
        ("WindowsVolume", "WINDOW~1"),
        ("Templates", "TEMPLA~1"),
    ],
)
def test_generated_short_names_match_what_the_aip_stores(long_name, expected):
    assert to_short_name(long_name) == expected


@pytest.mark.parametrize("name", ["Images", "log", "temp", "i386", "ftdimfr", "S-jet600"])
def test_names_that_already_fit_are_left_alone_apart_from_case(name):
    assert to_short_name(name) == name.upper()


def test_extension_is_truncated_to_three():
    # The four character extension makes the whole name illegal, so the stem
    # gets mangled too even though it would have fitted on its own.
    assert to_short_name("pcdc_top.rbfimage") == "PCDC_T~1.RBF"


def test_a_legal_eight_three_name_with_an_extension_is_left_alone():
    assert to_short_name("V4T.exe") == "V4T.EXE"
    assert to_short_name("T1.spf") == "T1.SPF"


def test_long_stem_with_extension_gets_a_tilde():
    assert to_short_name("commissioning-report.json") == "COMMIS~1.JSO"


def test_ordinal_selects_a_different_name():
    assert to_short_name("Data Files", 2) == "DATAFI~2"
    assert to_short_name("Data Files", 12) == "DATAF~12"


def test_spaces_and_illegal_characters_are_dropped():
    assert to_short_name("GS1 Data, v2") == "GS1DAT~1"


def test_ordinal_must_be_positive():
    with pytest.raises(ValueError, match="starts at 1"):
        to_short_name("Data Files", 0)


def test_ordinal_that_leaves_no_stem_is_rejected():
    with pytest.raises(ValueError, match="no room for a stem"):
        to_short_name("Data Files", 10_000_000)


def test_name_with_nothing_usable_is_rejected():
    with pytest.raises(ValueError, match="no usable short name characters"):
        to_short_name("   ")


@pytest.mark.parametrize("name", ["DATAFI~1", "V4T.exe", "log", "PCDC_T~1.RBF"])
def test_valid_short_names(name):
    assert is_valid_short_name(name)


@pytest.mark.parametrize("name", ["Data Files", "toolongname", "a.abcd", "with|pipe", ""])
def test_invalid_short_names(name):
    assert not is_valid_short_name(name)


@pytest.mark.parametrize("name", ["Data Files", "System Test", "S-jet600"])
def test_valid_long_names(name):
    assert is_valid_long_name(name)


@pytest.mark.parametrize("name", ["", "with/slash", "with:colon", "with*star"])
def test_invalid_long_names(name):
    assert not is_valid_long_name(name)


def test_allocator_disambiguates_siblings():
    allocator = ShortNameAllocator()
    assert allocator.allocate("Data Files") == "DATAFI~1"
    assert allocator.allocate("Data Folder") == "DATAFO~1"
    assert allocator.allocate("Data Files Backup") == "DATAFI~2"


def test_allocator_respects_reserved_names():
    allocator = ShortNameAllocator()
    allocator.reserve("DATAFI~1")
    assert allocator.allocate("Data Files") == "DATAFI~2"


def test_allocator_reserve_is_case_insensitive():
    allocator = ShortNameAllocator()
    allocator.reserve("datafi~1")
    assert allocator.allocate("Data Files") == "DATAFI~2"
    assert "DATAFI~2" in allocator.taken


def test_allocator_keeps_a_short_name_that_already_fits():
    allocator = ShortNameAllocator()
    assert allocator.allocate("Images") == "IMAGES"
    assert allocator.allocate("Images") == "IMAGES~2"
