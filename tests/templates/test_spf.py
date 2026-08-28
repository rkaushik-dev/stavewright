from __future__ import annotations

from datetime import datetime

import pytest

from stavewright.errors import TemplateError
from stavewright.templates.spf import (
    BLOCK_MARKER,
    MAGIC,
    SpfBlock,
    is_template,
    parse_spf,
    read_spf,
    read_utf16_string,
)


def u16(text: str) -> bytes:
    return text.encode("utf-16-le")


def build(
    *blocks: tuple[str, bytes],
    magic: str = MAGIC,
    timestamp: str = "13062016225044",
) -> bytes:
    raw = bytearray(u16(magic))
    raw.extend(b"\x00" * (64 - len(raw)))
    raw.extend(u16(timestamp))
    raw.extend(b"\x00" * 8)
    for name, payload in blocks:
        raw.extend(u16(BLOCK_MARKER))
        raw.extend(u16(name))
        raw.extend(b"\x00\x00")
        raw.extend(payload)
    return bytes(raw)


@pytest.fixture
def shipped(request):
    return read_spf(
        request.path.parent.parent.parent / "Matricode" / "Quattro" / "Templates" / "T1.spf"
    )


def test_read_utf16_string_stops_at_the_terminator():
    raw = u16("EAN-13+2") + b"\x00\x00" + u16("ignored")
    assert read_utf16_string(raw, 0) == "EAN-13+2"


def test_read_utf16_string_honours_the_limit():
    assert read_utf16_string(u16("ABCDEFGH"), 0, limit=3) == "ABC"


def test_read_utf16_string_at_an_offset():
    raw = u16("skip") + u16("wanted") + b"\x00\x00"
    assert read_utf16_string(raw, 8) == "wanted"


def test_read_utf16_string_at_the_end_of_the_buffer():
    assert read_utf16_string(u16("AB"), 4) == ""


def test_a_minimal_template_parses():
    template = parse_spf(build(("TEMPLATE", b"\x01\x02\x03\x04")))
    assert len(template) == 1
    assert template.kinds == ("TEMPLATE",)
    assert template.blocks[0].payload == b"\x01\x02\x03\x04"


def test_the_timestamp_is_read_as_day_month_year():
    template = parse_spf(build(("TEMPLATE", b"")))
    assert template.saved_at == datetime(2016, 6, 13, 22, 50, 44)


def test_an_unparseable_timestamp_becomes_none():
    template = parse_spf(build(("TEMPLATE", b""), timestamp="99999999999999"))
    assert template.saved_at is None


def test_a_short_timestamp_becomes_none():
    template = parse_spf(build(("TEMPLATE", b""), timestamp="1306"))
    assert template.saved_at is None


def test_a_non_numeric_timestamp_becomes_none():
    template = parse_spf(build(("TEMPLATE", b""), timestamp="not-a-stamp!!!"))
    assert template.saved_at is None


def test_a_file_that_is_not_a_template_is_refused():
    with pytest.raises(TemplateError, match="does not start with the template magic"):
        parse_spf(u16("SOMETHING ELSE ENTIRELY AND LONG ENOUGH TO GET PAST THE SIZE CHECK"))


def test_a_job_file_is_not_mistaken_for_a_template():
    with pytest.raises(TemplateError, match="does not start with the template magic") as excinfo:
        parse_spf(u16("FILE_JOB_VERSION_02") + b"\x00" * 64)
    assert excinfo.value.context["found"] == "FILE_JOB_VERSION_02"


def test_an_older_format_version_is_refused_by_the_magic():
    with pytest.raises(TemplateError, match="magic") as excinfo:
        parse_spf(build(("TEMPLATE", b""), magic="FILE_TEMPLATE_VERSION_01"))
    assert excinfo.value.context["expected"] == MAGIC


def test_a_truncated_file_is_refused():
    with pytest.raises(TemplateError, match="too short"):
        parse_spf(b"\x00\x08")


def test_an_empty_file_is_refused():
    with pytest.raises(TemplateError, match="too short"):
        parse_spf(b"")


def test_a_template_with_no_blocks_parses_to_nothing():
    template = parse_spf(build())
    assert len(template) == 0
    assert template.kinds == ()


def test_blocks_come_back_in_file_order():
    template = parse_spf(
        build(("TEMPLATE", b"a"), ("TEXTLABEL", b"b"), ("COGNEXLABEL", b"c"))
    )
    assert template.kinds == ("TEMPLATE", "TEXTLABEL", "COGNEXLABEL")
    assert [block.payload for block in template] == [b"a", b"b", b"c"]


def test_a_trailing_index_is_split_off_the_kind():
    block = SpfBlock(name="BARCODELABEL_3", offset=0)
    assert block.kind == "BARCODELABEL"
    assert block.index == 3


def test_a_name_without_an_index():
    block = SpfBlock(name="TEXTLABEL", offset=0)
    assert block.kind == "TEXTLABEL"
    assert block.index is None


def test_a_name_with_an_underscore_that_is_not_an_index():
    block = SpfBlock(name="TEXT_LABEL", offset=0)
    assert block.kind == "TEXT_LABEL"
    assert block.index is None


def test_of_kind_matches_on_the_base_name():
    template = parse_spf(
        build(("BARCODELABEL_1", b"a"), ("BARCODELABEL_3", b"b"), ("TEXTLABEL", b"c"))
    )
    assert [block.index for block in template.of_kind("BARCODELABEL")] == [1, 3]
    assert template.of_kind("TEXTLABEL")[0].payload == b"c"


def test_first_returns_the_earliest_block_of_that_kind():
    template = parse_spf(build(("BARCODELABEL_1", b"a"), ("BARCODELABEL_3", b"b")))
    assert template.first("BARCODELABEL").index == 1


def test_first_of_an_absent_kind_is_none():
    assert parse_spf(build(("TEMPLATE", b""))).first("BARCODELABEL") is None


def test_strings_pulls_utf16_runs_out_of_a_payload():
    payload = b"\x01\x02" + u16("EAN-13+2") + b"\x00\x00" + u16("Arial")
    block = SpfBlock(name="BARCODELABEL_3", offset=0, payload=payload)
    assert block.strings() == ["EAN-13+2", "Arial"]


def test_short_runs_are_dropped():
    payload = u16("AB") + b"\x00\x00" + u16("LONGER")
    block = SpfBlock(name="X", offset=0, payload=payload)
    assert block.strings() == ["LONGER"]


def test_the_minimum_run_length_is_adjustable():
    payload = u16("ABC") + b"\x00\x00" + u16("LONGER")
    block = SpfBlock(name="X", offset=0, payload=payload)
    assert block.strings(minimum=6) == ["LONGER"]


def test_strings_of_an_empty_payload_is_empty():
    assert SpfBlock(name="X", offset=0).strings() == []


def test_all_strings_spans_every_block():
    template = parse_spf(
        build(("TEXTLABEL", u16("Arial")), ("COGNEXLABEL", u16("Cognex Code")))
    )
    assert template.all_strings() == ["Arial", "Cognex Code"]


def test_describe_a_block():
    block = SpfBlock(name="BARCODELABEL_3", offset=436, payload=b"x" * 12)
    assert block.describe() == "BARCODELABEL #3 at 436 (12 bytes)"


def test_describe_a_block_without_an_index():
    assert SpfBlock(name="TEMPLATE", offset=348).describe() == "TEMPLATE at 348 (0 bytes)"


def test_read_reports_a_missing_file(tmp_path):
    with pytest.raises(TemplateError, match="cannot read template"):
        read_spf(tmp_path / "nope.spf")


def test_is_template(tmp_path):
    good = tmp_path / "good.spf"
    good.write_bytes(build(("TEMPLATE", b"")))
    bad = tmp_path / "bad.spf"
    bad.write_bytes(b"not a template at all")
    assert is_template(good)
    assert not is_template(bad)
    assert not is_template(tmp_path / "missing.spf")


# The shipped template.


def test_the_shipped_template_reads(shipped):
    assert shipped.size == 24004
    assert len(shipped) == 4


def test_the_shipped_template_was_saved_in_june_2016(shipped):
    assert shipped.saved_at == datetime(2016, 6, 13, 22, 50, 44)


def test_the_shipped_template_block_kinds(shipped):
    assert shipped.kinds == ("TEMPLATE", "BARCODELABEL", "TEXTLABEL", "COGNEXLABEL")


def test_the_shipped_barcode_block_is_instance_three(shipped):
    assert shipped.first("BARCODELABEL").index == 3


def test_the_shipped_barcode_block_names_its_symbology(shipped):
    assert "EAN-13+2" in shipped.first("BARCODELABEL").strings()


def test_the_shipped_barcode_block_carries_a_placeholder_value(shipped):
    strings = shipped.first("BARCODELABEL").strings()
    assert "00000000000000" in strings
    assert "000000000000000" in strings


def test_the_shipped_text_blocks_name_their_fonts(shipped):
    assert shipped.first("TEXTLABEL").strings() == ["F:Arial,12,0,0,S:3,ERT"]
    assert shipped.first("COGNEXLABEL").strings() == ["F:Arial,12,0,0,S:12,#Cognex Code"]


def test_the_barcode_block_is_almost_the_whole_file(shipped):
    barcode = shipped.first("BARCODELABEL")
    assert barcode.size > shipped.size * 0.9


def test_describe_names_the_file(shipped):
    assert shipped.describe() == "T1.spf: 4 blocks, saved 2016-06-13 22:50:44"
