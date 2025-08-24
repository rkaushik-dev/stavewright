from __future__ import annotations

import pytest

from v4t_setup.errors import Gs1Error
from v4t_setup.gs1.symbology import (
    DATA_MATRIX_SIDES,
    QUIET_ZONE_MODULES,
    Encoding,
    Shape,
    all_symbologies,
    data_matrix_side,
    fits_head,
    is_known,
    resolve,
)
from v4t_setup.model.profile import TerminalModel


def test_names_are_unique():
    names = [entry.name for entry in all_symbologies()]
    assert len(set(names)) == len(names)


def test_the_symbology_the_shipped_template_uses_is_known():
    assert is_known("EAN-13+2")
    assert resolve("EAN-13+2").shape is Shape.LINEAR


def test_an_unknown_symbology_is_refused():
    with pytest.raises(Gs1Error, match="unknown symbology") as excinfo:
        resolve("EAN-99")
    assert "EAN-13" in excinfo.value.context["known"]


def test_is_known():
    assert is_known("GS1-128")
    assert not is_known("PDF417")


@pytest.mark.parametrize(
    ("name", "length"),
    [("EAN-13", 13), ("EAN-13+2", 15), ("EAN-8", 8), ("UPC-A", 12), ("ITF-14", 14)],
)
def test_the_fixed_length_symbologies(name, length):
    entry = resolve(name)
    assert entry.is_fixed_length
    assert entry.accepts_length(length)
    assert not entry.accepts_length(length - 1)
    assert not entry.accepts_length(length + 1)


def test_the_variable_length_ones_have_a_range():
    entry = resolve("GS1-128")
    assert not entry.is_fixed_length
    assert entry.accepts_length(1)
    assert entry.accepts_length(48)
    assert not entry.accepts_length(49)


def test_an_ean13_cannot_carry_an_element_string():
    # This is the failure the module exists for. A template built around a
    # thirteen digit GTIN cannot take a GTIN plus a serial.
    entry = resolve("EAN-13")
    assert not entry.carries_ai
    assert not entry.accepts_length(22)


def test_the_two_symbologies_that_carry_ais():
    carriers = [entry.name for entry in all_symbologies() if entry.carries_ai]
    assert carriers == ["GS1-128", "GS1 DataMatrix"]


def test_numeric_symbologies_refuse_letters():
    entry = resolve("EAN-13")
    assert entry.accepts_characters("0123456789012")
    assert not entry.accepts_characters("012345678901X")


def test_alphanumeric_symbologies_accept_printable_ascii():
    entry = resolve("GS1-128")
    assert entry.accepts_characters("AD47-001")
    assert entry.accepts_characters("(01)01001000173250")
    assert not entry.accepts_characters("café")


def test_binary_symbologies_accept_anything():
    assert resolve("GS1 DataMatrix").accepts_characters("café ☃")


def test_a_fixed_linear_symbol_is_a_fixed_width():
    assert resolve("EAN-13").modules_for(13) == 95
    assert resolve("EAN-13").modules_for(99) == 95


def test_the_two_digit_add_on_makes_the_symbol_wider():
    assert resolve("EAN-13+2").modules_for(15) > resolve("EAN-13").modules_for(13)


def test_a_variable_linear_symbol_grows_with_the_data():
    entry = resolve("GS1-128")
    assert entry.modules_for(20) > entry.modules_for(10)
    assert entry.modules_for(20) - entry.modules_for(10) == 110


def test_the_quiet_zone_is_added_to_a_linear_symbol():
    entry = resolve("EAN-13")
    assert entry.width_modules(13) == 95 + 2 * QUIET_ZONE_MODULES
    assert entry.width_modules(13, quiet_zone=False) == 95


def test_a_matrix_symbol_gets_no_quiet_zone_added():
    entry = resolve("GS1 DataMatrix")
    assert entry.width_modules(10) == entry.width_modules(10, quiet_zone=False)


def test_width_in_dots_follows_the_module_size():
    entry = resolve("EAN-13")
    assert entry.width_dots(13, module_dots=2) == entry.width_modules(13) * 2
    assert entry.width_dots(13, module_dots=4) == entry.width_modules(13) * 4


def test_a_module_narrower_than_a_dot_is_refused():
    with pytest.raises(Gs1Error, match="narrower than one dot"):
        resolve("EAN-13").width_dots(13, module_dots=0)


def test_a_long_gs1_128_is_wider_than_you_would_guess():
    # Fifty characters at eleven modules each, plus the fixed overhead and the
    # quiet zone, is over two inches of substrate at 300 dpi with a two dot
    # module.
    entry = resolve("GS1-128")
    dots = entry.width_dots(50, module_dots=2)
    assert dots > 1200
    assert dots / 300 > 4


def test_the_data_matrix_table_is_sorted_and_increasing():
    sides = [side for side, _ in DATA_MATRIX_SIDES]
    capacities = [capacity for _, capacity in DATA_MATRIX_SIDES]
    assert sides == sorted(sides)
    assert capacities == sorted(capacities)


def test_a_matrix_grows_in_steps_not_smoothly():
    assert data_matrix_side(1) == 10
    assert data_matrix_side(6) == 10
    assert data_matrix_side(7) == 12
    assert data_matrix_side(10) == 12
    assert data_matrix_side(11) == 14


def test_the_shipped_element_string_in_a_data_matrix():
    # (01)00125682945601(21)0001 encodes to 22 characters, which needs the
    # 16 module version because the 14 module one only holds 16.
    assert data_matrix_side(22) == 16
    assert data_matrix_side(16) == 14
    assert data_matrix_side(17) == 16


def test_a_matrix_symbology_reports_its_side_not_a_rate():
    entry = resolve("GS1 DataMatrix")
    assert entry.modules_for(22) == 16
    assert entry.modules_for(23) == 16
    assert entry.modules_for(25) == 18


def test_asking_for_one_more_character_can_cost_a_whole_version():
    entry = resolve("GS1 DataMatrix")
    assert entry.modules_for(24) == 16
    assert entry.modules_for(25) == 18


def test_a_qr_symbol_uses_its_own_version_table():
    entry = resolve("QR")
    assert entry.modules_for(41) == 21
    assert entry.modules_for(42) == 25


def test_a_matrix_symbology_with_no_table_says_so():
    from v4t_setup.gs1.symbology import Symbology

    entry = Symbology("Made up", Shape.MATRIX, Encoding.BINARY, 1, 100)
    with pytest.raises(Gs1Error, match="no version table"):
        entry.modules_for(10)


def test_more_data_than_a_square_will_hold_is_refused():
    with pytest.raises(Gs1Error, match="more data than this symbology") as excinfo:
        data_matrix_side(4000)
    assert excinfo.value.context["symbology"] == "DataMatrix"
    assert excinfo.value.context["largest"] == 3116


def test_a_linear_symbol_always_fits_the_head():
    # Its width runs along the product, not across the head.
    assert fits_head(resolve("GS1-128"), 80, swath_dots=128)


def test_a_small_matrix_fits_a_600(profile):
    swath = TerminalModel.SJET_600.swath_dots
    assert fits_head(resolve("GS1 DataMatrix"), 22, swath_dots=swath)


def test_the_same_matrix_does_not_fit_a_mini():
    swath = TerminalModel.SJET_MINI.swath_dots
    assert swath == 128
    # 18 modules at two dots is 36, which fits. Push it until it does not.
    assert fits_head(resolve("GS1 DataMatrix"), 22, swath_dots=swath)
    assert not fits_head(resolve("GS1 DataMatrix"), 2000, swath_dots=swath)


def test_a_wider_module_stops_a_matrix_fitting():
    swath = TerminalModel.SJET_MINI.swath_dots
    assert fits_head(resolve("GS1 DataMatrix"), 500, swath_dots=swath, module_dots=2)
    assert not fits_head(resolve("GS1 DataMatrix"), 500, swath_dots=swath, module_dots=4)


def test_describe_a_fixed_symbology():
    assert resolve("EAN-13").describe() == "EAN-13 (linear, numeric, 13)"


def test_describe_a_variable_one():
    assert resolve("GS1-128").describe() == "GS1-128 (linear, alphanumeric, 1..48)"


def test_describe_a_matrix_one():
    assert resolve("QR").describe().startswith("QR (matrix, binary,")


@pytest.mark.parametrize("entry", all_symbologies(), ids=lambda e: e.name)
def test_every_entry_is_self_consistent(entry):
    assert entry.min_characters >= 1
    assert entry.max_characters >= entry.min_characters
    assert entry.modules_for(entry.min_characters) > 0
    if entry.shape is Shape.MATRIX:
        assert entry.encoding is Encoding.BINARY
