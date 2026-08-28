"""Raw reader for the Advanced Installer project file.

Nothing in here interprets a value. A row comes back as the attributes that
were on the element, as strings, and the caller decides what they mean. That
keeps the reader honest about the one thing it can actually guarantee, which is
what the file said.

Tables are addressed either by their full ``cid`` or by the alias in
:data:`TABLE_ALIASES`, so ``project.rows("Directory")`` and
``project.rows("caphyon.advinst.msicomp.MsiDirsComponent")`` are the same call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from stavewright.errors import AipParseError, AipTableMissingError

CID_PREFIX = "caphyon.advinst.msicomp."

TABLE_ALIASES: dict[str, str] = {
    "Property": "MsiPropsComponent",
    "Directory": "MsiDirsComponent",
    "Component": "MsiCompsComponent",
    "Feature": "MsiFeatsComponent",
    "File": "MsiFilesComponent",
    "Registry": "MsiRegsComponent",
    "Shortcut": "MsiShortsComponent",
    "Upgrade": "MsiUpgradeComponent",
    "CreateFolder": "MsiCreateFolderComponent",
    "LaunchCondition": "MsiLaunchConditionsComponent",
    "AppSearch": "MsiAppSearchComponent",
    "RegLocator": "MsiRegLocatorComponent",
    "Icon": "MsiIconsComponent",
    "CustomAction": "MsiCustActComponent",
    "Binary": "MsiBinaryComponent",
    "DuplicateFile": "MsiDuplicateFileComponent",
    "Build": "BuildComponent",
    "ProjectOptions": "ProjectOptionsComponent",
}
"""Friendly name to the tail of the ``cid``, for the tables we actually read."""

_ALIAS_BY_TAIL = {tail: alias for alias, tail in TABLE_ALIASES.items()}

TRUE_WORDS = frozenset({"true", "yes", "1"})
FALSE_WORDS = frozenset({"false", "no", "0", ""})


@dataclass(frozen=True, slots=True)
class AipRow:
    """One ``<ROW>``, as the attributes it carried."""

    values: dict[str, str]
    table: str = ""

    def __contains__(self, column: str) -> bool:
        return column in self.values

    def get(self, column: str, default: str | None = None) -> str | None:
        """Read a column, or *default* when it was not present."""
        return self.values.get(column, default)

    def require(self, column: str) -> str:
        """Read a column that has to be there.

        :raises AipParseError: when the row does not carry it.
        """
        try:
            return self.values[column]
        except KeyError:
            raise AipParseError(
                "row is missing a required column",
                context={"table": self.table, "column": column, "row": dict(self.values)},
            ) from None

    def get_int(self, column: str, default: int | None = None) -> int | None:
        """Read a column as an integer, tolerating a blank cell.

        :raises AipParseError: when the cell is present but not a number.
        """
        raw = self.values.get(column)
        if raw is None or raw.strip() == "":
            return default
        try:
            return int(raw.strip())
        except ValueError:
            raise AipParseError(
                "column is not an integer",
                context={"table": self.table, "column": column, "value": raw},
            ) from None

    def get_bool(self, column: str, default: bool = False) -> bool:
        """Read one of the ``true``/``false`` cells Advanced Installer writes."""
        raw = self.values.get(column)
        if raw is None:
            return default
        lowered = raw.strip().lower()
        if lowered in TRUE_WORDS:
            return True
        if lowered in FALSE_WORDS:
            return False
        raise AipParseError(
            "column is not a boolean",
            context={"table": self.table, "column": column, "value": raw},
        )

    def get_list(self, column: str, separator: str = " ") -> list[str]:
        """Split a packed cell such as the feature row's ``Components``."""
        raw = self.values.get(column)
        if not raw:
            return []
        return [part for part in raw.split(separator) if part]


@dataclass(frozen=True, slots=True)
class AipTable:
    """One ``<COMPONENT>`` element and the rows inside it."""

    cid: str
    rows: tuple[AipRow, ...] = ()
    attributes: dict[str, str] = field(default_factory=dict)

    @property
    def tail(self) -> str:
        """The ``cid`` with the vendor prefix stripped."""
        return self.cid[len(CID_PREFIX) :] if self.cid.startswith(CID_PREFIX) else self.cid

    @property
    def alias(self) -> str | None:
        """The friendly name, when this is a table we know about."""
        return _ALIAS_BY_TAIL.get(self.tail)

    def __len__(self) -> int:
        return len(self.rows)

    def __iter__(self) -> Any:
        return iter(self.rows)

    def find(self, column: str, value: str) -> AipRow | None:
        """First row whose *column* equals *value*, or None."""
        for row in self.rows:
            if row.get(column) == value:
                return row
        return None

    def index_by(self, column: str) -> dict[str, AipRow]:
        """Rows keyed by *column*.

        :raises AipParseError: when two rows share a key, which means the
            project file is internally inconsistent.
        """
        indexed: dict[str, AipRow] = {}
        for row in self.rows:
            key = row.require(column)
            if key in indexed:
                raise AipParseError(
                    "two rows share a key",
                    context={"table": self.tail, "column": column, "key": key},
                )
            indexed[key] = row
        return indexed


@dataclass(frozen=True, slots=True)
class AipProject:
    """A parsed project file."""

    document: dict[str, str] = field(default_factory=dict)
    tables: dict[str, AipTable] = field(default_factory=dict)
    source: Path | None = None

    def _resolve(self, name: str) -> str:
        # Tables are stored under their tail. A caller may hand us an alias, a
        # tail, or the whole cid, so try all three spellings before giving up.
        for candidate in (
            TABLE_ALIASES.get(name, name),
            name,
            name[len(CID_PREFIX) :] if name.startswith(CID_PREFIX) else name,
        ):
            if candidate in self.tables:
                return candidate
        raise AipTableMissingError(
            "project has no such table",
            context={"table": name, "known": sorted(self.tables)},
        )

    def has_table(self, name: str) -> bool:
        """True when the project carries *name*, by alias or by cid."""
        try:
            self._resolve(name)
        except AipTableMissingError:
            return False
        return True

    def table(self, name: str) -> AipTable:
        """Look a table up by alias or cid.

        :raises AipTableMissingError: when it is not in the project.
        """
        return self.tables[self._resolve(name)]

    def rows(self, name: str) -> tuple[AipRow, ...]:
        """The rows of a table, or an empty tuple when the table is absent.

        Absent and empty are the same thing for most callers. Advanced
        Installer drops a table entirely when it has no rows left.
        """
        try:
            return self.table(name).rows
        except AipTableMissingError:
            return ()

    @property
    def table_names(self) -> list[str]:
        """Every table in the project, by tail, sorted."""
        return sorted(table.tail for table in self.tables.values())

    @property
    def properties(self) -> dict[str, str]:
        """The property table flattened to a plain mapping."""
        return {
            row.require("Property"): row.get("Value", "") or ""
            for row in self.rows("Property")
        }

    @property
    def build_names(self) -> list[str]:
        """The build configurations declared in the project, such as ``Win32_Release``."""
        names = {row.get("Name", "") for row in self.rows("Build")}
        return sorted(name for name in names if name)


def _element_to_row(element: ElementTree.Element, table: str) -> AipRow:
    return AipRow(values=dict(element.attrib), table=table)


def parse_aip(text: str, *, source: Path | None = None) -> AipProject:
    """Parse project XML held in a string.

    :raises AipParseError: when the text is not XML or the root is not
        ``<DOCUMENT>``.
    """
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise AipParseError(
            "project file is not well formed XML",
            context={"detail": str(exc), "source": str(source) if source else None},
        ) from exc

    if root.tag != "DOCUMENT":
        raise AipParseError(
            "project root element is not DOCUMENT",
            context={"found": root.tag},
        )

    tables: dict[str, AipTable] = {}
    for element in root.findall("COMPONENT"):
        cid = element.get("cid")
        if not cid:
            raise AipParseError("COMPONENT element has no cid attribute")
        tail = cid[len(CID_PREFIX) :] if cid.startswith(CID_PREFIX) else cid
        rows = tuple(_element_to_row(row, tail) for row in element.findall("ROW"))
        attributes = {
            attr.get("name", ""): attr.get("value", "") for attr in element.findall("ATTRIBUTE")
        }
        if tail in tables:
            raise AipParseError("project declares the same table twice", context={"table": tail})
        tables[tail] = AipTable(cid=cid, rows=rows, attributes=attributes)

    return AipProject(document=dict(root.attrib), tables=tables, source=source)


def read_aip(path: Path) -> AipProject:
    """Read and parse a project file from disk.

    :raises AipParseError: when the file cannot be read or does not parse.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AipParseError(
            "cannot read project file",
            context={"path": str(path), "detail": str(exc)},
        ) from exc
    return parse_aip(text, source=path)


__all__ = [
    "CID_PREFIX",
    "TABLE_ALIASES",
    "AipProject",
    "AipRow",
    "AipTable",
    "parse_aip",
    "read_aip",
]
