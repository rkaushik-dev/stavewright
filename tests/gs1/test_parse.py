from __future__ import annotations

import pytest

from v4t_setup.errors import Gs1Error
from v4t_setup.gs1.ai import FNC1
from v4t_setup.gs1.parse import parse, parse_bracketed, parse_concatenated

# Both element strings that appear in the shipped GS1-1.txt.
SHIPPED_A = "(01)00125682945601(21)0001"
SHIPPED_B = "(01)01001000173250(15)150807"


def test_the_first_shipped_element_string():
    parsed = parse_bracketed(SHIPPED_A)
    assert parsed.ais == ("01", "21")
    assert parsed.require("01") == "00125682945601"
    assert parsed.require("21") == "0001"


def test_the_second_shipped_element_string():
    parsed = parse_bracketed(SHIPPED_B)
    assert parsed.ais == ("01", "15")
    assert parsed.require("15") == "150807"


@pytest.mark.parametrize("text", [SHIPPED_A, SHIPPED_B])
def test_bracketed_round_trips(text):
    assert parse_bracketed(text).bracketed() == text


def test_a_trailing_variable_element_needs_no_separator():
    # (21) is variable length but it is last, so the end of the string ends it.
    assert parse_bracketed(SHIPPED_A).concatenated() == "0100125682945601210001"


def test_a_fixed_predefined_element_needs_no_separator_either():
    assert parse_bracketed(SHIPPED_B).concatenated() == "010100100017325015150807"


def test_a_variable_element_in_the_middle_gets_a_separator():
    parsed = parse_bracketed("(01)01001000173250(21)0001(15)150807")
    assert parsed.concatenated() == f"0101001000173250210001{FNC1}15150807"


def test_two_variable_elements_in_a_row():
    parsed = parse_bracketed("(10)AD47-001(21)0001(01)01001000173250")
    assert parsed.concatenated() == f"10AD47-001{FNC1}210001{FNC1}010100100017325 0".replace(
        " ", ""
    )


def test_encoded_length_counts_separators():
    parsed = parse_bracketed("(01)01001000173250(21)0001(15)150807")
    assert parsed.encoded_length() == len(parsed.concatenated())
    # (01) is 2 + 14, (21) is 2 + 4 plus a separator because it is not last,
    # and (15) is 2 + 6 with nothing after it.
    assert parsed.encoded_length() == (2 + 14) + (2 + 4 + 1) + (2 + 6)
    assert parsed.encoded_length() == 31


@pytest.mark.parametrize(
    "text",
    [
        "0100125682945601210001",
        "010100100017325015150807",
        f"0101001000173250210001{FNC1}15150807",
        f"10AD47-001{FNC1}0101001000173250",
    ],
)
def test_concatenated_round_trips(text):
    assert parse_concatenated(text).concatenated() == text


def test_concatenated_reads_a_fixed_element_by_length():
    parsed = parse_concatenated("0100125682945601210001")
    assert parsed.require("01") == "00125682945601"
    assert parsed.require("21") == "0001"


def test_concatenated_reads_a_variable_element_up_to_the_separator():
    parsed = parse_concatenated(f"10AD47-001{FNC1}0101001000173250")
    assert parsed.require("10") == "AD47-001"
    assert parsed.require("01") == "01001000173250"


def test_concatenated_reads_a_trailing_variable_element_to_the_end():
    parsed = parse_concatenated("0101001000173250210001ABCD")
    assert parsed.require("21") == "0001ABCD"


def test_a_separator_after_a_predefined_element_is_consumed_not_required():
    # 15 is predefined so it should not be followed by a separator, but a
    # generator that emits one anyway still produces readable data.
    parsed = parse_concatenated("0101001000173250" + "15150807")
    assert parsed.ais == ("01", "15")


def test_parse_picks_the_form_from_the_first_character():
    assert parse(SHIPPED_A).ais == ("01", "21")
    assert parse("0100125682945601210001").ais == ("01", "21")


def test_membership_and_get():
    parsed = parse(SHIPPED_A)
    assert "01" in parsed
    assert "15" not in parsed
    assert parsed.get("15") is None
    assert parsed.get("01") == "00125682945601"


def test_require_reports_what_was_present():
    parsed = parse(SHIPPED_A)
    with pytest.raises(Gs1Error, match="missing a required application identifier") as excinfo:
        parsed.require("15")
    assert excinfo.value.context["present"] == ["01", "21"]


def test_len_and_iteration():
    parsed = parse(SHIPPED_A)
    assert len(parsed) == 2
    assert [element.ai for element in parsed] == ["01", "21"]


def test_str_of_an_element_is_its_bracketed_form():
    element = next(iter(parse(SHIPPED_A)))
    assert str(element) == "(01)00125682945601"


def test_str_of_an_element_string_is_its_bracketed_form():
    assert str(parse(SHIPPED_A)) == SHIPPED_A


def test_an_empty_string_is_refused():
    with pytest.raises(Gs1Error, match="is empty"):
        parse_bracketed("")
    with pytest.raises(Gs1Error, match="is empty"):
        parse_concatenated("")


def test_text_with_no_brackets_is_refused():
    with pytest.raises(Gs1Error, match="no bracketed AI"):
        parse_bracketed("0100125682945601")


def test_text_before_the_first_ai_is_refused():
    with pytest.raises(Gs1Error, match="text before its first AI") as excinfo:
        parse_bracketed("GTIN:(01)01001000173250")
    assert excinfo.value.context["leading"] == "GTIN:"


def test_an_unknown_ai_is_refused():
    with pytest.raises(Gs1Error, match="unknown application identifier"):
        parse_bracketed("(77)1234")


def test_a_fixed_length_element_that_is_too_short_is_refused():
    with pytest.raises(Gs1Error, match="wrong length") as excinfo:
        parse_bracketed("(01)0100100017325")
    assert excinfo.value.context["expected"] == "14"
    assert excinfo.value.context["length"] == 13


def test_a_fixed_length_element_that_is_too_long_is_refused():
    with pytest.raises(Gs1Error, match="wrong length"):
        parse_bracketed("(15)1508070")


def test_a_variable_element_over_its_maximum_is_refused():
    with pytest.raises(Gs1Error, match="wrong length") as excinfo:
        parse_bracketed("(21)" + "A" * 21)
    assert excinfo.value.context["expected"] == "1 to 20"


def test_an_empty_variable_element_is_refused():
    with pytest.raises(Gs1Error, match="wrong length"):
        parse_bracketed("(21)")


def test_a_numeric_ai_with_letters_is_refused():
    with pytest.raises(Gs1Error, match="must be numeric"):
        parse_bracketed("(01)0100100017325X")


def test_an_alphanumeric_ai_accepts_the_shipped_lot_codes():
    # AB:08 and AC:00 both appear in the LOTCODE column of GS1-1.txt.
    assert parse_bracketed("(10)AB:08").require("10") == "AB:08"
    assert parse_bracketed("(10)AC:00").require("10") == "AC:00"


@pytest.mark.parametrize("bad", ["A B", "A#B", "A$B", "A@B", "A[B", "A{B"])
def test_characters_outside_set_82_are_refused(bad):
    with pytest.raises(Gs1Error, match="character set 82") as excinfo:
        parse_bracketed(f"(21){bad}")
    assert excinfo.value.context["illegal"]


@pytest.mark.parametrize("good", ["AD47-001", "a.b_c", "12/34", "x%y", "p+q", "n?m"])
def test_characters_inside_set_82_are_accepted(good):
    assert parse_bracketed(f"(21){good}").require("21") == good


def test_a_fixed_element_running_off_the_end_is_refused():
    with pytest.raises(Gs1Error, match="runs off the end"):
        parse_concatenated("010012568294")


def test_an_unrecognised_ai_in_the_concatenated_form_is_refused():
    with pytest.raises(Gs1Error, match="unrecognised application identifier") as excinfo:
        parse_concatenated("7712345678")
    assert excinfo.value.context["offset"] == 0


def test_a_leading_separator_is_refused():
    with pytest.raises(Gs1Error, match="separator where an AI was expected"):
        parse_concatenated(f"{FNC1}0101001000173250")


def test_a_doubled_separator_is_refused():
    with pytest.raises(Gs1Error, match="separator where an AI was expected"):
        parse_concatenated(f"10AD47-001{FNC1}{FNC1}0101001000173250")


def test_offsets_are_recorded_for_bracketed_input():
    parsed = parse_bracketed(SHIPPED_A)
    assert [element.offset for element in parsed] == [0, 18]
