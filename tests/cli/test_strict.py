"""--strict, and the rule that it is on every command that reports.

A flag that is on some commands and not others is worse than no flag, because
the build server script that uses it works until somebody adds a step. So the
first test here is a discipline test over the whole command tree: anything that
takes --format produces a report, and anything that produces a report takes
--strict. That is the invariant. The rest check it means one thing everywhere.

What strict does not do is worth as much as what it does. It cannot rescue a
failure, it does not touch the document, and a command with nothing to say
comes out zero either way.
"""

from __future__ import annotations

import json

import click
import pytest
import typer

from v4t_setup.cli.common import EXIT_FINDINGS, EXIT_OK
from v4t_setup.cli.main import app

from .conftest import GS1_DATA, TEMPLATE


def walk(command: click.Command, path: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    """Every leaf command in the tree, as the words you would type."""
    if isinstance(command, click.Group):
        found: list[tuple[str, ...]] = []
        for name, child in command.commands.items():
            found.extend(walk(child, (*path, name)))
        return found
    return [path]


def flags(command: click.Command, path: tuple[str, ...]) -> set[str]:
    for name in path:
        assert isinstance(command, click.Group)
        command = command.commands[name]
    return {option for param in command.params for option in param.opts}


ROOT = typer.main.get_command(app)
LEAVES = walk(ROOT)
REPORTING = [path for path in LEAVES if "--format" in flags(ROOT, path)]


def test_there_are_reporting_commands_to_check():
    """The corpus, stated. A discipline test over an empty list passes."""
    assert len(REPORTING) >= 20


@pytest.mark.parametrize("path", REPORTING, ids=[" ".join(p) for p in REPORTING])
def test_every_reporting_command_takes_strict(path):
    assert "--strict" in flags(ROOT, path)


@pytest.mark.parametrize("path", REPORTING, ids=[" ".join(p) for p in REPORTING])
def test_every_reporting_command_also_writes_to_a_file(path):
    """--output travels with --format. Four commands used to print to stdout
    and nothing else, which sends somebody back to shell redirection, and that
    loses the exit code the report just earned."""
    assert "--output" in flags(ROOT, path)


def run(runner, *args):
    return runner.invoke(app, ["-q", *args])


def test_strict_turns_a_warning_into_a_failure(runner, bench):
    """The shipped template has exactly one warning and no errors."""
    plain = run(runner, "template", "lint", str(TEMPLATE))
    strict = run(runner, "template", "lint", str(TEMPLATE), "--strict")
    assert plain.exit_code == EXIT_OK, plain.output
    assert strict.exit_code == EXIT_FINDINGS, strict.output


def test_strict_does_not_change_the_report(runner, bench):
    plain = run(runner, "template", "lint", str(TEMPLATE), "--format", "json")
    strict = run(runner, "template", "lint", str(TEMPLATE), "--format", "json", "--strict")
    assert plain.stdout == strict.stdout
    assert json.loads(strict.stdout)["outcome"] == "warn"


def test_strict_leaves_a_clean_run_alone(runner, bench):
    """Nothing to say is still nothing to say."""
    plain = run(runner, "gs1", "parse", "(01)01001000173250")
    strict = run(runner, "gs1", "parse", "(01)01001000173250", "--strict")
    assert plain.exit_code == EXIT_OK, plain.output
    assert strict.exit_code == EXIT_OK, strict.output


def test_strict_cannot_rescue_a_failure(runner, bench):
    """A failure is a failure. Strict only ever moves in one direction."""
    plain = run(runner, "gs1", "check", str(GS1_DATA))
    strict = run(runner, "gs1", "check", str(GS1_DATA), "--strict")
    assert plain.exit_code == EXIT_FINDINGS
    assert strict.exit_code == EXIT_FINDINGS


def test_strict_still_writes_the_file_it_was_asked_for(runner, bench):
    out = bench / "template.json"
    result = run(
        runner, "template", "lint", str(TEMPLATE), "--format", "json",
        "--output", str(out), "--strict",
    )
    assert result.exit_code == EXIT_FINDINGS
    assert json.loads(out.read_text(encoding="utf-8"))["counts"] == {"warning": 1}


def test_strict_is_spelled_the_same_way_everywhere():
    """One spelling, one letter case, no short form. A -s that meant strict on
    one command and something else on the next is the failure being avoided."""
    for path in REPORTING:
        assert not any(
            option.startswith("-") and not option.startswith("--") and "strict" in option
            for option in flags(ROOT, path)
        )


def test_the_helper_is_what_ends_a_reporting_command():
    """No command builds its own exit code out of emit any more.

    apply is the one exception and it is deliberate: it emits the preflight
    report and then stops with a message, which is a different document from
    the one the command returns.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "src" / "v4t_setup" / "cli"
    offenders = [
        path.name
        for path in sorted(root.glob("cmd_*.py"))
        if "typer.Exit(emit(" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
