from __future__ import annotations

import pytest

from stavewright.errors import Gs1Error
from stavewright.gs1.checkdigit import (
    gtin_indicator,
    is_variable_measure,
    mod10_check_digit,
    normalise_gtin,
    require_mod10,
    verify_gln,
    verify_gtin,
    verify_mod10,
    verify_sscc,
    with_check_digit,
)

# Codes with independently known check digits. None of these come from running
# the implementation, which is the whole point of having them.
KNOWN_GOOD = [
    "01001000173250",  # GTIN-14, out of the shipped GS1 template data
    "00000000000000",
    "5000157024671",  # GTIN-13
    "4006381333931",  # GTIN-13
    "614141000036",  # GTIN-12
    "40170725",  # GTIN-8
    "123456789012345675",  # SSCC-18
]


@pytest.mark.parametrize("code", KNOWN_GOOD)
def test_known_good_codes_verify(code):
    assert verify_mod10(code)


@pytest.mark.parametrize("code", KNOWN_GOOD)
def test_recomputing_the_check_digit_reproduces_it(code):
    assert with_check_digit(code[:-1]) == code


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ("0100100017325", 0),
        ("500015702467", 1),
        ("400638133393", 1),
        ("61414100003", 6),
        ("4017072", 5),
        ("0012568294560", 6),
    ],
)
def test_check_digit_values(payload, expected):
    assert mod10_check_digit(payload) == expected


def test_the_weighting_starts_at_three_on_the_right():
    # A single digit payload is weighted 3, not 1. If the alternation were the
    # other way round this would come out as 9 rather than 7.
    assert mod10_check_digit("1") == 7


def test_two_digit_payload_uses_both_weights():
    # 1*1 + 2*3 = 7, so the check digit is 3.
    assert mod10_check_digit("12") == 3


def test_a_sum_that_is_already_a_multiple_of_ten_gives_zero():
    assert mod10_check_digit("0") == 0


@pytest.mark.parametrize("code", ["5000157024670", "4006381333930", "614141000037"])
def test_a_wrong_check_digit_fails(code):
    assert not verify_mod10(code)


def test_the_gtin_in_the_shipped_template_data_is_wrong():
    # 00125682945601 appears in every row of Matricode/Quattro/Templates/GS1-1.txt
    # and its check digit should be 6. This is not a test failure, it is a
    # finding about the data file, and the dataset linter reports it.
    assert not verify_gtin("00125682945601")
    assert mod10_check_digit("0012568294560") == 6


def test_the_other_gtin_in_the_same_file_is_correct():
    assert verify_gtin("01001000173250")


def test_require_mod10_returns_the_value_when_it_holds():
    assert require_mod10("01001000173250") == "01001000173250"


def test_require_mod10_reports_what_the_digit_should_have_been():
    with pytest.raises(Gs1Error, match="incorrect check digit") as excinfo:
        require_mod10("00125682945601", what="GTIN")
    assert excinfo.value.context["found"] == "1"
    assert excinfo.value.context["expected"] == "6"


@pytest.mark.parametrize("value", ["", "abc", "12x4", "12 34", "0100100017325O"])
def test_non_numeric_input_is_refused(value):
    with pytest.raises(Gs1Error):
        verify_mod10(value)


def test_a_single_digit_cannot_carry_a_check_digit():
    with pytest.raises(Gs1Error, match="too short"):
        verify_mod10("7")


def test_an_empty_payload_is_refused():
    with pytest.raises(Gs1Error, match="is empty"):
        mod10_check_digit("")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("40170725", "00000040170725"),
        ("614141000036", "00614141000036"),
        ("5000157024671", "05000157024671"),
        ("01001000173250", "01001000173250"),
    ],
)
def test_normalise_pads_to_fourteen(value, expected):
    assert normalise_gtin(value) == expected


def test_padding_does_not_change_the_check_digit():
    for code in ["40170725", "614141000036", "5000157024671"]:
        assert verify_mod10(normalise_gtin(code))


@pytest.mark.parametrize("length", [7, 9, 10, 11, 15, 18])
def test_an_illegal_gtin_length_is_refused(length):
    with pytest.raises(Gs1Error, match="8, 12, 13 or 14 digits"):
        normalise_gtin("1" * length)


@pytest.mark.parametrize(
    ("gtin", "indicator"),
    [
        ("01001000173250", 0),
        ("5000157024671", 0),
        ("15000157024678", 1),
        ("95000157024672", 9),
    ],
)
def test_indicator_digit(gtin, indicator):
    assert gtin_indicator(gtin) == indicator


def test_variable_measure_items_are_the_ones_starting_with_nine():
    assert is_variable_measure("95000157024672")
    assert not is_variable_measure("15000157024678")
    assert not is_variable_measure("01001000173250")


def test_sscc_must_be_eighteen_digits():
    assert verify_sscc("123456789012345675")
    assert not verify_sscc("12345678901234567")
    assert not verify_sscc("1234567890123456755")


def test_sscc_with_a_bad_check_digit():
    assert not verify_sscc("123456789012345670")


def test_gln_must_be_thirteen_digits():
    assert verify_gln("5000157024671")
    assert not verify_gln("500015702467")
    assert not verify_gln("50001570246712")


def test_gln_with_a_bad_check_digit():
    assert not verify_gln("5000157024670")
