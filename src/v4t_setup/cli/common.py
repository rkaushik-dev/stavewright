"""What every command needs and none of them should implement twice.

Three things live here. Loading settings the same way for every command, so a
site file works everywhere. Writing a report out and turning it into an exit
code. And turning a :class:`~v4t_setup.errors.V4TError` into a message with its
ticket code on it rather than a traceback.

The exit codes are worth stating. Zero is a pass, one is a failure the operator
has to act on, and two is the toolkit itself being unable to do the job:
a missing file, a bad profile, an unreadable manifest. A build server that
tells those two apart can retry the second and page somebody for the first.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Any, TypeVar

import typer

from v4t_setup.config.settings import Settings, find_config_file, load_settings
from v4t_setup.errors import V4TError
from v4t_setup.logging import configure, new_run_id
from v4t_setup.report.document import Outcome, Report
from v4t_setup.report.render import RENDERERS, render
from v4t_setup.util.fsops import atomic_write_text

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
        return cls(settings=settings, run_id=new_run_id(), quiet=quiet)


def get_context(ctx: typer.Context) -> AppContext:
    """Read the shared context off a Typer context.

    :raises typer.Exit: when the top level callback did not run, which only
        happens if a command is invoked directly in a test without one.
    """
    if not isinstance(ctx.obj, AppContext):
        typer.echo("v4t-setup: no application context, this is a bug", err=True)
        raise typer.Exit(EXIT_TOOLKIT)
    return ctx.obj


def check_format(fmt: str) -> str:
    """Validate a ``--format`` value.

    :raises typer.Exit: with the toolkit code, since a bad flag is not a
        finding about a terminal.
    """
    if fmt not in RENDERERS:
        typer.echo(
            f"v4t-setup: unknown format {fmt!r}, expected one of {', '.join(sorted(RENDERERS))}",
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


def fail(message: str, code: int = EXIT_TOOLKIT) -> None:
    """Print an error and stop.

    :raises typer.Exit: always.
    """
    typer.echo(f"v4t-setup: {message}", err=True)
    raise typer.Exit(code)


def handle_errors(function: Callable[..., T]) -> Callable[..., T]:
    """Turn a :class:`V4TError` into a message with its ticket code.

    A traceback on a bench laptop tells the operator nothing and tells the
    person reading the ticket less. The code is what field engineering greps
    for, so it goes first.
    """

    @wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> T:
        try:
            return function(*args, **kwargs)
        except V4TError as exc:
            typer.echo(f"v4t-setup: [{exc.code}] {exc.message}", err=True)
            for key in sorted(exc.context):
                typer.echo(f"  {key}: {exc.context[key]}", err=True)
            raise typer.Exit(EXIT_TOOLKIT) from exc

    return wrapper


def outcome_line(report: Report) -> str:
    """One line naming the outcome, for a quiet run that wants no document."""
    counts = report.counts()
    summary = ", ".join(f"{count} {name}" for name, count in counts.items())
    verdict = {Outcome.PASS: "ok", Outcome.WARN: "warnings", Outcome.FAIL: "failed"}[
        report.outcome
    ]
    return f"{report.title}: {verdict}" + (f" ({summary})" if summary else "")


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


def is_a_terminal() -> bool:
    """True when stdout is a terminal, for deciding whether to be chatty."""
    return sys.stdout.isatty()


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
    "get_context",
    "handle_errors",
    "is_a_terminal",
    "outcome_line",
    "require_directory",
    "require_file",
]
