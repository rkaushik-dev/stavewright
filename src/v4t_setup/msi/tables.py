"""The installer database inside the compound file.

Four streams describe everything else, and they have to be read in this order:

``!_StringPool`` and ``!_StringData``
    Every string in the database, once. The pool is pairs of 16 bit words, a
    length and a reference count; the data is the bytes those lengths cut up.
    Every other table stores strings as an index into this.

``!_Tables``
    One column of string references. The table names.

``!_Columns``
    Four columns: which table, which position, what the column is called, and a
    type word. Without it a table stream is an undifferentiated block of bytes,
    because there is nothing in the stream itself saying where one column ends.

Then any table can be read. Rows are stored column major: every value of column
one, then every value of column two. A row is assembled by taking the nth value
out of each column, which is why the whole stream has to be in hand before a
single row can be produced.

Two details that are easy to get wrong and quiet when you do:

Integers are stored biased. A two byte column holds ``value + 0x8000`` so that
the sign works out, and reading it raw gives every number in the database an
offset of 32768. A zero means null rather than zero.

String references are two bytes in most databases and three in a large one, and
which it is comes out of a flag bit in the string pool's own header. Reading a
three byte database as two byte does not fail. It produces rows made of real
strings that belong to other rows.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from v4t_setup.errors import V4TError
from v4t_setup.msi.cfb import CompoundFile, read_compound_file
from v4t_setup.msi.names import decode_stream_name, encode_stream_name, table_name

STRING_POOL = "!_StringPool"
STRING_DATA = "!_StringData"
TABLES = "!_Tables"
COLUMNS = "!_Columns"

LONG_STRING_REFS = 0x8000
"""Set in the high word of the codepage when string references are 3 bytes."""

SHORT_REF_BYTES = 2
LONG_REF_BYTES = 3

TYPE_STRING = 0x0800
TYPE_NULLABLE = 0x1000
TYPE_KEY = 0x2000
TYPE_SIZE_MASK = 0x00FF

INT2_BIAS = 1 << 15
INT4_BIAS = 1 << 31

DEFAULT_CODEPAGE = 1252


class MsiError(V4TError):
    """The compound file is not an installer database, or is a damaged one."""

    code = "MSI0001"


@dataclass(frozen=True, slots=True)
class StringPool:
    """Every string in the database, by index. Index zero is the empty string."""

    codepage: int
    strings: tuple[str, ...]
    bytes_per_ref: int

    def __len__(self) -> int:
        return len(self.strings)

    def get(self, index: int) -> str | None:
        """The string at *index*, or None for a null reference.

        :raises MsiError: when the index is past the end of the pool, which
            means the reference width was read wrong.
        """
        if index == 0:
            return None
        if index >= len(self.strings):
            raise MsiError(
                "a string reference points past the end of the pool",
                context={"index": index, "strings": len(self.strings)},
            )
        return self.strings[index]


@dataclass(frozen=True, slots=True)
class Column:
    """One column of one table."""

    number: int
    name: str
    type_word: int

    @property
    def is_string(self) -> bool:
        return bool(self.type_word & TYPE_STRING)

    @property
    def is_nullable(self) -> bool:
        return bool(self.type_word & TYPE_NULLABLE)

    @property
    def is_key(self) -> bool:
        return bool(self.type_word & TYPE_KEY)

    @property
    def declared_size(self) -> int:
        """The low byte: a length for a string, a width for an integer."""
        return self.type_word & TYPE_SIZE_MASK

    def width(self, bytes_per_ref: int) -> int:
        """How many bytes this column takes up per row."""
        if self.is_string:
            return bytes_per_ref
        return 4 if self.declared_size == 4 else 2

    def describe(self) -> str:
        kind = f"string({self.declared_size})" if self.is_string else f"int{self.declared_size}"
        flags = "".join(
            mark for mark, on in (("key", self.is_key), ("null", self.is_nullable)) if on
        )
        return f"{self.name} {kind}{' ' + flags if flags else ''}"


Value = str | int | None
Row = dict[str, Value]


@dataclass(frozen=True, slots=True)
class Table:
    """One table, read."""

    name: str
    columns: tuple[Column, ...] = ()
    rows: tuple[Row, ...] = ()

    def __len__(self) -> int:
        return len(self.rows)

    def __iter__(self) -> Iterator[Row]:
        return iter(self.rows)

    @property
    def column_names(self) -> list[str]:
        return [column.name for column in self.columns]

    @property
    def key_columns(self) -> list[str]:
        return [column.name for column in self.columns if column.is_key]

    def column(self, name: str) -> list[Value]:
        """Every value in one column, in row order.

        :raises MsiError: when the table has no such column.
        """
        if name not in self.column_names:
            raise MsiError(
                "this table has no such column",
                context={"table": self.name, "column": name, "known": self.column_names},
            )
        return [row.get(name) for row in self.rows]

    def describe(self) -> str:
        return f"{self.name}: {len(self.rows)} rows, {len(self.columns)} columns"


def _words(raw: bytes) -> list[int]:
    return list(struct.unpack(f"<{len(raw) // 2}H", raw[: len(raw) // 2 * 2]))


def read_string_pool(pool: bytes, data: bytes) -> StringPool:
    """Cut *data* into strings the way *pool* says.

    :raises MsiError: when the pool is too short to carry a header, or asks for
        more bytes than the data stream has.
    """
    if len(pool) < 4:
        raise MsiError("the string pool is too short to hold its header")
    words = _words(pool)
    codepage = words[0] | ((words[1] & ~LONG_STRING_REFS) << 16)
    bytes_per_ref = LONG_REF_BYTES if words[1] & LONG_STRING_REFS else SHORT_REF_BYTES

    strings: list[str] = [""]
    encoding = _encoding(codepage)
    offset = 0
    index = 1
    while index * 2 + 1 < len(words):
        length = words[index * 2]
        refs = words[index * 2 + 1]
        if length == 0 and refs == 0:
            # A hole. It still consumes an index, because everything that
            # refers to a later string counted it.
            strings.append("")
            index += 1
            continue
        if length == 0:
            # A string over 64KB. The high half of its length was parked in
            # this entry's reference count and the real entry is the next one.
            if index * 2 + 3 >= len(words):
                raise MsiError("a long string entry runs off the end of the pool")
            length = (words[index * 2 + 3] << 16) + words[index * 2 + 2]
            index += 2
        else:
            index += 1
        if offset + length > len(data):
            raise MsiError(
                "the string pool asks for more bytes than the data stream has",
                context={"wanted": offset + length, "have": len(data)},
            )
        strings.append(data[offset : offset + length].decode(encoding, "replace"))
        offset += length
    return StringPool(
        codepage=codepage or DEFAULT_CODEPAGE,
        strings=tuple(strings),
        bytes_per_ref=bytes_per_ref,
    )


def _encoding(codepage: int) -> str:
    if codepage in (0, 1252):
        return "cp1252"
    if codepage == 65001:
        return "utf-8"
    return f"cp{codepage}"


def _read_ref(raw: bytes, offset: int, width: int) -> int:
    if width == LONG_REF_BYTES:
        return raw[offset] | (raw[offset + 1] << 8) | (raw[offset + 2] << 16)
    return int(struct.unpack_from("<H", raw, offset)[0])


def decode_rows(raw: bytes, columns: tuple[Column, ...], pool: StringPool) -> tuple[Row, ...]:
    """Turn one table stream into rows.

    Column major, so this reads down each column and assembles rows at the end.
    """
    widths = [column.width(pool.bytes_per_ref) for column in columns]
    row_width = sum(widths)
    if row_width == 0:
        return ()
    count = len(raw) // row_width

    values: list[list[Value]] = []
    base = 0
    for column, width in zip(columns, widths, strict=True):
        column_values: list[Value] = []
        for row in range(count):
            offset = base + row * width
            if column.is_string:
                column_values.append(pool.get(_read_ref(raw, offset, width)))
            elif width == 4:
                stored = int(struct.unpack_from("<I", raw, offset)[0])
                column_values.append(None if stored == 0 else stored - INT4_BIAS)
            else:
                stored = int(struct.unpack_from("<H", raw, offset)[0])
                column_values.append(None if stored == 0 else stored - INT2_BIAS)
        values.append(column_values)
        base += width * count

    return tuple(
        {column.name: values[position][row] for position, column in enumerate(columns)}
        for row in range(count)
    )


@dataclass
class MsiDatabase:
    """An installer database, read out of a compound file."""

    pool: StringPool
    tables: dict[str, Table] = field(default_factory=dict)
    source: Path | None = None

    def __len__(self) -> int:
        return len(self.tables)

    def __contains__(self, name: str) -> bool:
        return name in self.tables

    @property
    def table_names(self) -> list[str]:
        return sorted(self.tables)

    def get(self, name: str) -> Table:
        """One table.

        :raises MsiError: when the database has no such table.
        """
        try:
            return self.tables[name]
        except KeyError:
            raise MsiError(
                "the database has no such table",
                context={"table": name, "known": self.table_names},
            ) from None

    def property(self, name: str) -> str | None:
        """One row of the Property table, which is what most callers want."""
        if "Property" not in self.tables:
            return None
        for row in self.tables["Property"]:
            if row.get("Property") == name:
                value = row.get("Value")
                return value if isinstance(value, str) else None
        return None

    def describe(self) -> str:
        where = self.source.name if self.source else "(in memory)"
        rows = sum(len(table) for table in self.tables.values())
        return f"{where}: {len(self.tables)} tables, {rows} rows, {len(self.pool)} strings"


def _columns_for(rows: tuple[Row, ...]) -> dict[str, tuple[Column, ...]]:
    grouped: dict[str, list[Column]] = {}
    for row in rows:
        table = row.get("Table")
        number = row.get("Number")
        name = row.get("Name")
        type_word = row.get("Type")
        if not isinstance(table, str) or not isinstance(name, str):
            continue
        if not isinstance(number, int) or not isinstance(type_word, int):
            continue
        grouped.setdefault(table, []).append(
            Column(number=number, name=name, type_word=type_word & 0xFFFF)
        )
    return {
        table: tuple(sorted(columns, key=lambda column: column.number))
        for table, columns in grouped.items()
    }


COLUMNS_SCHEMA = (
    Column(number=1, name="Table", type_word=TYPE_STRING | TYPE_KEY | 64),
    Column(number=2, name="Number", type_word=TYPE_KEY | 2),
    Column(number=3, name="Name", type_word=TYPE_STRING | 64),
    Column(number=4, name="Type", type_word=2),
)
"""The shape of ``_Columns`` itself, which cannot be read out of ``_Columns``."""

TABLES_SCHEMA = (Column(number=1, name="Name", type_word=TYPE_STRING | TYPE_KEY | 64),)


def read_database(source: CompoundFile, path: Path | None = None) -> MsiDatabase:
    """Read every table in an installer database.

    :raises MsiError: when the compound file has no string pool, which means it
        is a compound file but not an installer.
    """
    for required in (STRING_POOL, STRING_DATA, TABLES, COLUMNS):
        if encode_stream_name(required) not in source:
            raise MsiError(
                "this compound file is not an installer database",
                context={"missing": required, "streams": len(source)},
            )

    pool = read_string_pool(
        source.read(encode_stream_name(STRING_POOL)),
        source.read(encode_stream_name(STRING_DATA)),
    )
    schema = _columns_for(
        decode_rows(source.read(encode_stream_name(COLUMNS)), COLUMNS_SCHEMA, pool)
    )

    tables: dict[str, Table] = {}
    for encoded in source.names:
        name = table_name(encoded)
        if name is None or name.startswith("_"):
            continue
        columns = schema.get(name)
        if columns is None:
            # A stream that decodes as a table and has no schema row. The
            # database is inconsistent, and skipping it beats inventing
            # columns for it.
            continue
        tables[name] = Table(
            name=name,
            columns=columns,
            rows=decode_rows(source.read(encoded), columns, pool),
        )
    return MsiDatabase(pool=pool, tables=tables, source=path)


def read_msi(path: Path) -> MsiDatabase:
    """Open an MSI and read its tables.

    :raises CompoundFileError: when the file is not a compound file.
    :raises MsiError: when it is one but not an installer database.
    """
    return read_database(read_compound_file(path), path)


def stream_names(source: CompoundFile) -> list[str]:
    """Every stream in a compound file, decoded, sorted."""
    return sorted(decode_stream_name(name) for name in source.names)


__all__ = [
    "COLUMNS",
    "COLUMNS_SCHEMA",
    "LONG_REF_BYTES",
    "SHORT_REF_BYTES",
    "STRING_DATA",
    "STRING_POOL",
    "TABLES",
    "TABLES_SCHEMA",
    "TYPE_KEY",
    "TYPE_NULLABLE",
    "TYPE_STRING",
    "Column",
    "MsiDatabase",
    "MsiError",
    "Row",
    "StringPool",
    "Table",
    "Value",
    "decode_rows",
    "read_database",
    "read_msi",
    "read_string_pool",
    "stream_names",
]
