"""What every command needs and none of them should implement twice.

Three things live here. Loading settings the same way for every command, so a
site file works everywhere. Writing a report out and turning it into an exit
code. And turning a :class:`~stavewright.errors.StavewrightError` into a message with its
ticket code on it rather than a traceback.

The exit codes are worth stating. Zero is a pass, one is a failure the operator
has to act on, and two is the toolkit itself being unable to do the job:
a missing file, a bad profile, an unreadable manifest. A build server that
tells those two apart can retry the second and page somebody for the first.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Any, NoReturn, TypeVar

import typer

from stavewright.config.settings import Settings, find_config_file, load_settings
from stavewright.errors import StavewrightError
from stavewright.logging import configure, new_run_id
from stavewright.report.document import Outcome, Report
from stavewright.report.render import RENDERERS, render
from stavewright.util.fsops import atomic_write_text

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_TOOLKIT = 2

DEFAULT_FORMAT = "text"

T = TypeVar("T")


@dataclass
class AppContext:
    """What the top level command works out once and every subcommand reuses."""

    settings: Settings
    run_id: str
    quiet: bool = False
    settings_file: Path | None = None
    """Which file the settings came from, or None when none was found.

    Worth keeping because "it is not reading my settings" is a support call,
    and the answer is nearly always that the file is one directory up from
    where somebody thought it was.
    """

    @classmethod
    def build(
        cls,
        *,
        config: Path | None = None,
        overrides: dict[str, Any] | None = None,
        quiet: bool = False,
    ) -> AppContext:
        """Load settings and set logging up.

        When no config file is named, the working directory and its parents are
        searched for one. A bench laptop keeps a single file at the top of the
        commissioning directory and everything under it inherits it.
        """
        files = [config] if config is not None else []
        if not files:
            found = find_config_file(Path.cwd())
            if found is not None:
                files = [found]
        settings = load_settings(files=files, overrides=overrides or {})
        configure(
            level=settings.log_level,
            log_file=settings.log_file,
            console=not quiet,
        )
        return cls(
            settings=settings,
            run_id=new_run_id(),
            quiet=quiet,
            settings_file=files[0] if files else None,
        )


def get_context(ctx: typer.Context) -> AppContext:
    """Read the shared context off a Typer context.

    :raises typer.Exit: when the top level callback did not run, which only
        happens if a command is invoked directly in a test without one.
    """
    if not isinstance(ctx.obj, AppContext):
        typer.echo("stavewright: no application context, this is a bug", err=True)
        raise typer.Exit(EXIT_TOOLKIT)
    return ctx.obj


def check_format(fmt: str) -> str:
    """Validate a ``--format`` value.

    :raises typer.Exit: with the toolkit code, since a bad flag is not a
        finding about a terminal.
    """
    if fmt not in RENDERERS:
        typer.echo(
            f"stavewright: unknown format {fmt!r}, expected one of {', '.join(sorted(RENDERERS))}",
            err=True,
        )
        raise typer.Exit(EXIT_TOOLKIT)
    return fmt


def emit(report: Report, fmt: str = DEFAULT_FORMAT, output: Path | None = None) -> int:
    """Render *report* and write it. Returns the exit code the command wants.

    Writing to a file still prints one line to stderr saying where it went.
    Silently producing a file somebody then cannot find is not helpful.
    """
    text = render(report, check_format(fmt))
    if output is None:
        typer.echo(text, nl=False)
    else:
        atomic_write_text(output, text)
        typer.echo(f"written to {output}", err=True)
    return report.outcome.exit_code


def finish(
    report: Report,
    fmt: str = DEFAULT_FORMAT,
    output: Path | None = None,
    *,
    strict: bool = False,
) -> NoReturn:
    """Write a report out and stop with the code it earned.

    Every reporting command ended with the same line, and adding ``--strict``
    to one of them made that line different in one place, which is how a flag
    ends up meaning two things. So the last line of a command is this call and
    the strictness lives here.

    Strict promotes a warning to a failure and nothing else. It cannot turn a
    failure into a pass, and it does not touch the report, so the file written
    on a strict run and a lenient one are byte for byte the same.

    :raises typer.Exit: always, which is what makes it the last line.
    """
    code = emit(report, fmt, output)
    if strict and code == EXIT_OK and report.outcome is not Outcome.PASS:
        code = EXIT_FINDINGS
    raise typer.Exit(code)


def fail(message: str, code: int = EXIT_TOOLKIT) -> NoReturn:
    """Print an error and stop.

    :raises typer.Exit: always.
    """
    typer.echo(f"stavewright: {message}", err=True)
    raise typer.Exit(code)


def handle_errors(function: Callable[..., T]) -> Callable[..., T]:
    """Turn a :class:`StavewrightError` into a message with its ticket code.

    A traceback on a bench laptop tells the operator nothing and tells the
    person reading the ticket less. The code is what field engineering greps
    for, so it goes first.
    """

    @wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> T:
        try:
            return function(*args, **kwargs)
        except StavewrightError as exc:
            typer.echo(f"stavewright: [{exc.code}] {exc.message}", err=True)
            for key in sorted(exc.context):
                typer.echo(f"  {key}: {exc.context[key]}", err=True)
            raise typer.Exit(EXIT_TOOLKIT) from exc

    return wrapper


def require_file(path: Path, what: str) -> Path:
    """Stop with a clear message when a path is not a readable file.

    :raises typer.Exit: when it is missing or is a directory.
    """
    if not path.exists():
        fail(f"{what} does not exist: {path}")
    if path.is_dir():
        fail(f"{what} is a directory, expected a file: {path}")
    return path


def require_directory(path: Path, what: str) -> Path:
    """Stop with a clear message when a path is not a directory.

    :raises typer.Exit: when it is missing or is a file.
    """
    if not path.exists():
        fail(f"{what} does not exist: {path}")
    if not path.is_dir():
        fail(f"{what} is a file, expected a directory: {path}")
    return path


def echo_lines(lines: list[str], *, err: bool = False) -> None:
    """Print a list of lines, one per line."""
    for line in lines:
        typer.echo(line, err=err)


__all__ = [
    "DEFAULT_FORMAT",
    "EXIT_FINDINGS",
    "EXIT_OK",
    "EXIT_TOOLKIT",
    "AppContext",
    "check_format",
    "echo_lines",
    "emit",
    "fail",
    "finish",
    "get_context",
    "handle_errors",
    "require_directory",
    "require_file",
]
