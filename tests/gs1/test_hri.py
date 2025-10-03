from __future__ import annotations

import pytest

from v4t_setup.finding import Severity
from v4t_setup.gs1.ai import FNC1
from v4t_setup.gs1.hri import (
    MAX_HRI_CHARACTERS,
    check_element_string,
    check_hri,
    is_printable,
    render,
    render_lines,
)
from v4t_setup.gs1.parse import parse

SHIPPED = "(01)00125682945601(21)0001"
DATED = "(01)01001000173250(15)150807"


def rules(findings) -> list[str]:
    return [f.message for f in findings]


def test_the_shipped_element_string_renders_to_itself():
    assert render(parse(SHIPPED)) == SHIPPED


def test_the_dated_one_too():
    assert render(parse(DATED)) == DATED


def test_the_encoded_form_renders_back_with_brackets():
    assert render(parse("0100125682945601210001")) == SHIPPED


def test_a_date_is_printed_as_it_is_stored():
    # Not expanded. An operator reading 150807 against a label that says
    # 07/08/15 has to be able to tell they are the same thing.
    assert "150807" in render(parse(DATED))
    assert "2015" not in render(parse(DATED))


def test_there_is_no_separator_in_the_rendered_form():
    with_separator = parse(f"10AD47-001{FNC1}0101001000173250")
    assert FNC1 not in render(with_separator)


def test_rendering_one_element_per_line():
    lines = render_lines(parse(SHIPPED))
    assert [line.text for line in lines] == ["(01)00125682945601", "(21)0001"]
    assert [line.ai for line in lines] == ["01", "21"]


def test_a_line_knows_its_own_length():
    assert len(render_lines(parse(SHIPPED))[1]) == len("(21)0001")


def test_printable_text():
    assert is_printable(SHIPPED)
    assert is_printable("AD47-001")


def test_a_separator_is_not_printable():
    assert not is_printable(f"01{FNC1}21")


@pytest.mark.parametrize("character", ["\x00", "\r", "\n", "\t"])
def test_control_characters_are_not_printable(character):
    assert not is_printable(f"abc{character}def")


def test_good_text_produces_nothing():
    assert check_hri(SHIPPED) == []


def test_empty_text_is_an_error():
    findings = check_hri("")
    assert len(findings) == 1
    assert findings[0].severity is Severity.ERROR
    assert "no human readable text" in findings[0].message


def test_the_encoded_form_as_hri_is_an_error():
    # This is the common template mistake. A person keying it in has no way to
    # see where the AI ends.
    findings = check_hri("0100125682945601210001")
    assert any("it is the encoded form" in message for message in rules(findings))
    assert any(f.severity is Severity.ERROR for f in findings)


def test_a_separator_left_in_the_text_is_an_error():
    findings = check_hri(f"(10)AD47-001{FNC1}(01)01001000173250")
    error = next(f for f in findings if "no glyph" in f.message)
    assert error.severity is Severity.ERROR
    assert error.context["codepoints"] == ["U+001D"]


def test_a_newline_in_the_text_is_an_error():
    findings = check_hri("(01)01001000173250\n(21)0001")
    assert any("no glyph" in message for message in rules(findings))


def test_characters_outside_set_82_are_a_warning():
    findings = check_hri("(10)café")
    warning = next(f for f in findings if "outside GS1 set 82" in f.message)
    assert warning.severity is Severity.WARNING
    assert "é" in warning.context["characters"]


def test_brackets_themselves_are_not_flagged():
    assert check_hri(SHIPPED) == []


def test_text_that_is_too_long_is_a_warning():
    long_text = "(10)" + "A" * (MAX_HRI_CHARACTERS + 10)
    findings = check_hri(long_text)
    warning = next(f for f in findings if "too long to read" in f.message)
    assert warning.severity is Severity.WARNING
    assert warning.context["limit"] == MAX_HRI_CHARACTERS


def test_text_at_the_limit_is_fine():
    text = "(10)" + "A" * (MAX_HRI_CHARACTERS - 4)
    assert len(text) == MAX_HRI_CHARACTERS
    assert not any("too long" in message for message in rules(check_hri(text)))


def test_text_that_matches_the_symbol_passes():
    parsed = parse(SHIPPED)
    assert check_hri(SHIPPED, parsed) == []


def test_text_that_disagrees_with_the_symbol_is_an_error():
    # The defect that gets product rejected rather than merely queried.
    parsed = parse(SHIPPED)
    findings = check_hri("(01)01001000173250(21)0001", parsed)
    mismatch = next(f for f in findings if "does not match what the symbol encodes" in f.message)
    assert mismatch.severity is Severity.ERROR
    assert mismatch.context["encoded"] == SHIPPED


def test_a_missing_element_in_the_text_is_caught():
    parsed = parse(SHIPPED)
    findings = check_hri("(01)00125682945601", parsed)
    assert any("does not match" in message for message in rules(findings))


def test_checking_an_element_string_directly():
    assert check_element_string(SHIPPED) == []


def test_checking_the_encoded_form_directly_is_fine():
    # Given the encoded form, the check renders the proper HRI from it first,
    # so it does not then complain that the HRI is the encoded form.
    assert check_element_string("0100125682945601210001") == []


def test_checking_something_that_will_not_parse_falls_back_to_the_text_rules():
    findings = check_element_string("not an element string at all")
    assert findings
    assert any("encoded form" in message for message in rules(findings))


def test_a_long_element_string_is_flagged_through_the_convenience_form():
    text = "(01)01001000173250(21)" + "A" * 20 + "(10)" + "B" * 20
    findings = check_element_string(text)
    assert any("too long to read" in message for message in rules(findings))


def test_every_finding_carries_the_hri_rule_name():
    findings = check_hri(f"0100125682945601{FNC1}")
    assert findings
    assert all(f.rule == "hri" for f in findings)


def test_several_problems_are_all_reported():
    findings = check_hri("01001000173250" + "A" * 60)
    messages = " ".join(rules(findings))
    assert "encoded form" in messages
    assert "too long" in messages
