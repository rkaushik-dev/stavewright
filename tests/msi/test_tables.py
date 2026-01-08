from __future__ import annotations

import struct
from pathlib import Path

import pytest

from v4t_setup.msi.cfb import CompoundFile, read_compound_file
from v4t_setup.msi.names import encode_stream_name
from v4t_setup.msi.tables import (
    LONG_REF_BYTES,
    SHORT_REF_BYTES,
    TYPE_KEY,
    TYPE_NULLABLE,
    TYPE_STRING,
    Column,
    MsiDatabase,
    MsiError,
    StringPool,
    decode_rows,
    read_database,
    read_msi,
    read_string_pool,
    stream_names,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MSI = REPO_ROOT / "msi-quattro" / "msi-Jet600.msi"

UPGRADE_CODE = "{7418C8BB-3B78-4FF6-AC4A-665158841F31}"


@pytest.fixture(scope="module")
def db() -> MsiDatabase:
    return read_msi(MSI)


def pool_of(*strings: str, long_refs: bool = False) -> StringPool:
    data = b"".join(text.encode("cp1252") for text in strings)
    header = struct.pack("<HH", 1252, 0x8000 if long_refs else 0)
    entries = b"".join(struct.pack("<HH", len(text.encode("cp1252")), 1) for text in strings)
    return read_string_pool(header + entries, data)


def test_reading_the_shipped_installer(db):
    assert db.source.name == "msi-Jet600.msi"
    assert len(db) == 32


def test_it_describes_itself(db):
    assert db.describe().startswith("msi-Jet600.msi: 32 tables,")


def test_the_string_pool_is_cp1252(db):
    assert db.pool.codepage == 1252


def test_the_shipped_database_uses_short_string_references(db):
    assert db.pool.bytes_per_ref == SHORT_REF_BYTES


def test_index_zero_is_null(db):
    assert db.pool.get(0) is None


def test_a_reference_past_the_end_of_the_pool_is_refused(db):
    with pytest.raises(MsiError, match="past the end of the pool"):
        db.pool.get(len(db.pool) + 10)


def test_the_tables_that_matter_are_there(db):
    for name in ("Directory", "Component", "Feature", "File", "Registry", "Upgrade"):
        assert name in db


def test_the_internal_tables_are_not_exposed_as_tables(db):
    assert not any(name.startswith("_") for name in db.table_names)


def test_asking_for_a_table_that_is_not_there(db):
    with pytest.raises(MsiError, match="no such table") as caught:
        db.get("Nonsense")
    assert "Directory" in caught.value.context["known"]


def test_the_directory_table_has_its_three_columns(db):
    assert db.get("Directory").column_names == [
        "Directory",
        "Directory_Parent",
        "DefaultDir",
    ]


def test_the_directory_table_has_the_rows_the_project_declares(db):
    assert len(db.get("Directory")) == 539


def test_the_root_directory_row_reads_correctly(db):
    root = next(row for row in db.get("Directory") if row["Directory"] == "TARGETDIR")
    assert root["Directory_Parent"] is None
    assert root["DefaultDir"] == "SourceDir"


def test_a_packed_default_dir_survives(db):
    row = next(row for row in db.get("Directory") if row["Directory"] == "ProgramMenuFolder")
    assert "|" in str(row["DefaultDir"])


def test_the_file_table_carries_real_sizes(db):
    sizes = [row["FileSize"] for row in db.get("File")]
    assert all(isinstance(size, int) and size > 0 for size in sizes)


def test_a_four_byte_integer_is_unbiased_correctly(db):
    # 632656 bytes, which is msvcr80.dll. Read raw it would come back as a
    # number over two billion.
    row = next(row for row in db.get("File") if row["FileName"].endswith("msvcr80.dll"))
    assert row["FileSize"] == 632656


def test_a_two_byte_integer_is_unbiased_correctly(db):
    sequences = [row["Sequence"] for row in db.get("File")]
    assert min(sequences) == 1
    assert max(sequences) < 1000


def test_a_null_integer_reads_as_none(db):
    row = next(row for row in db.get("File") if row["Attributes"] is None)
    assert row["Attributes"] is None


def test_the_upgrade_table_carries_the_products_own_code(db):
    row = db.get("Upgrade").rows[0]
    assert row["UpgradeCode"] == UPGRADE_CODE
    assert row["ActionProperty"] == "NEWERPRODUCTFOUND"


def test_the_product_properties_read(db):
    assert db.property("ProductName") == "msi-Jet600"
    assert db.property("ProductVersion") == "1.0.0"
    assert db.property("UpgradeCode") == UPGRADE_CODE


def test_a_property_that_is_not_set(db):
    assert db.property("NotAProperty") is None


def test_a_database_with_no_property_table_answers_none(db):
    without = MsiDatabase(pool=db.pool, tables={})
    assert without.property("ProductName") is None


def test_the_component_table_agrees_with_feature_components(db):
    components = {row["Component"] for row in db.get("Component")}
    referenced = {row["Component_"] for row in db.get("FeatureComponents")}
    assert referenced <= components


def test_every_file_belongs_to_a_component_that_exists(db):
    components = {row["Component"] for row in db.get("Component")}
    assert {row["Component_"] for row in db.get("File")} <= components


def test_reading_a_column(db):
    names = db.get("Directory").column("Directory")
    assert "TARGETDIR" in names
    assert len(names) == len(db.get("Directory"))


def test_reading_a_column_that_is_not_there(db):
    with pytest.raises(MsiError, match="no such column") as caught:
        db.get("Directory").column("Nonsense")
    assert caught.value.context["known"] == ["Directory", "Directory_Parent", "DefaultDir"]


def test_a_table_iterates_its_rows(db):
    table = db.get("Upgrade")
    assert list(iter(table)) == list(table.rows)


def test_the_key_columns_are_marked(db):
    assert db.get("Directory").key_columns == ["Directory"]


def test_a_table_describes_itself(db):
    assert db.get("Upgrade").describe() == "Upgrade: 1 rows, 7 columns"


def test_the_column_descriptions_read(db):
    described = [column.describe() for column in db.get("Feature").columns]
    assert described[0] == "Feature string(38) key"
    assert "int2" in described[4]


def test_a_string_column_takes_the_reference_width():
    column = Column(number=1, name="x", type_word=TYPE_STRING | 64)
    assert column.width(SHORT_REF_BYTES) == 2
    assert column.width(LONG_REF_BYTES) == 3


def test_an_integer_column_takes_its_declared_width():
    assert Column(number=1, name="x", type_word=2).width(SHORT_REF_BYTES) == 2
    assert Column(number=1, name="x", type_word=4).width(SHORT_REF_BYTES) == 4


def test_an_integer_column_of_an_odd_declared_size_falls_back_to_two():
    assert Column(number=1, name="x", type_word=1).width(SHORT_REF_BYTES) == 2


def test_the_type_flags_read():
    column = Column(number=1, name="x", type_word=TYPE_STRING | TYPE_KEY | TYPE_NULLABLE | 8)
    assert column.is_string
    assert column.is_key
    assert column.is_nullable
    assert column.declared_size == 8


def test_stream_names_come_back_decoded_and_sorted(db):
    names = stream_names(read_compound_file(MSI))
    assert names == sorted(names)
    assert "!Directory" in names


def test_a_short_string_pool_is_refused():
    with pytest.raises(MsiError, match="too short"):
        read_string_pool(b"\x00\x00", b"")


def test_a_pool_that_wants_more_bytes_than_there_are():
    header = struct.pack("<HH", 1252, 0)
    entry = struct.pack("<HH", 40, 1)
    with pytest.raises(MsiError, match="more bytes than the data stream has"):
        read_string_pool(header + entry, b"short")


def test_a_pool_reads_its_strings_in_order():
    pool = pool_of("Directory", "Component", "Feature")
    assert pool.get(1) == "Directory"
    assert pool.get(3) == "Feature"


def test_a_hole_in_the_pool_still_takes_an_index():
    header = struct.pack("<HH", 1252, 0)
    entries = struct.pack("<HH", 3, 1) + struct.pack("<HH", 0, 0) + struct.pack("<HH", 4, 1)
    pool = read_string_pool(header + entries, b"abcdefg")
    assert pool.get(1) == "abc"
    assert pool.get(2) is None or pool.strings[2] == ""
    assert pool.get(3) == "defg"


def test_the_long_reference_flag_is_read():
    assert pool_of("a", long_refs=True).bytes_per_ref == LONG_REF_BYTES
    assert pool_of("a").bytes_per_ref == SHORT_REF_BYTES


def test_the_flag_bit_is_not_taken_as_part_of_the_codepage():
    assert pool_of("a", long_refs=True).codepage == 1252


def test_decoding_rows_is_column_major():
    pool = pool_of("one", "two", "three", "four")
    columns = (
        Column(number=1, name="A", type_word=TYPE_STRING | 64),
        Column(number=2, name="B", type_word=TYPE_STRING | 64),
    )
    # Column A holds strings 1 and 2, column B holds 3 and 4.
    raw = struct.pack("<HHHH", 1, 2, 3, 4)
    rows = decode_rows(raw, columns, pool)
    assert rows == ({"A": "one", "B": "three"}, {"A": "two", "B": "four"})


def test_decoding_an_empty_stream_gives_no_rows():
    columns = (Column(number=1, name="A", type_word=TYPE_STRING | 64),)
    assert decode_rows(b"", columns, pool_of("x")) == ()


def test_decoding_with_no_columns_gives_no_rows():
    assert decode_rows(b"\x01\x00", (), pool_of("x")) == ()


def test_a_partial_final_row_is_dropped():
    columns = (Column(number=1, name="A", type_word=TYPE_STRING | 64),)
    assert len(decode_rows(b"\x01\x00\x02", columns, pool_of("x", "y"))) == 1


def test_a_three_byte_reference_is_read_little_endian():
    strings = [f"s{n}" for n in range(1, 301)]
    pool = pool_of(*strings, long_refs=True)
    columns = (Column(number=1, name="A", type_word=TYPE_STRING | 64),)
    rows = decode_rows(bytes([0x2C, 0x01, 0x00]), columns, pool)
    assert rows[0]["A"] == "s300"


def test_a_compound_file_that_is_not_an_installer(tmp_path):
    # Build a minimal compound file by damaging the real one's directory: drop
    # the string pool and the reader should say what is missing rather than
    # producing an empty database.
    source = read_compound_file(MSI)
    without = _without_stream(source, "!_StringPool")
    with pytest.raises(MsiError, match="not an installer database") as caught:
        read_database(without)
    assert caught.value.context["missing"] == "!_StringPool"


def _without_stream(source: CompoundFile, name: str) -> CompoundFile:
    """A copy of *source* with one stream renamed out of the way."""
    raw = bytearray(source.raw)
    encoded = encode_stream_name(name)
    entry = source.find(encoded)
    assert entry is not None
    directory = struct.unpack_from("<I", raw, 48)[0]
    base = (directory + 1) * 512 + entry.index * 128
    replacement = "zzz".encode("utf-16-le")
    raw[base : base + len(replacement)] = replacement
    struct.pack_into("<H", raw, base + 64, len(replacement) + 2)
    return CompoundFile(bytes(raw))
