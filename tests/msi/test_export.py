from __future__ import annotations

from pathlib import Path

import pytest

from v4t_setup.msi.export import (
    ENCODING,
    SUFFIX,
    ExportError,
    cell,
    escape,
    export,
    parse_idt,
    to_idt,
    type_code,
    unescape,
    write_table,
)
from v4t_setup.msi.tables import (
    TYPE_KEY,
    TYPE_NULLABLE,
    TYPE_STRING,
    Column,
    MsiDatabase,
    StringPool,
    Table,
    read_msi,
)

MSI = Path(__file__).resolve().parents[2] / "msi-quattro" / "msi-Jet600.msi"
EMPTY_POOL = StringPool(codepage=1252, strings=("",), bytes_per_ref=2)


@pytest.fixture(scope="module")
def db() -> MsiDatabase:
    return read_msi(MSI)


def table(name="Thing", columns=None, rows=()) -> Table:
    columns = columns or (
        Column(number=1, name="Key", type_word=TYPE_STRING | TYPE_KEY | 72),
        Column(number=2, name="Value", type_word=TYPE_STRING | TYPE_NULLABLE | 255),
    )
    return Table(name=name, columns=tuple(columns), rows=tuple(rows))


@pytest.mark.parametrize(
    ("raw", "escaped"),
    [
        ("plain", "plain"),
        ("with\ttab", "with\\ttab"),
        ("with\nnewline", "with\\nnewline"),
        ("with\r\nboth", "with\\r\\nboth"),
        (r"C:\Sellenis", "C:\\\\Sellenis"),
        ("", ""),
    ],
)
def test_escaping(raw, escaped):
    assert escape(raw) == escaped


@pytest.mark.parametrize(
    "raw",
    ["plain", "with\ttab", "with\nnewline", r"C:\Sellenis\S-jet600", "\\t is not a tab"],
)
def test_escaping_round_trips(raw):
    assert unescape(escape(raw)) == raw


def test_a_doubled_backslash_survives_two_round_trips():
    # The classic failure: escaping the backslash last so every other escape
    # gets escaped again.
    once = escape(r"a\tb")
    assert unescape(once) == r"a\tb"
    assert unescape(unescape(escape(once))) == r"a\tb"


def test_a_null_is_the_empty_cell():
    assert cell(None) == ""


def test_a_number_is_written_as_text():
    assert cell(632656) == "632656"


def test_type_codes():
    assert type_code(Column(number=1, name="x", type_word=TYPE_STRING | 72)) == "s72"
    assert type_code(Column(number=1, name="x", type_word=2)) == "i2"


def test_a_nullable_column_takes_a_capital():
    column = Column(number=1, name="x", type_word=TYPE_STRING | TYPE_NULLABLE | 255)
    assert type_code(column) == "S255"


def test_the_three_header_lines():
    lines = to_idt(table()).split("\r\n")
    assert lines[0] == "Key\tValue"
    assert lines[1] == "s72\tS255"
    assert lines[2] == "Thing\tKey"


def test_a_table_with_no_key_column_names_its_first():
    plain = table(
        columns=(Column(number=1, name="Only", type_word=TYPE_STRING | 72),),
    )
    assert to_idt(plain).split("\r\n")[2] == "Thing\tOnly"


def test_the_rows_follow_the_header():
    document = to_idt(table(rows=({"Key": "a", "Value": "one"},)))
    assert document.split("\r\n")[3] == "a\tone"


def test_a_null_cell_is_empty():
    document = to_idt(table(rows=({"Key": "a", "Value": None},)))
    assert document.split("\r\n")[3] == "a\t"


def test_the_document_ends_with_a_line_ending():
    assert to_idt(table()).endswith("\r\n")


def test_the_line_endings_are_the_ones_orca_writes():
    assert "\r\n" in to_idt(table())
    assert "\n\n" not in to_idt(table())


def test_a_table_with_no_columns_is_refused():
    with pytest.raises(ExportError, match="no columns"):
        to_idt(Table(name="Empty"))


def test_a_hand_built_table_round_trips():
    original = table(
        rows=(
            {"Key": "a", "Value": "one"},
            {"Key": "b", "Value": None},
            {"Key": "c", "Value": "with\ttab"},
        )
    )
    back = parse_idt(to_idt(original))
    assert back.name == original.name
    assert back.column_names == original.column_names
    assert back.rows == original.rows


def test_the_key_column_survives_the_round_trip():
    assert parse_idt(to_idt(table())).key_columns == ["Key"]


def test_integers_come_back_as_integers():
    numeric = table(
        columns=(
            Column(number=1, name="Id", type_word=TYPE_KEY | 2),
            Column(number=2, name="Size", type_word=4),
        ),
        rows=({"Id": 1, "Size": 632656},),
    )
    assert parse_idt(to_idt(numeric)).rows[0] == {"Id": 1, "Size": 632656}


def test_a_document_that_is_too_short():
    with pytest.raises(ExportError, match="three header lines"):
        parse_idt("one\ntwo\n")


def test_a_type_line_that_does_not_match():
    with pytest.raises(ExportError, match="does not match the name line"):
        parse_idt("a\tb\r\ns72\r\nThing\ta\r\n")


def test_a_row_with_the_wrong_number_of_cells():
    document = to_idt(table()) + "only-one-cell\r\n"
    with pytest.raises(ExportError, match="as many cells") as caught:
        parse_idt(document)
    assert caught.value.context["columns"] == 2


@pytest.mark.parametrize("name", ["Directory", "Component", "File", "Registry", "Upgrade"])
def test_every_shipped_table_round_trips(db, name):
    original = db.get(name)
    back = parse_idt(to_idt(original))
    assert back.rows == original.rows
    assert back.column_names == original.column_names


def test_the_registry_table_carries_backslashes_and_survives(db):
    document = to_idt(db.get("Registry"))
    assert "\\\\" in document
    assert parse_idt(document).rows == db.get("Registry").rows


def test_exporting_every_table(db, tmp_path):
    report = export(db, tmp_path)
    assert report.tables == tuple(db.table_names)
    assert report.rows == sum(len(db.get(name)) for name in db.table_names)
    assert (tmp_path / f"Directory{SUFFIX}").is_file()


def test_exporting_names_the_files_after_the_tables(db, tmp_path):
    export(db, tmp_path, only=["Upgrade"])
    assert [path.name for path in tmp_path.iterdir()] == ["Upgrade.idt"]


def test_exporting_only_some(db, tmp_path):
    report = export(db, tmp_path, only=["Registry", "Directory"])
    assert report.tables == ("Directory", "Registry")


def test_exporting_a_table_that_is_not_there(db, tmp_path):
    with pytest.raises(ExportError, match="no such table") as caught:
        export(db, tmp_path, only=["Nonsense"])
    assert caught.value.context["missing"] == ["Nonsense"]


def test_exporting_creates_the_directory(db, tmp_path):
    root = tmp_path / "a" / "b"
    export(db, root, only=["Upgrade"])
    assert root.is_dir()


def test_the_written_file_keeps_its_line_endings(db, tmp_path):
    path = write_table(db.get("Upgrade"), tmp_path)
    assert b"\r\n" in path.read_bytes()


def test_the_written_file_is_in_the_installer_codepage(db, tmp_path):
    path = write_table(db.get("Property"), tmp_path)
    assert path.read_bytes().decode(ENCODING)


def test_a_written_file_reads_back_as_the_same_table(db, tmp_path):
    path = write_table(db.get("Feature"), tmp_path)
    back = parse_idt(path.read_bytes().decode(ENCODING))
    assert back.rows == db.get("Feature").rows


def test_the_report_describes_itself(db, tmp_path):
    report = export(db, tmp_path, only=["Upgrade"])
    assert report.describe().endswith("1 tables, 1 rows")


def test_an_empty_database_exports_nothing(tmp_path):
    empty = MsiDatabase(pool=EMPTY_POOL, tables={})
    report = export(empty, tmp_path)
    assert report.tables == ()
    assert report.rows == 0
