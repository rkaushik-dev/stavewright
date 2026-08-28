from __future__ import annotations

import pytest

from stavewright.config.profiles import (
    describe_profiles,
    is_profile_document,
    load_directory,
    parse_profiles,
    read_profile,
    read_profiles,
)
from stavewright.errors import ConfigError
from stavewright.model.profile import Architecture, TerminalModel

SINGLE = """
terminal_id = "LINE-PC-04"
machine = "s-jet600"
architecture = "x64"
head_count = 2
fpga_revision = "PCDC-1.4"
languages = ["pl-PL", "ru-RU"]
"""

LINE = """
machine = "s-jet600"
architecture = "x64"
fpga_revision = "PCDC-1.4"
languages = ["pl-PL"]

[terminals.LINE-PC-04]
head_count = 2

[terminals.LINE-PC-05]
head_count = 1
hasp_serial = "HASP-0042"

[terminals.LINE-PC-09]
machine = "s-jet-mini"
clear = ["fpga_revision"]
"""

MINI_ONLY = """
[terminals.LINE-PC-09]
machine = "s-jet-mini"
head_count = 1
"""


def write(tmp_path, text, name="line.toml"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_a_single_terminal_file(tmp_path):
    profiles = read_profiles(write(tmp_path, SINGLE))
    assert list(profiles) == ["LINE-PC-04"]
    profile = profiles["LINE-PC-04"]
    assert profile.head_count == 2
    assert profile.machine is TerminalModel.SJET_600
    assert profile.language_ids == (1045, 1049)


def test_a_single_terminal_file_needs_no_name(tmp_path):
    assert read_profile(write(tmp_path, SINGLE)).terminal_id == "LINE-PC-04"


def test_a_line_file_holds_several(tmp_path):
    profiles = read_profiles(write(tmp_path, LINE))
    assert sorted(profiles) == ["LINE-PC-04", "LINE-PC-05", "LINE-PC-09"]


def test_shared_values_reach_every_terminal(tmp_path):
    profiles = read_profiles(write(tmp_path, LINE))
    for profile in profiles.values():
        assert profile.architecture is Architecture.X64
    assert profiles["LINE-PC-04"].language_ids == (1045,)
    assert profiles["LINE-PC-05"].language_ids == (1045,)


def test_a_terminal_overrides_what_it_needs(tmp_path):
    profiles = read_profiles(write(tmp_path, LINE))
    assert profiles["LINE-PC-04"].head_count == 2
    assert profiles["LINE-PC-05"].head_count == 1
    assert profiles["LINE-PC-05"].hasp_serial == "HASP-0042"
    assert profiles["LINE-PC-04"].hasp_serial is None


def test_a_terminal_can_override_the_machine(tmp_path):
    profiles = read_profiles(write(tmp_path, LINE))
    mini = profiles["LINE-PC-09"]
    assert mini.machine is TerminalModel.SJET_MINI
    assert mini.fpga_revision is None


def test_the_terminal_id_comes_from_the_table_name(tmp_path):
    profiles = read_profiles(write(tmp_path, MINI_ONLY))
    assert profiles["LINE-PC-09"].terminal_id == "LINE-PC-09"


def test_naming_a_terminal_in_a_line_file(tmp_path):
    profile = read_profile(write(tmp_path, LINE), "LINE-PC-05")
    assert profile.head_count == 1


def test_not_naming_one_in_a_line_file_is_refused(tmp_path):
    with pytest.raises(ConfigError, match="holds several terminals") as excinfo:
        read_profile(write(tmp_path, LINE))
    assert excinfo.value.context["known"] == ["LINE-PC-04", "LINE-PC-05", "LINE-PC-09"]


def test_naming_a_terminal_that_is_not_there(tmp_path):
    with pytest.raises(ConfigError, match="no such terminal") as excinfo:
        read_profile(write(tmp_path, LINE), "LINE-PC-99")
    assert excinfo.value.context["terminal"] == "LINE-PC-99"


def test_a_file_with_neither_form_is_refused(tmp_path):
    with pytest.raises(ConfigError, match="no terminals and no terminal_id"):
        read_profiles(write(tmp_path, 'machine = "s-jet600"\n'))


def test_a_terminals_entry_that_is_not_a_table_is_refused():
    with pytest.raises(ConfigError, match="terminals entry is not a table"):
        parse_profiles({"terminals": "LINE-PC-04"})


def test_a_terminal_entry_that_is_not_a_table_is_refused():
    with pytest.raises(ConfigError, match="terminal entry is not a table"):
        parse_profiles({"terminals": {"LINE-PC-04": "s-jet600"}})


def test_an_invalid_profile_is_refused(tmp_path):
    text = SINGLE.replace("head_count = 2", "head_count = 9")
    with pytest.raises(ConfigError, match="not valid") as excinfo:
        read_profiles(write(tmp_path, text))
    assert excinfo.value.context["where"].endswith("line.toml")


def test_an_invalid_profile_in_a_line_file_names_the_terminal(tmp_path):
    text = LINE.replace("[terminals.LINE-PC-04]\nhead_count = 2", "[terminals.LINE-PC-04]\nhead_count = 9")
    with pytest.raises(ConfigError, match="not valid") as excinfo:
        read_profiles(write(tmp_path, text))
    assert "LINE-PC-04" in excinfo.value.context["where"]


def test_a_missing_file_is_reported(tmp_path):
    with pytest.raises(ConfigError, match="cannot read profile file"):
        read_profiles(tmp_path / "nope.toml")


def test_a_file_that_is_not_toml_is_reported(tmp_path):
    with pytest.raises(ConfigError, match="not valid TOML"):
        read_profiles(write(tmp_path, "this is = = not toml"))


def test_loading_a_directory(tmp_path):
    write(tmp_path, LINE, "line-one.toml")
    write(tmp_path, MINI_ONLY.replace("LINE-PC-09", "LINE-PC-20"), "line-two.toml")
    profiles = load_directory(tmp_path)
    assert sorted(profiles) == ["LINE-PC-04", "LINE-PC-05", "LINE-PC-09", "LINE-PC-20"]


def test_loading_a_directory_ignores_other_files(tmp_path):
    write(tmp_path, LINE, "line.toml")
    (tmp_path / "notes.md").write_text("not a profile", encoding="utf-8")
    assert len(load_directory(tmp_path)) == 3


def test_a_terminal_declared_twice_is_refused(tmp_path):
    write(tmp_path, MINI_ONLY, "one.toml")
    write(tmp_path, MINI_ONLY, "two.toml")
    with pytest.raises(ConfigError, match="declare the same terminal") as excinfo:
        load_directory(tmp_path)
    assert excinfo.value.context["terminal"] == "LINE-PC-09"
    assert excinfo.value.context["first"].endswith("one.toml")
    assert excinfo.value.context["second"].endswith("two.toml")


def test_loading_a_directory_that_is_not_there(tmp_path):
    with pytest.raises(ConfigError, match="does not exist"):
        load_directory(tmp_path / "nope")


def test_loading_an_empty_directory(tmp_path):
    assert load_directory(tmp_path) == {}


def test_describe_lists_them_in_order(tmp_path):
    profiles = read_profiles(write(tmp_path, LINE))
    lines = describe_profiles(profiles)
    assert lines[0].startswith("LINE-PC-04")
    assert lines[1].startswith("LINE-PC-05")
    assert lines[2].startswith("LINE-PC-09")


def test_describe_of_nothing():
    assert describe_profiles({}) == []


def test_clear_accepts_a_single_name():
    profiles = parse_profiles(
        {
            "machine": "s-jet600",
            "fpga_revision": "PCDC-1.4",
            "terminals": {
                "LINE-PC-09": {"machine": "s-jet-mini", "clear": "fpga_revision"}
            },
        }
    )
    assert profiles["LINE-PC-09"].fpga_revision is None


def test_clear_naming_something_that_is_not_a_field_is_refused():
    with pytest.raises(ConfigError, match="not a profile field") as excinfo:
        parse_profiles(
            {"terminals": {"LINE-PC-09": {"machine": "s-jet-mini", "clear": ["nonsense"]}}}
        )
    assert excinfo.value.context["unknown"] == ["nonsense"]


def test_clear_that_is_not_a_list_is_refused():
    with pytest.raises(ConfigError, match="must be a list of field names"):
        parse_profiles({"terminals": {"LINE-PC-09": {"clear": 42}}})


def test_clearing_something_that_was_not_inherited_is_harmless():
    profiles = parse_profiles(
        {"terminals": {"LINE-PC-09": {"machine": "s-jet-mini", "clear": ["hasp_serial"]}}}
    )
    assert profiles["LINE-PC-09"].hasp_serial is None


def test_loading_a_directory_skips_the_settings_file(tmp_path):
    # A bench keeps stavewright.toml beside its profiles. Reading it as a profile
    # fails with a message about missing terminals that helps nobody.
    from stavewright.config.settings import CONFIG_FILE_NAME

    write(tmp_path, MINI_ONLY, "line.toml")
    write(tmp_path, 'staging_root = "_staging"\n', CONFIG_FILE_NAME)
    assert sorted(load_directory(tmp_path)) == ["LINE-PC-09"]


def test_a_scan_leaves_the_roster_alone(tmp_path):
    write(tmp_path, LINE, "line.toml")
    (tmp_path / "roster.toml").write_text('plant = "Wrexham"\n', encoding="utf-8")
    assert sorted(load_directory(tmp_path)) == ["LINE-PC-04", "LINE-PC-05", "LINE-PC-09"]


def test_a_scan_leaves_the_calendar_alone(tmp_path):
    write(tmp_path, LINE, "line.toml")
    (tmp_path / "calendar.toml").write_text(
        "[[window]]\nstart = 2025-06-02T22:00:00Z\nend = 2025-06-03T02:00:00Z\n",
        encoding="utf-8",
    )
    assert sorted(load_directory(tmp_path)) == ["LINE-PC-04", "LINE-PC-05", "LINE-PC-09"]


def test_a_named_path_still_refuses_a_document_that_is_not_a_profile(tmp_path):
    (tmp_path / "roster.toml").write_text('plant = "Wrexham"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="no terminals and no terminal_id"):
        read_profiles(tmp_path / "roster.toml")


def test_a_scan_still_refuses_a_document_that_is_not_toml(tmp_path):
    write(tmp_path, LINE, "line.toml")
    (tmp_path / "broken.toml").write_text("plant = ", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid TOML"):
        load_directory(tmp_path)


def test_a_scan_still_refuses_a_profile_that_does_not_validate(tmp_path):
    write(tmp_path, LINE, "line.toml")
    write(tmp_path, 'terminal_id = "LINE-PC-77"\nmachine = "s-jet-9000"\n', "odd.toml")
    with pytest.raises(ConfigError, match="terminal profile is not valid"):
        load_directory(tmp_path)


def test_a_document_declaring_a_terminal_id_is_a_profile():
    assert is_profile_document({"terminal_id": "LINE-PC-04"})


def test_a_document_declaring_a_terminals_table_is_a_profile():
    assert is_profile_document({"terminals": {"LINE-PC-04": {}}})


def test_a_roster_is_not_a_profile():
    assert not is_profile_document({"plant": "Wrexham", "line": {}})


def test_a_calendar_is_not_a_profile():
    assert not is_profile_document({"window": []})


def test_a_settings_file_is_not_a_profile():
    assert not is_profile_document({"staging_root": "_staging"})


def test_an_empty_document_is_not_a_profile():
    assert not is_profile_document({})


def test_a_mistyped_key_means_the_file_is_ignored(tmp_path):
    # The cost of recognising profiles by content. It is documented on
    # load_directory rather than left for somebody to find.
    write(tmp_path, LINE, "line.toml")
    write(tmp_path, 'terminal_ids = "LINE-PC-77"\n', "typo.toml")
    assert "LINE-PC-77" not in load_directory(tmp_path)
