"""What changed between two builds of an installer.

The question after a release goes wrong is always the same: what is different
about this build. A byte comparison says everything, because the package code,
the cabinet and every sequence number move on every build. The tables barely
move at all, and that is where the answer is.

Rows are matched on the table's own key columns, which is what makes this a
diff rather than a set comparison. Two Registry rows with the same key and a
different value are one changed row, not one added and one removed, and the
difference between those two readings is the difference between a page and a
shrug.

Three tables are left out by default and each for its own reason:

``_Validation`` is the schema, and a schema change is real but it is not what
anybody is looking for at four in the afternoon.

``MsiFileHash`` changes whenever a file does, and the File table already said
so, in a line somebody can read.

``Property`` is compared but ``ProductCode`` and ``PackageCode`` inside it are
expected to differ, so they are reported separately rather than in with the
rows nobody expected to move.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum

from stavewright.msi.tables import MsiDatabase, Row, Table

NOISY_TABLES = frozenset({"_Validation", "MsiFileHash"})
"""Tables whose changes are noise rather than news."""

EXPECTED_PROPERTIES = frozenset({"ProductCode", "PackageCode"})
"""Properties that are supposed to change between builds."""

SAMPLE_SIZE = 5


class Change(StrEnum):
    ADDED = "added"
    REMOVED = "removed"
    CHANGED = "changed"


@dataclass(frozen=True, slots=True)
class RowChange:
    """One row that is not the same in both."""

    table: str
    key: str
    change: Change
    fields: tuple[str, ...] = ()
    """Which columns differ. Empty for an added or removed row."""

    before: Row = field(default_factory=dict)
    after: Row = field(default_factory=dict)

    def describe(self) -> str:
        if self.change is Change.CHANGED:
            return f"{self.table} {self.key}: {', '.join(self.fields)}"
        return f"{self.table} {self.key}: {self.change.value}"


@dataclass(frozen=True, slots=True)
class TableDifference:
    """What changed in one table."""

    name: str
    added: tuple[RowChange, ...] = ()
    removed: tuple[RowChange, ...] = ()
    changed: tuple[RowChange, ...] = ()

    def __len__(self) -> int:
        return len(self.added) + len(self.removed) + len(self.changed)

    def __iter__(self) -> Iterator[RowChange]:
        return iter((*self.added, *self.removed, *self.changed))

    @property
    def is_identical(self) -> bool:
        return len(self) == 0

    def describe(self) -> str:
        return (
            f"{self.name}: {len(self.added)} added, {len(self.removed)} removed, "
            f"{len(self.changed)} changed"
        )


@dataclass(frozen=True, slots=True)
class DatabaseDifference:
    """Two installers held up against each other."""

    tables: tuple[TableDifference, ...] = ()
    only_in_before: tuple[str, ...] = ()
    only_in_after: tuple[str, ...] = ()
    expected_properties: dict[str, tuple[str, str]] = field(default_factory=dict)

    def __len__(self) -> int:
        return sum(len(table) for table in self.tables)

    def __iter__(self) -> Iterator[TableDifference]:
        return iter(self.tables)

    @property
    def is_identical(self) -> bool:
        return not (len(self) or self.only_in_before or self.only_in_after)

    @property
    def changed_tables(self) -> list[str]:
        return [table.name for table in self.tables if not table.is_identical]

    def of_table(self, name: str) -> TableDifference | None:
        return next((table for table in self.tables if table.name == name), None)

    def describe(self) -> list[str]:
        lines = [table.describe() for table in self.tables if not table.is_identical]
        lines.extend(f"{name}: only in the first" for name in self.only_in_before)
        lines.extend(f"{name}: only in the second" for name in self.only_in_after)
        for name, (before, after) in sorted(self.expected_properties.items()):
            lines.append(f"{name}: {before} -> {after} (expected to change)")
        return lines


def _key_of(table: Table, row: Row) -> str:
    """One string identifying a row, out of the table's key columns.

    A table with no declared key falls back to every column, which makes an
    edit read as one row removed and one added. That is the honest answer:
    with no key there is nothing to say the two are the same row.
    """
    columns = table.key_columns or table.column_names
    return "|".join(str(row.get(name, "")) for name in columns)


def compare_tables(before: Table, after: Table) -> TableDifference:
    """Compare one table with the version of itself in another installer."""
    first = {_key_of(before, row): row for row in before}
    second = {_key_of(after, row): row for row in after}
    columns = [name for name in after.column_names if name in before.column_names]

    added = tuple(
        RowChange(table=after.name, key=key, change=Change.ADDED, after=second[key])
        for key in sorted(set(second) - set(first))
    )
    removed = tuple(
        RowChange(table=before.name, key=key, change=Change.REMOVED, before=first[key])
        for key in sorted(set(first) - set(second))
    )
    changed: list[RowChange] = []
    for key in sorted(set(first) & set(second)):
        fields = tuple(
            name for name in columns if first[key].get(name) != second[key].get(name)
        )
        if fields:
            changed.append(
                RowChange(
                    table=after.name,
                    key=key,
                    change=Change.CHANGED,
                    fields=fields,
                    before=first[key],
                    after=second[key],
                )
            )
    return TableDifference(
        name=after.name,
        added=added,
        removed=removed,
        changed=tuple(changed),
    )


def compare_databases(
    before: MsiDatabase,
    after: MsiDatabase,
    *,
    skip: frozenset[str] = NOISY_TABLES,
) -> DatabaseDifference:
    """Compare two installers, table by table.

    :param skip: tables not to compare. The default leaves out the schema and
        the file hashes, both of which restate what another table already said.
    """
    shared = sorted((set(before.table_names) & set(after.table_names)) - skip)
    differences = tuple(
        compare_tables(before.get(name), after.get(name)) for name in shared
    )

    expected: dict[str, tuple[str, str]] = {}
    for name in sorted(EXPECTED_PROPERTIES):
        first = before.property(name)
        second = after.property(name)
        if first != second:
            expected[name] = (first or "", second or "")

    return DatabaseDifference(
        tables=tuple(table for table in differences if not table.is_identical),
        only_in_before=tuple(sorted(set(before.table_names) - set(after.table_names) - skip)),
        only_in_after=tuple(sorted(set(after.table_names) - set(before.table_names) - skip)),
        expected_properties=expected,
    )


def summarise(difference: DatabaseDifference, limit: int = SAMPLE_SIZE) -> list[str]:
    """A few lines per changed table, for a report that has to fit on a page."""
    lines: list[str] = []
    for table in difference:
        lines.append(table.describe())
        lines.extend(f"    {change.describe()}" for change in list(table)[:limit])
        if len(table) > limit:
            lines.append(f"    and {len(table) - limit} more")
    return lines


__all__ = [
    "EXPECTED_PROPERTIES",
    "NOISY_TABLES",
    "SAMPLE_SIZE",
    "Change",
    "DatabaseDifference",
    "RowChange",
    "TableDifference",
    "compare_databases",
    "compare_tables",
    "summarise",
]
