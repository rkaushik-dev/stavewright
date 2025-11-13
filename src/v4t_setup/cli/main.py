"""The ``v4t-setup`` command.

Subcommands are grouped by what an engineer is doing rather than by which
module implements them. ``gs1`` and ``template`` are their own groups because
the people who use them are quality rather than commissioning, and they never
want the rest.
"""

from __future__ import annotations

from pathlib import Path

import typer

from v4t_setup.__about__ import PRODUCT_NAME, __version__
from v4t_setup.cli import cmd_bundle, cmd_fleet, cmd_gs1, cmd_run, cmd_template
from v4t_setup.cli.common import EXIT_TOOLKIT, AppContext, echo_lines
from v4t_setup.errors import V4TError

app = typer.Typer(
    name="v4t-setup",
    help=f"Commissioning toolkit for {PRODUCT_NAME} marking terminals.",
    no_args_is_help=True,
    add_completion=False,
)

app.add_typer(cmd_bundle.app, name="bundle", help="Build, sign and stage a bundle.")
app.add_typer(cmd_run.app, name="run", help="Plan, check, apply and verify a terminal.")
app.add_typer(cmd_fleet.app, name="fleet", help="Order a plant's terminals into waves.")
app.add_typer(cmd_gs1.app, name="gs1", help="Validate GS1 element strings and data files.")
app.add_typer(cmd_template.app, name="template", help="Inspect and lint print templates.")


def _version(value: bool) -> None:
    if value:
        typer.echo(f"v4t-setup {__version__}")
        raise typer.Exit


@app.callback()
def main(
    ctx: typer.Context,
    config: Path = typer.Option(
        None,
        "--config",
        "-c",
        help="Settings file. Defaults to the nearest v4t-setup.toml.",
    ),
    log_level: str = typer.Option(None, "--log-level", help="DEBUG, INFO, WARNING, ERROR."),
    log_file: Path = typer.Option(None, "--log-file", help="Write a JSON log here."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="No log output on stderr."),
    version: bool = typer.Option(
        False, "--version", callback=_version, is_eager=True, help="Print the version and stop."
    ),
) -> None:
    """Set up the shared context every subcommand uses."""
    overrides = {"log_level": log_level, "log_file": log_file}
    try:
        ctx.obj = AppContext.build(config=config, overrides=overrides, quiet=quiet)
    except V4TError as exc:
        typer.echo(f"v4t-setup: [{exc.code}] {exc.message}", err=True)
        raise typer.Exit(EXIT_TOOLKIT) from exc


@app.command("checks")
def list_checks() -> None:
    """List the preflight checks and what each one is for."""
    from v4t_setup.preflight.runner import describe_checks

    echo_lines(describe_checks())


@app.command("formats")
def list_formats() -> None:
    """List the report formats the commands accept."""
    from v4t_setup.report.render import RENDERERS

    echo_lines(sorted(RENDERERS))


def run() -> None:
    """Console script entry point."""
    app()


__all__ = ["app", "main", "run"]
