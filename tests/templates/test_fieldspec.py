from __future__ import annotations

import pytest

from v4t_setup.errors import TemplateError
from v4t_setup.templates.fieldspec import (
    FieldSpec,
    FontSpec,
    find_field_specs,
    parse_field_spec,
    try_parse_field_spec,
)

# The two specifications that appear in the shipped T1.spf.
TEXT_LABEL = "F:Arial,12,0,0,S:3,ERT"
COGNEX_LABEL = "F:Arial,12,0,0,S:12,#Cognex Code"


def test_the_shipped_text_label():
    spec = parse_field_spec(TEXT_LABEL)
    assert spec.font == FontSpec(name="Arial", size=12)
    assert spec.source == 3
    assert spec.reference == "ERT"
    assert spec.literal is None
    assert spec.is_bound
    assert not spec.is_literal


def test_the_shipped_cognex_label():
    spec = parse_field_spec(COGNEX_LABEL)
    assert spec.font == FontSpec(name="Arial", size=12)
    assert spec.source == 12
    assert spec.literal == "Cognex Code"
    assert spec.reference is None
    assert spec.is_literal
    assert not spec.is_bound


@pytest.mark.parametrize("text", [TEXT_LABEL, COGNEX_LABEL])
def test_the_shipped_specs_round_trip(text):
    assert str(parse_field_spec(text)) == text


def test_a_literal_may_contain_spaces():
    assert parse_field_spec("#Batch of the day").literal == "Batch of the day"


def test_an_empty_literal_is_allowed():
    spec = parse_field_spec("F:Arial,10,0,0,#")
    assert spec.literal == ""
    assert spec.is_literal


def test_bold_and_italic_flags():
    spec = parse_field_spec("F:Arial,14,1,1,S:1,GTIN")
    assert spec.font == FontSpec(name="Arial", size=14, bold=True, italic=True)


def test_only_bold():
    assert parse_field_spec("F:OCR-B,9,1,0,S:1,X").font.bold
    assert not parse_field_spec("F:OCR-B,9,1,0,S:1,X").font.italic


def test_a_font_name_with_a_hyphen():
    assert parse_field_spec("F:OCR-B,9,0,0,S:1,X").font.name == "OCR-B"


def test_a_spec_with_no_font():
    spec = parse_field_spec("S:4,SERIAL")
    assert spec.font is None
    assert spec.source == 4
    assert spec.reference == "SERIAL"


def test_a_spec_with_no_source():
    spec = parse_field_spec("F:Arial,12,0,0,SERIAL")
    assert spec.source is None
    assert spec.reference == "SERIAL"


def test_a_spec_with_only_a_font():
    spec = parse_field_spec("F:Arial,12,0,0")
    assert spec.font == FontSpec(name="Arial", size=12)
    assert not spec.is_bound
    assert not spec.is_literal


def test_extra_tokens_after_the_first_reference_are_kept():
    spec = parse_field_spec("F:Arial,12,0,0,S:3,ERT,LEFT,WRAP")
    assert spec.reference == "ERT"
    assert spec.extras == ("LEFT", "WRAP")


def test_extra_tokens_round_trip():
    text = "F:Arial,12,0,0,S:3,ERT,LEFT,WRAP"
    assert str(parse_field_spec(text)) == text


def test_empty_tokens_are_skipped():
    spec = parse_field_spec("F:Arial,12,0,0,,S:3,,ERT")
    assert spec.source == 3
    assert spec.reference == "ERT"


def test_an_empty_spec_is_refused():
    with pytest.raises(TemplateError, match="is empty"):
        parse_field_spec("")


def test_a_whitespace_only_spec_is_refused():
    with pytest.raises(TemplateError, match="is empty"):
        parse_field_spec("   ")


def test_a_truncated_font_run_is_refused():
    with pytest.raises(TemplateError, match="font run is truncated") as excinfo:
        parse_field_spec("F:Arial,12,0")
    assert excinfo.value.context["found"] == 2


def test_a_font_run_with_nothing_after_it_is_refused():
    with pytest.raises(TemplateError, match="font run is truncated"):
        parse_field_spec("F:Arial")


def test_a_non_numeric_font_size_is_refused():
    with pytest.raises(TemplateError, match="font size is not a number"):
        parse_field_spec("F:Arial,twelve,0,0")


@pytest.mark.parametrize("flag", ["2", "-1", "y", ""])
def test_a_bad_bold_flag_is_refused(flag):
    with pytest.raises(TemplateError, match="bold flag must be 0 or 1"):
        parse_field_spec(f"F:Arial,12,{flag},0")


@pytest.mark.parametrize("flag", ["2", "true", "x"])
def test_a_bad_italic_flag_is_refused(flag):
    with pytest.raises(TemplateError, match="italic flag must be 0 or 1"):
        parse_field_spec(f"F:Arial,12,0,{flag}")


def test_a_non_numeric_source_is_refused():
    with pytest.raises(TemplateError, match="source slot is not a number"):
        parse_field_spec("F:Arial,12,0,0,S:third,ERT")


def test_describe_a_bound_field():
    assert parse_field_spec(TEXT_LABEL).describe() == "field ERT from slot 3, Arial 12pt"


def test_describe_a_literal_field():
    assert parse_field_spec(COGNEX_LABEL).describe() == (
        'literal "Cognex Code" from slot 12, Arial 12pt'
    )


def test_describe_a_styled_font():
    spec = parse_field_spec("F:Arial,14,1,1,S:1,GTIN")
    assert spec.describe() == "field GTIN from slot 1, Arial 14pt bold italic"


def test_describe_a_spec_with_nothing_bound():
    assert parse_field_spec("F:Arial,12,0,0").describe() == "no data, Arial 12pt"


def test_describe_a_spec_with_no_font():
    assert parse_field_spec("S:4,SERIAL").describe() == "field SERIAL from slot 4, no font"


def test_font_str_round_trips():
    assert str(FontSpec(name="Arial", size=12)) == "F:Arial,12,0,0"
    assert str(FontSpec(name="Arial", size=12, bold=True)) == "F:Arial,12,1,0"


def test_try_parse_returns_none_for_ordinary_text():
    assert try_parse_field_spec("EAN-13+2") is None
    assert try_parse_field_spec("00000000000000") is None
    assert try_parse_field_spec("") is None


def test_try_parse_returns_none_for_a_broken_spec():
    assert try_parse_field_spec("F:Arial,12") is None


def test_try_parse_handles_a_good_spec():
    assert try_parse_field_spec(TEXT_LABEL) is not None


def test_find_field_specs_filters_a_block_string_list():
    strings = ["EAN-13+2", TEXT_LABEL, "00000000000000", COGNEX_LABEL]
    found = find_field_specs(strings)
    assert [spec.source for spec in found] == [3, 12]


def test_find_field_specs_of_nothing_is_empty():
    assert find_field_specs([]) == []
    assert find_field_specs(["EAN-13+2", "000000"]) == []


def test_an_empty_field_spec_object():
    spec = FieldSpec()
    assert str(spec) == ""
    assert spec.describe() == "no data, no font"
