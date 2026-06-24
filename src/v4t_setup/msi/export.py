"""Writing an installer's tables out as text, the way Orca does.

An ``.idt`` is a tab separated file with a three line header: the column names,
the column types, and a line naming the table and its key columns. It is what
Orca exports and imports, which makes it the format everybody who has ever
edited an MSI already knows.

Two reasons this is worth having and neither is editing:

Diffing. Two builds of the same product differ in every byte of the MSI and in
almost none of the tables. Exported, they diff in a text tool, and the answer
to "what changed between build 12 and build 13" is a page rather than a shrug.

Review. A change to the Registry table is a change to what gets written to a
customer's machine, and that belongs in a pull request in a form a person can
read.

The escaping is the part worth being careful about. A tab ends a cell and a
newline ends a row, so a value carrying either has to be written some other
way, and MSI's answer is the same escape sequences C uses. Getting it wrong
produces a file that imports without complaint and is subtly wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from v4t_setup.errors import V4TError
from v4t_setup.msi.tables import (
    TYPE_KEY,
    TYPE_NULLABLE,
    TYPE_STRING,
    Column,
    MsiDatabase,
    Table,
)
from v4t_setup.util.fsops import atomic_write_bytes

SUFFIX = ".idt"
ENCODING = "cp1252"

ESCAPES = {
    "\t": "\\t",
    "\n": "\\n",
    "\r": "\\r",
    "\\": "\\\\",
}
UNESCAPES = {escaped: raw for raw, escaped in ESCAPES.items()}


class ExportError(V4TError):
    """A table cannot be written out."""

    code = "MSI0002"


@dataclass(frozen=True, slots=True)
class ExportReport:
    """What was written."""

    root: Path
    tables: tuple[str, ...] = ()
    rows: int = 0

    def describe(self) -> str:
        return f"{self.root}: {len(self.tables)} tables, {self.rows} rows"


def escape(value: str) -> str:
    """Escape one cell.

    The backslash has to go first or every other escape gets escaped again,
    which is the classic way to write a file that round trips once and not
    twice.
    """
    out = value.replace("\\", "\\\\")
    for raw, escaped in ESCAPES.items():
        if raw != "\\":
            out = out.replace(raw, escaped)
    return out


def unescape(value: str) -> str:
    """Undo :func:`escape`, left to right so a doubled backslash stays one."""
    out: list[str] = []
    index = 0
    while index < len(value):
        pair = value[index : index + 2]
        if pair in UNESCAPES:
            out.append(UNESCAPES[pair])
            index += 2
        else:
            out.append(value[index])
            index += 1
    return "".join(out)


def type_code(column: Column) -> str:
    """The type letter and width Orca writes in the second header line.

    ``s`` is a string, ``i`` an integer, and an upper case letter means the
    column may be null. A zero width string is one with no declared limit.
    """
    letter = "s" if column.is_string else "i"
    if column.is_nullable:
        letter = letter.upper()
    return f"{letter}{column.declared_size}"


def cell(value: object) -> str:
    """One value as text. None is the empty string, which is how a null reads."""
    if value is None:
        return ""
    return escape(str(value))


def to_idt(table: Table) -> str:
    """One table as an ``.idt`` document."""
    if not table.columns:
        raise ExportError(
            "a table with no columns cannot be written out",
            context={"table": table.name},
        )
    keys = table.key_columns or [table.columns[0].name]
    lines = [
        "\t".join(column.name for column in table.columns),
        "\t".join(type_code(column) for column in table.columns),
        "\t".join([table.name, *keys]),
    ]
    lines.extend(
        "\t".join(cell(row.get(column.name)) for column in table.columns)
        for row in table
    )
    # Orca writes CRLF and so does this, because the files are compared against
    # ones Orca produced and a whole file diff is not a useful diff.
    return "\r\n".join(lines) + "\r\n"


def write_table(table: Table, root: Path) -> Path:
    """Write one table into *root*, named after itself.

    Through :func:`atomic_write_bytes` rather than the text flavour, because
    that one rewrites CRLF to LF on the way past. Everywhere else in this
    toolkit that is what you want. Here the line ending is part of the format.
    """
    path = root / f"{table.name}{SUFFIX}"
    atomic_write_bytes(path, to_idt(table).encode(ENCODING, "replace"))
    return path


def export(database: MsiDatabase, root: Path, *, only: list[str] | None = None) -> ExportReport:
    """Write every table out.

    :param only: table names to write. Anything named and absent is an error,
        because a caller asking for the Registry table and silently getting
        nothing is the worst outcome here.
    :raises ExportError: when a named table is not in the database.
    """
    names = only if only is not None else database.table_names
    missing = sorted(name for name in names if name not in database)
    if missing:
        raise ExportError(
            "the database has no such table",
            context={"missing": missing, "known": database.table_names},
        )

    root.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    rows = 0
    for name in sorted(names):
        table = database.get(name)
        write_table(table, root)
        written.append(name)
        rows += len(table)
    return ExportReport(root=root, tables=tuple(written), rows=rows)


def parse_idt(text: str) -> Table:
    """Read one back, which is what makes the round trip testable.

    :raises ExportError: when the document has fewer than three header lines,
        or a row has a different number of cells from the header.
    """
    lines = text.replace("\r\n", "\n").rstrip("\n").split("\n")
    if len(lines) < 3:
        raise ExportError("an idt file needs three header lines", context={"lines": len(lines)})

    names = lines[0].split("\t")
    codes = lines[1].split("\t")
    heading = lines[2].split("\t")
    if len(codes) != len(names):
        raise ExportError(
            "the type line does not match the name line",
            context={"names": len(names), "types": len(codes)},
        )

    keys = set(heading[1:])
    columns = tuple(
        Column(
            number=index,
            name=name,
            type_word=_type_word(code, name in keys),
        )
        for index, (name, code) in enumerate(zip(names, codes, strict=True), start=1)
    )
    rows = []
    for number, line in enumerate(lines[3:], start=4):
        cells = line.split("\t")
        if len(cells) != len(names):
            raise ExportError(
                "a row does not have as many cells as there are columns",
                context={"line": number, "cells": len(cells), "columns": len(names)},
            )
        rows.append(
            {
                column.name: _value(column, unescape(text))
                for column, text in zip(columns, cells, strict=True)
            }
        )
    return Table(name=heading[0], columns=columns, rows=tuple(rows))


def _type_word(code: str, is_key: bool) -> int:
    letter = code[:1]
    size = int(code[1:] or 0)
    word = size
    if letter.lower() == "s":
        word |= TYPE_STRING
    if letter.isupper():
        word |= TYPE_NULLABLE
    if is_key:
        word |= TYPE_KEY
    return word


def _value(column: Column, text: str) -> str | int | None:
    if text == "":
        return None
    if column.is_string:
        return text
    try:
        return int(text)
    except ValueError:
        return text


__all__ = [
    "ENCODING",
    "ESCAPES",
    "SUFFIX",
    "ExportError",
    "ExportReport",
    "cell",
    "escape",
    "export",
    "parse_idt",
    "to_idt",
    "type_code",
    "unescape",
    "write_table",
]
