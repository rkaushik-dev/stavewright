"""Reading the CSV data file a template marks from.

``Matricode/Quattro/Templates/GS1-1.txt`` is the shipped example. Its header is::

    NDX,SERIAL,BATCH,LOTCODE,GTIN,ADX,GS1 DATA,GS1,EAN13

and it has 130 data rows. Thirty two of those carry only eight fields because
the trailing ``EAN13`` value is missing, which is why the reader keeps track of
how wide each row actually was instead of assuming the header width.

The file is not a general purpose CSV. There are no quoted fields, no escapes
and no embedded commas, so the reader stays strict about that rather than
accepting a dialect the front end would then fail to read.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from stavewright.errors import Gs1Error

EXPECTED_COLUMNS = (
    "NDX",
    "SERIAL",
    "BATCH",
    "LOTCODE",
    "GTIN",
    "ADX",
    "GS1 DATA",
    "GS1",
    "EAN13",
)
"""The header the front end writes. Order matters, the reader is positional."""

OPTIONAL_COLUMNS = frozenset({"EAN13"})
"""Trailing columns a row is allowed to leave off entirely."""

BOM = "﻿"


@dataclass(frozen=True, slots=True)
class DatasetRow:
    """One data row, addressed by column name."""

    line: int
    values: tuple[str, ...]
    width: int

    def get(self, column: str) -> str | None:
        """Read a column by name, or None when this row did not carry it."""
        try:
            index = EXPECTED_COLUMNS.index(column)
        except ValueError:
            raise Gs1Error(
                "no such dataset column",
                context={"column": column, "known": list(EXPECTED_COLUMNS)},
            ) from None
        if index >= len(self.values):
            return None
        return self.values[index]

    def require(self, column: str) -> str:
        """Read a column that has to be present.

        :raises Gs1Error: when the row is too short to carry it.
        """
        value = self.get(column)
        if value is None:
            raise Gs1Error(
                "dataset row is missing a column",
                context={"line": self.line, "column": column, "width": self.width},
            )
        return value

    @property
    def is_ragged(self) -> bool:
        """True when the row carried fewer fields than the header declares."""
        return self.width < len(EXPECTED_COLUMNS)

    @property
    def missing_columns(self) -> tuple[str, ...]:
        """Trailing columns this row left off."""
        return EXPECTED_COLUMNS[self.width :]

    @property
    def index(self) -> str:
        return self.require("NDX")

    @property
    def serial(self) -> str:
        return self.require("SERIAL")

    @property
    def batch(self) -> str:
        return self.require("BATCH")

    @property
    def lot_code(self) -> str:
        return self.require("LOTCODE")

    @property
    def gtin(self) -> str:
        return self.require("GTIN")

    @property
    def alignment_mark(self) -> str:
        """The ADX column. One letter that selects a head alignment pattern."""
        return self.require("ADX")

    @property
    def item_element_string(self) -> str:
        """The ``GS1 DATA`` column, which is what the item barcode encodes."""
        return self.require("GS1 DATA")

    @property
    def case_element_string(self) -> str:
        """The ``GS1`` column, which is what the case barcode encodes."""
        return self.require("GS1")

    @property
    def ean13(self) -> str | None:
        """The ``EAN13`` column, absent on the short rows."""
        return self.get("EAN13")


@dataclass
class Dataset:
    """A whole data file."""

    header: tuple[str, ...] = ()
    rows: list[DatasetRow] = field(default_factory=list)
    source: Path | None = None

    def __len__(self) -> int:
        return len(self.rows)

    def __iter__(self) -> Iterator[DatasetRow]:
        return iter(self.rows)

    def __getitem__(self, position: int) -> DatasetRow:
        return self.rows[position]

    @property
    def ragged_rows(self) -> list[DatasetRow]:
        """Rows that left a trailing column off."""
        return [row for row in self.rows if row.is_ragged]

    @property
    def widths(self) -> dict[int, int]:
        """How many rows had each width, keyed by width."""
        counts: dict[int, int] = {}
        for row in self.rows:
            counts[row.width] = counts.get(row.width, 0) + 1
        return dict(sorted(counts.items()))

    def column(self, name: str) -> list[str | None]:
        """Every value in *name*, in file order, with None for short rows."""
        return [row.get(name) for row in self.rows]

    def describe(self) -> str:
        """One line for a report header."""
        where = self.source.name if self.source else "(in memory)"
        ragged = len(self.ragged_rows)
        suffix = f", {ragged} ragged" if ragged else ""
        return f"{where}: {len(self.rows)} rows{suffix}"


def _strip_bom(text: str) -> str:
    return text[1:] if text.startswith(BOM) else text


def parse_dataset(text: str, *, source: Path | None = None) -> Dataset:
    """Parse the contents of a data file.

    :raises Gs1Error: when the header is missing, has the wrong columns, or a
        row carries more fields than the header does.
    """
    lines = _strip_bom(text).replace("\r\n", "\n").split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        raise Gs1Error("dataset is empty")

    header = tuple(next(csv.reader([lines[0]])))
    if header != EXPECTED_COLUMNS:
        raise Gs1Error(
            "dataset header does not match the expected columns",
            context={"found": list(header), "expected": list(EXPECTED_COLUMNS)},
        )

    rows: list[DatasetRow] = []
    for offset, raw in enumerate(lines[1:], start=2):
        if not raw.strip():
            continue
        values = tuple(next(csv.reader([raw])))
        if len(values) > len(EXPECTED_COLUMNS):
            raise Gs1Error(
                "dataset row has more fields than the header",
                context={"line": offset, "width": len(values), "header": len(EXPECTED_COLUMNS)},
            )
        rows.append(DatasetRow(line=offset, values=values, width=len(values)))

    return Dataset(header=header, rows=rows, source=source)


def read_dataset(path: Path) -> Dataset:
    """Read and parse a data file from disk.

    The front end writes these with a Windows code page rather than UTF-8, so
    the read falls back to cp1252 when the bytes are not valid UTF-8 rather
    than failing on a single accented batch code.

    :raises Gs1Error: when the file cannot be read or does not parse.
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise Gs1Error(
            "cannot read dataset",
            context={"path": str(path), "detail": str(exc)},
        ) from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")
    return parse_dataset(text, source=path)


__all__ = [
    "BOM",
    "EXPECTED_COLUMNS",
    "OPTIONAL_COLUMNS",
    "Dataset",
    "DatasetRow",
    "parse_dataset",
    "read_dataset",
]
