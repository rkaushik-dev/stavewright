from __future__ import annotations

import pytest

from v4t_setup.errors import Gs1Error
from v4t_setup.gs1.ai import (
    CharacterSet,
    DataKind,
    all_identifiers,
    decimal_position,
    is_known,
    longest_prefix,
    resolve,
)


def test_the_ais_our_templates_use_are_all_present():
    # (01) GTIN, (21) SERIAL, (15) BEST BEFORE and (10) BATCH are what the
    # shipped GS1-1.txt element strings are built out of.
    for ai in ("01", "21", "15", "10"):
        assert is_known(ai)


def test_ai_rows_are_unique():
    codes = [entry.ai for entry in all_identifiers()]
    assert len(set(codes)) == len(codes)


def test_gtin_is_fourteen_fixed_numeric_with_a_check_digit():
    entry = resolve("01")
    assert entry.data_title == "GTIN"
    assert entry.is_fixed_length
    assert entry.min_length == 14
    assert entry.charset is CharacterSet.NUMERIC
    assert entry.check_digit


def test_serial_is_variable_alphanumeric_without_a_check_digit():
    entry = resolve("21")
    assert not entry.is_fixed_length
    assert entry.min_length == 1
    assert entry.max_length == 20
    assert entry.charset is CharacterSet.ALPHANUMERIC
    assert not entry.check_digit


def test_dates_are_six_digits():
    for ai in ("11", "12", "13", "15", "16", "17"):
        entry = resolve(ai)
        assert entry.kind is DataKind.DATE
        assert entry.min_length == entry.max_length == 6


@pytest.mark.parametrize("ai", ["00", "01", "02", "11", "15", "17", "20"])
def test_predefined_length_ais_need_no_separator(ai):
    entry = resolve(ai)
    assert entry.has_predefined_length
    assert not entry.requires_fnc1


@pytest.mark.parametrize("ai", ["10", "21", "22", "30", "37", "240", "400", "8004", "90"])
def test_variable_length_ais_need_a_separator(ai):
    entry = resolve(ai)
    assert not entry.has_predefined_length
    assert entry.requires_fnc1


def test_the_pairing_that_catches_people_out():
    # 15 is fixed at six and predefined, so no separator. 21 has a maximum of
    # twenty and is not predefined, so it always needs one. Both look similar in
    # a template editor and only one of them is safe to put in the middle.
    assert not resolve("15").requires_fnc1
    assert resolve("21").requires_fnc1


def test_a_fixed_length_ai_can_still_need_a_separator():
    # 8018 is exactly eighteen digits but 80 is not a predefined prefix.
    entry = resolve("8018")
    assert entry.is_fixed_length
    assert entry.requires_fnc1


def test_a_four_digit_measure_ai_resolves_directly():
    entry = resolve("3103")
    assert entry.kind is DataKind.MEASURE
    assert entry.has_decimal_indicator
    assert entry.family == "310"


@pytest.mark.parametrize("ai", ["3100", "3101", "3102", "3104", "3105", "3109"])
def test_other_decimal_positions_resolve_to_the_same_row(ai):
    assert resolve(ai) is resolve("3103")


def test_decimal_position_reads_the_last_digit():
    assert decimal_position("3100") == 0
    assert decimal_position("3103") == 3
    assert decimal_position("3105") == 5
    assert decimal_position("3202") == 2


def test_decimal_position_of_an_ai_without_one_is_refused():
    with pytest.raises(Gs1Error, match="no decimal indicator"):
        decimal_position("01")


def test_measure_ais_are_predefined_length():
    assert resolve("3103").has_predefined_length
    assert resolve("3203").has_predefined_length


@pytest.mark.parametrize("ai", ["", "1", "12345", "0a", "ab"])
def test_a_malformed_ai_is_refused(ai):
    with pytest.raises(Gs1Error):
        resolve(ai)


def test_a_non_numeric_ai_says_so():
    with pytest.raises(Gs1Error, match="must be digits"):
        resolve("A1")


def test_an_ai_of_the_wrong_length_says_so():
    with pytest.raises(Gs1Error, match="2 to 4 digits"):
        resolve("1")


def test_an_unknown_but_well_formed_ai_says_so():
    with pytest.raises(Gs1Error, match="unknown application identifier"):
        resolve("77")


def test_is_known_swallows_the_error():
    assert is_known("01")
    assert not is_known("77")
    assert not is_known("nonsense")


def test_describe_a_fixed_length_ai():
    assert resolve("01").describe() == "(01) GTIN N14"


def test_describe_a_variable_length_ai():
    assert resolve("21").describe() == "(21) SERIAL AN1..20"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0100125682945601", "01"),
        ("210001", "21"),
        ("15150807", "15"),
        ("3103001234", "3103"),
        ("240ABCDEF", "240"),
        ("8004ABC", "8004"),
    ],
)
def test_longest_prefix_picks_the_right_ai(text, expected):
    assert longest_prefix(text) == expected


def test_longest_prefix_prefers_the_longer_match():
    # 3103 must win over any two digit reading of the same text.
    assert longest_prefix("3103000500") == "3103"


def test_longest_prefix_returns_none_for_unknown_text():
    assert longest_prefix("7712345") is None
    assert longest_prefix("") is None
    assert longest_prefix("0") is None


def test_longest_prefix_does_not_read_past_the_end():
    assert longest_prefix("01") == "01"
    assert longest_prefix("0") is None
