from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from v4t_setup.msi.difference import (
    EXPECTED_PROPERTIES,
    NOISY_TABLES,
    Change,
    compare_databases,
    compare_tables,
    summarise,
)
from v4t_setup.msi.tables import (
    TYPE_KEY,
    TYPE_STRING,
    Column,
    MsiDatabase,
    StringPool,
    Table,
    read_msi,
)

MSI = Path(__file__).resolve().parents[2] / "msi-quattro" / "msi-Jet600.msi"
EMPTY_POOL = StringPool(codepage=1252, strings=("",), bytes_per_ref=2)

COLUMNS = (
    Column(number=1, name="Key", type_word=TYPE_STRING | TYPE_KEY | 72),
    Column(number=2, name="Value", type_word=TYPE_STRING | 255),
)


def table(name: str = "Thing", rows=()) -> Table:
    return Table(name=name, columns=COLUMNS, rows=tuple(rows))


def database(**tables: Table) -> MsiDatabase:
    return MsiDatabase(pool=EMPTY_POOL, tables=dict(tables))


@pytest.fixture(scope="module")
def shipped():
    return read_msi(MSI)


def test_a_table_compared_with_itself_is_identical():
    one = table(rows=({"Key": "a", "Value": "one"},))
    assert compare_tables(one, one).is_identical


def test_an_added_row():
    before = table(rows=({"Key": "a", "Value": "one"},))
    after = table(rows=({"Key": "a", "Value": "one"}, {"Key": "b", "Value": "two"}))
    difference = compare_tables(before, after)
    assert [change.key for change in difference.added] == ["b"]
    assert difference.added[0].change is Change.ADDED


def test_a_removed_row():
    before = table(rows=({"Key": "a", "Value": "one"}, {"Key": "b", "Value": "two"}))
    after = table(rows=({"Key": "a", "Value": "one"},))
    difference = compare_tables(before, after)
    assert [change.key for change in difference.removed] == ["b"]


def test_a_changed_row_is_one_change_not_two():
    # The whole point of matching on the key. Otherwise an edited registry
    # value reads as one row gone and one row appeared.
    before = table(rows=({"Key": "a", "Value": "one"},))
    after = table(rows=({"Key": "a", "Value": "two"},))
    difference = compare_tables(before, after)
    assert len(difference) == 1
    assert difference.changed[0].change is Change.CHANGED
    assert difference.changed[0].fields == ("Value",)


def test_a_changed_row_carries_both_versions():
    before = table(rows=({"Key": "a", "Value": "one"},))
    after = table(rows=({"Key": "a", "Value": "two"},))
    change = compare_tables(before, after).changed[0]
    assert change.before["Value"] == "one"
    assert change.after["Value"] == "two"


def test_a_table_with_no_key_falls_back_to_every_column():
    keyless = Table(
        name="Thing",
        columns=(Column(number=1, name="Value", type_word=TYPE_STRING | 255),),
        rows=({"Value": "one"},),
    )
    edited = replace(keyless, rows=({"Value": "two"},))
    difference = compare_tables(keyless, edited)
    assert len(difference.added) == 1
    assert len(difference.removed) == 1
    assert not difference.changed


def test_the_changes_come_back_in_a_stable_order():
    before = table(rows=({"Key": "b", "Value": "x"}, {"Key": "a", "Value": "x"}))
    after = table()
    assert [change.key for change in compare_tables(before, after).removed] == ["a", "b"]


def test_iterating_a_table_difference_gives_every_change():
    before = table(rows=({"Key": "a", "Value": "one"}, {"Key": "b", "Value": "two"}))
    after = table(rows=({"Key": "a", "Value": "changed"}, {"Key": "c", "Value": "new"}))
    difference = compare_tables(before, after)
    assert len(list(difference)) == len(difference) == 3


def test_a_table_difference_describes_itself():
    before = table(rows=({"Key": "a", "Value": "one"},))
    after = table(rows=({"Key": "b", "Value": "two"},))
    assert compare_tables(before, after).describe() == (
        "Thing: 1 added, 1 removed, 0 changed"
    )


def test_a_row_change_describes_itself():
    before = table(rows=({"Key": "a", "Value": "one"},))
    after = table(rows=({"Key": "a", "Value": "two"},))
    assert compare_tables(before, after).changed[0].describe() == "Thing a: Value"


def test_an_added_row_describes_itself():
    after = table(rows=({"Key": "a", "Value": "one"},))
    assert compare_tables(table(), after).added[0].describe() == "Thing a: added"


def test_a_database_compared_with_itself(shipped):
    difference = compare_databases(shipped, shipped)
    assert difference.is_identical
    assert difference.changed_tables == []
    assert difference.describe() == []


def test_a_changed_property_shows_up(shipped):
    edited = MsiDatabase(
        pool=shipped.pool,
        tables={
            **shipped.tables,
            "Property": Table(
                name="Property",
                columns=shipped.get("Property").columns,
                rows=tuple(
                    {**row, "Value": "Sellenis"}
                    if row.get("Property") == "Manufacturer"
                    else row
                    for row in shipped.get("Property")
                ),
            ),
        },
    )
    difference = compare_databases(shipped, edited)
    assert difference.changed_tables == ["Property"]
    change = difference.of_table("Property").changed[0]
    assert change.after["Value"] == "Sellenis"


def test_a_table_only_in_one_of_them(shipped):
    without = MsiDatabase(
        pool=shipped.pool,
        tables={name: t for name, t in shipped.tables.items() if name != "Upgrade"},
    )
    difference = compare_databases(shipped, without)
    assert difference.only_in_before == ("Upgrade",)
    assert difference.only_in_after == ()
    assert not difference.is_identical


def test_the_noisy_tables_are_left_out(shipped):
    edited = MsiDatabase(
        pool=shipped.pool,
        tables={**shipped.tables, "MsiFileHash": Table(name="MsiFileHash", columns=COLUMNS)},
    )
    assert compare_databases(shipped, edited).is_identical


def test_the_skip_list_can_be_changed(shipped):
    edited = MsiDatabase(
        pool=shipped.pool,
        tables={**shipped.tables, "MsiFileHash": Table(name="MsiFileHash", columns=COLUMNS)},
    )
    difference = compare_databases(shipped, edited, skip=frozenset())
    assert not difference.is_identical


def test_the_default_skip_list_is_the_documented_one():
    assert {"_Validation", "MsiFileHash"} == NOISY_TABLES


def test_an_expected_property_change_is_reported_separately():
    first = database(
        Property=Table(
            name="Property",
            columns=COLUMNS,
            rows=({"Key": "x", "Value": "y"},),
        )
    )
    first.tables["Property"] = Table(
        name="Property",
        columns=(
            Column(number=1, name="Property", type_word=TYPE_STRING | TYPE_KEY | 72),
            Column(number=2, name="Value", type_word=TYPE_STRING | 255),
        ),
        rows=({"Property": "ProductCode", "Value": "{AAA}"},),
    )
    second = database(
        Property=Table(
            name="Property",
            columns=first.get("Property").columns,
            rows=({"Property": "ProductCode", "Value": "{BBB}"},),
        )
    )
    difference = compare_databases(first, second)
    assert difference.expected_properties["ProductCode"] == ("{AAA}", "{BBB}")


def test_the_expected_properties_are_the_documented_ones():
    assert {"ProductCode", "PackageCode"} == EXPECTED_PROPERTIES


def test_the_summary_is_capped():
    before = table(rows=tuple({"Key": f"k{n}", "Value": "x"} for n in range(20)))
    difference = compare_databases(database(Thing=before), database(Thing=table()))
    lines = summarise(difference, limit=3)
    assert any("and 17 more" in line for line in lines)


def test_the_summary_names_the_table():
    before = table(rows=({"Key": "a", "Value": "one"},))
    after = table(rows=({"Key": "a", "Value": "two"},))
    lines = summarise(compare_databases(database(Thing=before), database(Thing=after)))
    assert lines[0].startswith("Thing:")
    assert "Thing a: Value" in lines[1]


def test_a_database_difference_describes_the_expected_changes():
    first = database(
        Property=Table(
            name="Property",
            columns=(
                Column(number=1, name="Property", type_word=TYPE_STRING | TYPE_KEY | 72),
                Column(number=2, name="Value", type_word=TYPE_STRING | 255),
            ),
            rows=({"Property": "PackageCode", "Value": "{AAA}"},),
        )
    )
    second = database(
        Property=Table(
            name="Property",
            columns=first.get("Property").columns,
            rows=({"Property": "PackageCode", "Value": "{BBB}"},),
        )
    )
    lines = compare_databases(first, second).describe()
    assert any("expected to change" in line for line in lines)
