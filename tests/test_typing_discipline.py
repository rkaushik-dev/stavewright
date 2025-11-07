"""Checks over the source itself rather than over what it does.

Every one of these started as a real gap. The no-untyped-def ignores in
particular were load bearing for a while: thirteen action bodies sat behind
them and mypy was checking none of the three methods on any of them.

The rule is not that ignores are banned. It is that an ignore has to name a
code, so a reader can tell what is being suppressed, and none of them may be
no-untyped-def, because that one hides whole function bodies rather than one
awkward expression.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE_ROOT = PROJECT_ROOT / "src" / "v4t_setup"
TEST_ROOT = PROJECT_ROOT / "tests"

BOM = b"\xef\xbb\xbf"

IGNORE = re.compile(r"#\s*type:\s*ignore(\[(?P<codes>[^\]]*)\])?")

CONTAINER_METHODS = {"__iter__", "__len__", "__contains__", "__getitem__"}


def source_files() -> list[Path]:
    return sorted(SOURCE_ROOT.rglob("*.py"))


def python_files() -> list[Path]:
    """Everything this repository is responsible for, tests included."""
    return sorted(SOURCE_ROOT.rglob("*.py")) + sorted(TEST_ROOT.rglob("*.py"))


def relative(path: Path) -> str:
    return str(path.relative_to(SOURCE_ROOT))


def ignores(path: Path) -> Iterator[tuple[int, str | None]]:
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        match = IGNORE.search(line)
        if match:
            yield number, match.group("codes")


def functions(path: Path) -> Iterator[ast.FunctionDef]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            yield node


def test_the_package_has_source_to_check():
    assert len(source_files()) > 50


@pytest.mark.parametrize("path", source_files(), ids=relative)
def test_no_function_hides_behind_an_untyped_ignore(path):
    offenders = [number for number, codes in ignores(path) if codes == "no-untyped-def"]
    assert offenders == [], f"{relative(path)} lines {offenders}"


@pytest.mark.parametrize("path", source_files(), ids=relative)
def test_every_ignore_names_its_code(path):
    bare = [number for number, codes in ignores(path) if not codes]
    assert bare == [], f"{relative(path)} lines {bare}"


@pytest.mark.parametrize("path", source_files(), ids=relative)
def test_every_function_annotates_its_return(path):
    missing = [
        node.name
        for node in functions(path)
        if node.returns is None and not node.name.startswith("__init__")
    ]
    assert missing == [], f"{relative(path)}: {missing}"


@pytest.mark.parametrize("path", source_files(), ids=relative)
def test_every_argument_is_annotated(path):
    missing: list[str] = []
    for node in functions(path):
        arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        missing.extend(
            f"{node.name}({argument.arg})"
            for argument in arguments
            if argument.annotation is None and argument.arg not in ("self", "cls")
        )
    assert missing == [], f"{relative(path)}: {missing}"


@pytest.mark.parametrize("path", source_files(), ids=relative)
def test_a_container_method_says_what_it_contains(path):
    # __iter__ returning a bare Iterator tells a caller nothing, which is what
    # the ignores were previously doing by another route.
    vague: list[str] = []
    for node in functions(path):
        if node.name != "__iter__" or node.returns is None:
            continue
        if isinstance(node.returns, ast.Name) and node.returns.id == "Iterator":
            vague.append(node.name)
    assert vague == [], f"{relative(path)}: {vague}"


def test_no_python_file_carries_a_byte_order_mark():
    # A PowerShell redirect on this bench writes UTF-8 with a BOM by default,
    # and Python reads the mark as a non printable character in column one.
    # The module still imports, because the import machinery strips it, so the
    # first thing that noticed was ast.parse in the test above.
    marked = [str(path) for path in python_files() if path.read_bytes().startswith(BOM)]
    assert marked == []


def test_no_python_file_has_windows_line_endings():
    # .gitattributes turns normalisation off for the whole tree, so that the
    # 2016 payload is stored byte for byte. That also means a file written
    # through Python's text mode on Windows goes in with CRLF and stays there.
    mixed = [str(path) for path in python_files() if b"\r\n" in path.read_bytes()]
    assert mixed == []


def test_the_container_methods_travel_together():
    # A class with __iter__ and no __len__ makes truthiness silently expensive,
    # because Python falls back to walking the whole iterator.
    lonely: list[str] = []
    for path in source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            names = {child.name for child in node.body if isinstance(child, ast.FunctionDef)}
            defined = names & CONTAINER_METHODS
            if "__iter__" in defined and "__len__" not in defined:
                lonely.append(f"{relative(path)}:{node.name}")
    assert lonely == []
