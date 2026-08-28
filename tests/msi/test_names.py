from __future__ import annotations

from pathlib import Path

import pytest

from stavewright.msi.cfb import read_compound_file
from stavewright.msi.names import (
    ALPHABET,
    TABLE_MARKER,
    decode_stream_name,
    encode_stream_name,
    is_table_stream,
    table_name,
)

MSI = Path(__file__).resolve().parents[2] / "msi-quattro" / "msi-Jet600.msi"

# Every table the shipped installer carries, which is the thing this module
# exists to be able to name.
SHIPPED_TABLES = {
    "AdminExecuteSequence",
    "AdminUISequence",
    "AdvtExecuteSequence",
    "Binary",
    "Component",
    "Control",
    "ControlCondition",
    "ControlEvent",
    "CustomAction",
    "Dialog",
    "Directory",
    "EventMapping",
    "Feature",
    "FeatureComponents",
    "File",
    "InstallExecuteSequence",
    "InstallUISequence",
    "Media",
    "ModuleComponents",
    "ModuleDependency",
    "ModuleSignature",
    "MsiAssembly",
    "MsiAssemblyName",
    "MsiFileHash",
    "MsiSFCBypass",
    "Property",
    "RadioButton",
    "Registry",
    "SxsMsmGenComponents",
    "TextStyle",
    "UIText",
    "Upgrade",
    "_Columns",
    "_StringData",
    "_StringPool",
    "_Tables",
    "_Validation",
}


@pytest.fixture(scope="module")
def encoded_names() -> list[str]:
    return read_compound_file(MSI).names


def test_the_alphabet_is_sixty_four_characters():
    assert len(ALPHABET) == 64
    assert len(set(ALPHABET)) == 64


def test_the_alphabet_ends_with_dot_and_underscore():
    assert ALPHABET[-2:] == "._"


def test_the_shipped_msi_decodes_to_the_tables_it_should_have(encoded_names):
    found = {name for name in (table_name(raw) for raw in encoded_names) if name}
    assert found == SHIPPED_TABLES


def test_every_packed_shipped_name_round_trips(encoded_names):
    for raw in encoded_names:
        if raw.startswith("\x05"):
            continue
        assert encode_stream_name(decode_stream_name(raw)) == raw


def test_the_summary_information_stream_is_stored_unpacked(encoded_names):
    # The installer does not pack it, because it is a plain OLE property set
    # rather than an MSI table. So the name survives decoding untouched, and
    # encoding it produces a packed name the file does not have.
    assert "\x05SummaryInformation" in encoded_names
    assert decode_stream_name("\x05SummaryInformation") == "\x05SummaryInformation"
    assert encode_stream_name("\x05SummaryInformation") != "\x05SummaryInformation"


def test_decoding_always_undoes_encoding(encoded_names):
    # The direction that does hold everywhere, which is the one callers use.
    for raw in encoded_names:
        plain = decode_stream_name(raw)
        assert decode_stream_name(encode_stream_name(plain)) == plain


def test_the_binary_streams_keep_their_dots(encoded_names):
    decoded = [decode_stream_name(raw) for raw in encoded_names]
    assert "Binary.DefBannerBitmap" in decoded


@pytest.mark.parametrize(
    "plain",
    [
        "Directory",
        "Component",
        "_StringPool",
        "Binary.DefBannerBitmap",
        "MsiAssemblyName",
        "a",
        "ab",
        "abc",
        "A_b.c9",
        "_",
        ".",
        "0123456789",
    ],
)
def test_a_plain_name_round_trips(plain):
    assert decode_stream_name(encode_stream_name(plain)) == plain


@pytest.mark.parametrize("plain", ["!Directory", "!_Tables", "!A"])
def test_a_table_name_round_trips(plain):
    assert decode_stream_name(encode_stream_name(plain)) == plain


def test_an_empty_name():
    assert decode_stream_name("") == ""
    assert encode_stream_name("") == ""


def test_an_odd_length_name_uses_a_single_digit_unit():
    # Three characters go into one two digit unit and one single.
    assert len(encode_stream_name("abc")) == 2


def test_an_even_length_name_packs_two_at_a_time():
    assert len(encode_stream_name("abcd")) == 2


def test_a_table_marker_costs_one_unit():
    assert len(encode_stream_name("!abcd")) == 3


def test_the_marker_is_the_documented_code_point():
    assert ord(TABLE_MARKER) == 0x4840


def test_a_table_stream_is_recognised_by_its_marker():
    assert is_table_stream(encode_stream_name("!Directory"))
    assert not is_table_stream(encode_stream_name("Directory"))


def test_a_stream_that_is_not_a_table_has_no_table_name():
    assert table_name(encode_stream_name("Binary.NewFldrBtn")) is None


def test_the_table_name_drops_the_marker():
    assert table_name(encode_stream_name("!Feature")) == "Feature"


def test_characters_outside_the_alphabet_pass_through():
    assert decode_stream_name("\x05Summary") == "\x05Summary"


def test_a_name_that_is_part_packed_and_part_not():
    packed = encode_stream_name("\x05ab")
    assert decode_stream_name(packed) == "\x05ab"


def test_a_marker_that_is_not_first_still_decodes_to_a_bang():
    # Never written by the installer, but the decoder should not lose a
    # character over it.
    assert decode_stream_name(f"a{TABLE_MARKER}") == "a!"


def test_the_two_digit_range_is_low_bits_first():
    # "01" packs 0 in the low six bits and 1 in the high six.
    assert encode_stream_name("01") == chr(0x3800 + 0 + (1 << 6))


def test_the_one_digit_range_starts_where_it_should():
    assert encode_stream_name("0") == chr(0x4800)
    assert encode_stream_name("_") == chr(0x4800 + 63)


def test_decoding_the_top_of_the_one_digit_range():
    assert decode_stream_name(chr(0x483F)) == "_"


def test_decoding_the_top_of_the_two_digit_range():
    assert decode_stream_name(chr(0x47FF)) == "__"


def test_a_code_point_just_below_the_packed_range_is_literal():
    assert decode_stream_name(chr(0x37FF)) == chr(0x37FF)


def test_a_code_point_just_above_the_marker_is_literal():
    assert decode_stream_name(chr(0x4841)) == chr(0x4841)


def test_every_alphabet_character_survives_a_round_trip():
    assert decode_stream_name(encode_stream_name(ALPHABET)) == ALPHABET
