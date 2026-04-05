"""Every command that takes a shared option takes the shared one.

The help text for --format was written out fifteen times and three of the
copies had already drifted. Adding markdown to the renderers meant editing all
fifteen, which is exactly the moment somebody edits fourteen.

These read the parameters off the built Typer app rather than the source, so a
command that declares its own copy with identical text still fails: it is the
next edit that matters, not this one.
"""

from __future__ import annotations

import click
import pytest
from typer.main import get_command

from v4t_setup.cli import options
from v4t_setup.cli.main import app

SHARED = {
    "--format": options.FORMAT,
    "--output": options.OUTPUT,
    "--aip": options.AIP,
    "--templates": options.TEMPLATES,
    "--journals": options.JOURNALS,
    "--batch": options.BATCH,
    "--pending": options.PENDING,
    "--offline": options.OFFLINE,
}


def commands() -> list[tuple[str, click.Command]]:
    """Every leaf command, by its full path."""
    found: list[tuple[str, click.Command]] = []

    def walk(name: str, command: click.Command) -> None:
        if isinstance(command, click.Group):
            for child, sub in command.commands.items():
                walk(f"{name} {child}".strip(), sub)
        else:
            found.append((name, command))

    walk("", get_command(app))
    return sorted(found)


def option_help(command: click.Command, flag: str) -> str | None:
    for parameter in command.params:
        if isinstance(parameter, click.Option) and flag in parameter.opts:
            return parameter.help
    return None


def test_the_two_terminal_options_are_deliberately_different():
    # The same flag means two things: pick a terminal out of a profile file, or
    # filter the journals. One shared description would make one of them wrong.
    assert options.TERMINAL.help != options.TERMINAL_FILTER.help
    filtering = [
        name
        for name, command in commands()
        if option_help(command, "--terminal") == options.TERMINAL_FILTER.help
    ]
    assert filtering == ["run history"]


def test_the_app_has_commands_to_check():
    assert len(commands()) > 15


@pytest.mark.parametrize("flag", sorted(SHARED))
def test_every_command_that_takes_it_gives_the_same_help(flag):
    expected = SHARED[flag].help
    wrong = {
        name: option_help(command, flag)
        for name, command in commands()
        if option_help(command, flag) is not None
        and option_help(command, flag) != expected
    }
    assert wrong == {}


def test_format_is_on_most_commands():
    taking = [name for name, command in commands() if option_help(command, "--format")]
    assert len(taking) > 10


def test_the_format_help_names_every_renderer():
    from v4t_setup.report.render import RENDERERS

    help_text = options.FORMAT.help or ""
    for name in RENDERERS:
        assert name in help_text, name


def test_the_profile_option_is_required_where_it_is_shared():
    for name, command in commands():
        for parameter in command.params:
            if isinstance(parameter, click.Option) and "--profile" in parameter.opts:
                # Two shapes, and which one a command wants is a real decision.
                assert parameter.required in (True, False), name


def test_output_is_never_required():
    for name, command in commands():
        for parameter in command.params:
            if isinstance(parameter, click.Option) and "--output" in parameter.opts:
                assert not parameter.required, name


def test_short_flags_do_not_collide_within_one_command():
    for name, command in commands():
        shorts: list[str] = []
        for parameter in command.params:
            if not isinstance(parameter, click.Option):
                continue
            shorts.extend(opt for opt in parameter.opts if len(opt) == 2)
        assert len(shorts) == len(set(shorts)), f"{name}: {shorts}"


def test_every_option_has_help_text():
    missing: list[str] = []
    for name, command in commands():
        missing.extend(
            f"{name} {parameter.opts[0]}"
            for parameter in command.params
            if isinstance(parameter, click.Option) and not parameter.help
        )
    assert missing == []
