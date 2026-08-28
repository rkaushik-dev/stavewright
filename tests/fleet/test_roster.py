from __future__ import annotations

import tomllib
from datetime import date

import pytest

from stavewright.errors import ConfigError
from stavewright.fleet.roster import (
    Criticality,
    Roster,
    ShiftPattern,
    parse_roster,
    read_roster,
)
from stavewright.model.profile import TerminalProfile

TERMINALS = ["LINE-PC-04", "LINE-PC-05", "LINE-PC-06", "BENCH-01"]

DOCUMENT = """
plant = "Wrexham"

[line.export]
criticality = "critical"
shift_pattern = "continuous"

[line.export.terminal.LINE-PC-04]
position = 1
commissioned = 2024-11-03

[line.export.terminal.LINE-PC-05]
position = 2

[line.sample]
criticality = "normal"

[line.sample.terminal.LINE-PC-06]
position = 1
commissioned = 2025-02-17

[line.bench]
criticality = "spare"

[line.bench.terminal.BENCH-01]
position = 1
"""


def profiles(*terminal_ids: str) -> dict[str, TerminalProfile]:
    names = terminal_ids or tuple(TERMINALS)
    return {
        name: TerminalProfile(terminal_id=name, fpga_revision="PCDC-1.4") for name in names
    }


def roster(document: str = DOCUMENT, **kwargs) -> Roster:
    return parse_roster(tomllib.loads(document), profiles(), **kwargs)


def test_the_plant_comes_through():
    assert roster().plant == "Wrexham"


def test_every_terminal_is_there():
    assert roster().terminal_ids == sorted(TERMINALS)


def test_the_lines_are_listed_sorted():
    assert roster().lines == ["bench", "export", "sample"]


def test_membership():
    built = roster()
    assert "LINE-PC-04" in built
    assert "LINE-PC-99" not in built


def test_length():
    assert len(roster()) == 4


def test_iterating_yields_entries():
    assert {entry.terminal_id for entry in roster()} == set(TERMINALS)


def test_an_entry_carries_its_profile():
    entry = roster().get("LINE-PC-04")
    assert entry.profile.terminal_id == "LINE-PC-04"


def test_line_and_position_come_from_the_roster_not_the_profile():
    entry = roster().get("LINE-PC-05")
    assert entry.line == "export"
    assert entry.position == 2


def test_criticality_is_inherited_from_the_line():
    assert roster().get("LINE-PC-05").criticality is Criticality.CRITICAL
    assert roster().get("BENCH-01").criticality is Criticality.SPARE


def test_shift_pattern_is_inherited_too():
    assert roster().get("LINE-PC-04").shift_pattern is ShiftPattern.CONTINUOUS


def test_shift_pattern_defaults_to_day():
    assert roster().get("LINE-PC-06").shift_pattern is ShiftPattern.DAY


def test_criticality_defaults_to_normal():
    assert roster().get("LINE-PC-06").criticality is Criticality.NORMAL


def test_a_commissioning_date_is_read_as_a_date():
    assert roster().get("LINE-PC-04").commissioned == date(2024, 11, 3)


def test_a_terminal_with_no_date_has_never_been_commissioned():
    assert roster().get("LINE-PC-05").commissioned is None
    assert not roster().get("LINE-PC-05").is_commissioned


def test_asking_for_an_unknown_terminal_says_what_it_knows():
    with pytest.raises(ConfigError) as caught:
        roster().get("LINE-PC-99")
    assert "LINE-PC-04" in caught.value.context["known"]


def test_a_line_is_returned_upstream_first():
    order = [entry.terminal_id for entry in roster().on_line("export")]
    assert order == ["LINE-PC-04", "LINE-PC-05"]


def test_a_line_nobody_named_is_empty():
    assert roster().on_line("packing") == []


def test_selecting_by_criticality():
    found = roster().of_criticality(Criticality.CRITICAL)
    assert {entry.terminal_id for entry in found} == {"LINE-PC-04", "LINE-PC-05"}


def test_working_order_puts_the_spare_first_and_the_export_line_last():
    order = [entry.terminal_id for entry in roster().in_working_order()]
    assert order == ["BENCH-01", "LINE-PC-06", "LINE-PC-04", "LINE-PC-05"]


def test_uncommissioned_machines_come_back_in_working_order():
    order = [entry.terminal_id for entry in roster().uncommissioned()]
    assert order == ["BENCH-01", "LINE-PC-05"]


def test_what_feeds_a_machine():
    upstream = roster().upstream_of("LINE-PC-05")
    assert [entry.terminal_id for entry in upstream] == ["LINE-PC-04"]


def test_nothing_feeds_the_first_machine_on_a_line():
    assert roster().upstream_of("LINE-PC-04") == []


def test_upstream_comes_back_nearest_first():
    document = DOCUMENT + """
[line.export.terminal.BENCH-01]
position = 3
"""
    built = parse_roster(tomllib.loads(document), profiles())
    upstream = [entry.terminal_id for entry in built.upstream_of("BENCH-01")]
    assert upstream == ["LINE-PC-05", "LINE-PC-04"]


def test_describing_the_plant():
    lines = roster().describe()
    assert lines[0].startswith("BENCH-01 on bench at 1")
    assert "last commissioned never" in lines[0]
    assert "last commissioned 2024-11-03" in "\n".join(lines)


def test_criticality_ranks_least_disruptive_first():
    ranks = [Criticality.SPARE.rank, Criticality.NORMAL.rank, Criticality.CRITICAL.rank]
    assert ranks == sorted(ranks)


def test_only_one_critical_machine_may_be_down_at_a_time():
    assert Criticality.CRITICAL.max_concurrent == 1
    assert Criticality.NORMAL.max_concurrent > 1


def test_a_continuous_line_has_no_planned_window():
    assert not ShiftPattern.CONTINUOUS.has_planned_downtime
    assert ShiftPattern.TWO_SHIFT.has_planned_downtime


def test_a_roster_with_no_plant_is_refused():
    with pytest.raises(ConfigError, match="does not name its plant"):
        parse_roster({"line": {}}, profiles())


def test_a_blank_plant_is_refused():
    with pytest.raises(ConfigError, match="does not name its plant"):
        parse_roster({"plant": "   "}, profiles())


def test_a_roster_with_no_lines_is_empty_rather_than_an_error():
    built = parse_roster({"plant": "Wrexham"}, profiles())
    assert len(built) == 0
    assert built.lines == []


def test_a_line_that_is_not_a_table_is_refused():
    with pytest.raises(ConfigError, match="line export is not a table"):
        parse_roster({"plant": "W", "line": {"export": "critical"}}, profiles())


def test_a_terminal_that_is_not_a_table_is_refused():
    payload = {"plant": "W", "line": {"export": {"terminal": {"LINE-PC-04": 1}}}}
    with pytest.raises(ConfigError, match="terminal LINE-PC-04 is not a table"):
        parse_roster(payload, profiles())


def test_a_terminal_with_no_profile_is_refused():
    document = DOCUMENT.replace("LINE-PC-06", "LINE-PC-77")
    with pytest.raises(ConfigError) as caught:
        parse_roster(tomllib.loads(document), profiles())
    assert caught.value.context["terminal"] == "LINE-PC-77"
    assert "LINE-PC-04" in caught.value.context["known"]


def test_an_unknown_criticality_lists_the_ones_it_knows():
    payload = {"plant": "W", "line": {"export": {"criticality": "urgent"}}}
    with pytest.raises(ConfigError) as caught:
        parse_roster(payload, profiles())
    assert caught.value.context["known"] == ["critical", "normal", "spare"]


def test_an_unknown_shift_pattern_is_refused():
    payload = {"plant": "W", "line": {"export": {"shift_pattern": "nights"}}}
    with pytest.raises(ConfigError, match="shiftpattern"):
        parse_roster(payload, profiles())


@pytest.mark.parametrize("value", [None, 0, -1, "1", 1.0, True])
def test_a_bad_position_is_refused(value):
    payload = {
        "plant": "W",
        "line": {"export": {"terminal": {"LINE-PC-04": {"position": value}}}},
    }
    with pytest.raises(ConfigError, match="position must be a whole number"):
        parse_roster(payload, profiles())


def test_two_terminals_cannot_share_a_position():
    document = DOCUMENT.replace("""
[line.export.terminal.LINE-PC-05]
position = 2
""", """
[line.export.terminal.LINE-PC-05]
position = 1
""")
    with pytest.raises(ConfigError) as caught:
        parse_roster(tomllib.loads(document), profiles())
    assert caught.value.context["first"] == "LINE-PC-04"
    assert caught.value.context["second"] == "LINE-PC-05"


def test_the_same_position_on_two_lines_is_fine():
    assert roster().on_line("export")[0].position == roster().on_line("sample")[0].position


def test_a_commissioned_value_that_is_not_a_date_is_refused():
    payload = {
        "plant": "W",
        "line": {
            "export": {"terminal": {"LINE-PC-04": {"position": 1, "commissioned": "yesterday"}}}
        },
    }
    with pytest.raises(ConfigError, match="commissioned must be a date"):
        parse_roster(payload, profiles())


def test_reading_a_roster_from_disk(tmp_path):
    path = tmp_path / "roster.toml"
    path.write_text(DOCUMENT, encoding="utf-8")
    assert read_roster(path, profiles()).plant == "Wrexham"


def test_a_missing_roster_file_says_so(tmp_path):
    with pytest.raises(ConfigError, match="cannot read roster file"):
        read_roster(tmp_path / "gone.toml", profiles())


def test_a_roster_that_is_not_toml_says_so(tmp_path):
    path = tmp_path / "roster.toml"
    path.write_text("plant = ", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid TOML"):
        read_roster(path, profiles())


def test_the_error_names_the_file_it_was_reading(tmp_path):
    path = tmp_path / "roster.toml"
    path.write_text("line = {}", encoding="utf-8")
    with pytest.raises(ConfigError) as caught:
        read_roster(path, profiles())
    assert caught.value.context["where"] == str(path)
