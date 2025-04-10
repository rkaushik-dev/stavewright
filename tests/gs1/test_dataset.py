from __future__ import annotations

import pytest

from v4t_setup.errors import Gs1Error
from v4t_setup.gs1.dataset import (
    BOM,
    EXPECTED_COLUMNS,
    Dataset,
    parse_dataset,
    read_dataset,
)

HEADER = ",".join(EXPECTED_COLUMNS)
FULL_ROW = (
    "01,0001,AD47-001,AB:08,00125682945601,A,"
    "(01)00125682945601(21)0001,(01)01001000173250(15)150807,125682945601"
)
SHORT_ROW = (
    "23,0023,AD47-001,AC:00,00125682945601,G,"
    "(01)00125682945601(21)0023,(01)01001000173250(15)150807"
)


@pytest.fixture
def shipped(request):
    return read_dataset(
        request.path.parent.parent.parent / "Matricode" / "Quattro" / "Templates" / "GS1-1.txt"
    )


def test_a_full_row_reads_every_column():
    row = parse_dataset(f"{HEADER}\n{FULL_ROW}\n")[0]
    assert row.index == "01"
    assert row.serial == "0001"
    assert row.batch == "AD47-001"
    assert row.lot_code == "AB:08"
    assert row.gtin == "00125682945601"
    assert row.alignment_mark == "A"
    assert row.item_element_string == "(01)00125682945601(21)0001"
    assert row.case_element_string == "(01)01001000173250(15)150807"
    assert row.ean13 == "125682945601"


def test_a_full_row_is_not_ragged():
    row = parse_dataset(f"{HEADER}\n{FULL_ROW}\n")[0]
    assert not row.is_ragged
    assert row.width == 9
    assert row.missing_columns == ()


def test_a_short_row_is_ragged_and_says_what_is_missing():
    row = parse_dataset(f"{HEADER}\n{SHORT_ROW}\n")[0]
    assert row.is_ragged
    assert row.width == 8
    assert row.missing_columns == ("EAN13",)
    assert row.ean13 is None


def test_a_short_row_still_reads_its_present_columns():
    row = parse_dataset(f"{HEADER}\n{SHORT_ROW}\n")[0]
    assert row.serial == "0023"
    assert row.case_element_string == "(01)01001000173250(15)150807"


def test_requiring_a_missing_column_reports_the_line():
    row = parse_dataset(f"{HEADER}\n{SHORT_ROW}\n")[0]
    with pytest.raises(Gs1Error, match="missing a column") as excinfo:
        row.require("EAN13")
    assert excinfo.value.context["line"] == 2
    assert excinfo.value.context["width"] == 8


def test_asking_for_a_column_that_does_not_exist():
    row = parse_dataset(f"{HEADER}\n{FULL_ROW}\n")[0]
    with pytest.raises(Gs1Error, match="no such dataset column"):
        row.get("SKU")


def test_line_numbers_start_at_two():
    dataset = parse_dataset(f"{HEADER}\n{FULL_ROW}\n{SHORT_ROW}\n")
    assert [row.line for row in dataset] == [2, 3]


def test_blank_lines_are_skipped_without_shifting_the_numbering():
    dataset = parse_dataset(f"{HEADER}\n{FULL_ROW}\n\n{SHORT_ROW}\n")
    assert [row.line for row in dataset] == [2, 4]


def test_crlf_endings_are_handled():
    dataset = parse_dataset(f"{HEADER}\r\n{FULL_ROW}\r\n")
    assert len(dataset) == 1
    assert dataset[0].ean13 == "125682945601"


def test_a_byte_order_mark_is_stripped():
    dataset = parse_dataset(f"{BOM}{HEADER}\n{FULL_ROW}\n")
    assert dataset.header == EXPECTED_COLUMNS


def test_trailing_blank_lines_do_not_become_rows():
    assert len(parse_dataset(f"{HEADER}\n{FULL_ROW}\n\n\n")) == 1


def test_an_empty_file_is_refused():
    with pytest.raises(Gs1Error, match="dataset is empty"):
        parse_dataset("")


def test_a_wrong_header_is_refused():
    with pytest.raises(Gs1Error, match="header does not match") as excinfo:
        parse_dataset("NDX,SERIAL\n01,0001\n")
    assert excinfo.value.context["found"] == ["NDX", "SERIAL"]


def test_a_reordered_header_is_refused():
    reordered = ",".join(["SERIAL", "NDX", *EXPECTED_COLUMNS[2:]])
    with pytest.raises(Gs1Error, match="header does not match"):
        parse_dataset(f"{reordered}\n")


def test_a_row_with_too_many_fields_is_refused():
    with pytest.raises(Gs1Error, match="more fields than the header") as excinfo:
        parse_dataset(f"{HEADER}\n{FULL_ROW},extra\n")
    assert excinfo.value.context["width"] == 10


def test_a_header_with_no_rows_is_a_valid_empty_dataset():
    dataset = parse_dataset(f"{HEADER}\n")
    assert len(dataset) == 0
    assert dataset.ragged_rows == []


def test_widths_counts_rows_by_width():
    dataset = parse_dataset(f"{HEADER}\n{FULL_ROW}\n{SHORT_ROW}\n{SHORT_ROW}\n")
    assert dataset.widths == {8: 2, 9: 1}


def test_column_extracts_a_whole_column():
    dataset = parse_dataset(f"{HEADER}\n{FULL_ROW}\n{SHORT_ROW}\n")
    assert dataset.column("SERIAL") == ["0001", "0023"]
    assert dataset.column("EAN13") == ["125682945601", None]


def test_indexing_and_iteration():
    dataset = parse_dataset(f"{HEADER}\n{FULL_ROW}\n{SHORT_ROW}\n")
    assert dataset[0].serial == "0001"
    assert [row.serial for row in dataset] == ["0001", "0023"]


def test_describe_an_in_memory_dataset():
    dataset = parse_dataset(f"{HEADER}\n{FULL_ROW}\n{SHORT_ROW}\n")
    assert dataset.describe() == "(in memory): 2 rows, 1 ragged"


def test_describe_with_no_ragged_rows():
    assert parse_dataset(f"{HEADER}\n{FULL_ROW}\n").describe() == "(in memory): 1 rows"


def test_an_empty_dataset_object():
    assert len(Dataset()) == 0
    assert Dataset().describe() == "(in memory): 0 rows"


def test_read_reports_a_missing_file(tmp_path):
    with pytest.raises(Gs1Error, match="cannot read dataset"):
        read_dataset(tmp_path / "nope.txt")


def test_a_windows_code_page_file_still_reads(tmp_path):
    path = tmp_path / "GS1-1.txt"
    row = FULL_ROW.replace("AD47-001", "AD47-00\xe9")
    path.write_bytes(f"{HEADER}\n{row}\n".encode("cp1252"))
    assert read_dataset(path)[0].batch == "AD47-00\xe9"


# The shipped file. These numbers move only when someone edits it.


def test_the_shipped_file_reads(shipped):
    assert shipped.header == EXPECTED_COLUMNS
    assert len(shipped) == 130


def test_the_shipped_file_has_thirty_two_ragged_rows(shipped):
    assert len(shipped.ragged_rows) == 32
    assert shipped.widths == {8: 32, 9: 98}


def test_every_ragged_row_is_only_missing_ean13(shipped):
    for row in shipped.ragged_rows:
        assert row.missing_columns == ("EAN13",)


def test_the_shipped_file_carries_one_gtin_throughout(shipped):
    assert {row.gtin for row in shipped} == {"00125682945601"}


def test_the_shipped_file_carries_one_batch_throughout(shipped):
    assert {row.batch for row in shipped} == {"AD47-001"}


def test_the_shipped_serials_repeat(shipped):
    serials = [row.serial for row in shipped]
    assert len(serials) == 130
    assert len(set(serials)) < len(serials)


def test_the_shipped_index_runs_past_the_serial_cycle(shipped):
    assert shipped[0].index == "01"
    assert shipped[-1].index == "100"


def test_describe_names_the_file(shipped):
    assert shipped.describe() == "GS1-1.txt: 130 rows, 32 ragged"
