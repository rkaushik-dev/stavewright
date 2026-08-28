"""What each group of report builders drags in behind it.

The builders were one module importing from twelve subsystems, so a command
that wanted to print a GS1 data file loaded the compound file reader, the apply
executor and the Windows target port. Splitting them only helps if it stays
split, and nothing in an ordinary test would notice it creeping back.

Each check runs in a subprocess. Doing it in this one would only tell you what
the rest of the suite has already imported.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

GROUPS = ["terminal", "quality", "installer", "fleet"]

FORBIDDEN = {
    "quality": ["stavewright.msi.cfb", "stavewright.apply.executor", "stavewright.fleet.roster"],
    "fleet": ["stavewright.msi.cfb", "stavewright.apply.executor", "stavewright.templates.spf"],
    "installer": ["stavewright.apply.executor", "stavewright.fleet.roster", "stavewright.gs1.dataset"],
    "terminal": ["stavewright.msi.cfb", "stavewright.fleet.roster"],
}


def imported_by(module: str) -> set[str]:
    """Every stavewright module loaded as a side effect of importing *module*."""
    script = (
        "import sys, json;"
        f"__import__('{module}');"
        "print(json.dumps(sorted(n for n in sys.modules if n.startswith('stavewright'))))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
    )
    import json

    return set(json.loads(result.stdout))


@pytest.mark.parametrize("group", GROUPS)
def test_each_group_imports(group):
    assert f"stavewright.report.builders.{group}" in imported_by(
        f"stavewright.report.builders.{group}"
    )


@pytest.mark.parametrize("group", GROUPS)
def test_a_group_does_not_drag_in_the_others_subsystems(group):
    loaded = imported_by(f"stavewright.report.builders.{group}")
    intruders = [name for name in FORBIDDEN[group] if name in loaded]
    assert intruders == [], f"{group} pulled in {intruders}"


def test_the_package_itself_pulls_in_no_builders():
    # There is deliberately no re-export in __init__, so importing the package
    # gets you a docstring and nothing else.
    loaded = imported_by("stavewright.report.builders")
    assert not any(name.startswith("stavewright.report.builders.") for name in loaded)


def test_the_shared_helpers_pull_in_nothing():
    loaded = imported_by("stavewright.report.shared")
    assert loaded == {"stavewright", "stavewright.report", "stavewright.report.shared"}


def test_importing_the_package_itself_loads_nothing():
    # The exports are lazy. This is what makes everything above possible: the
    # top level __init__ is the first file Python loads for any submodule, and
    # written eagerly it pulled in forty eight of them.
    assert imported_by("stavewright") == {"stavewright"}


def test_an_export_still_resolves():
    script = "import stavewright; print(stavewright.build_plan.__name__)"
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "build_plan"


def test_a_name_that_is_not_exported_raises_attribute_error():
    script = (
        "import stavewright\n"
        "try:\n"
        "    stavewright.nonsense\n"
        "except AttributeError as exc:\n"
        "    print(exc)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    assert "has no attribute 'nonsense'" in result.stdout


def test_dir_lists_the_exports():
    script = "import stavewright; print(len(dir(stavewright)), 'build_plan' in dir(stavewright))"
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    count, present = result.stdout.split()
    assert int(count) > 20
    assert present == "True"


def test_the_quality_group_is_much_cheaper_than_it_was():
    # The old single module, on top of an eager package init, loaded better
    # than sixty stavewright modules to print a GS1 report. This is the number
    # that matters, not the list.
    assert len(imported_by("stavewright.report.builders.quality")) < 30
