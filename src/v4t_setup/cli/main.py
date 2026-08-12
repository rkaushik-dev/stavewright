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
from v4t_setup.cli import cmd_bundle, cmd_fleet, cmd_gs1, cmd_run, cmd_template, options
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


@app.command("doctor")
def doctor(
    ctx: typer.Context,
    strict: bool = typer.Option(
        False, "--strict", help="Treat the warnings as failures too."
    ),
    fmt: str = options.FORMAT,
    output: Path = options.OUTPUT,
) -> None:
    """Say what this installation is and whether it will work.

    The first question on a support call is which version of what is on the
    laptop. Exits 1 when something here would stop a command halfway through.

    ``--strict`` also fails on the warnings, which is what a build server
    wants: a missing settings file is fine on a laptop and means somebody
    forgot to check one in on CI.
    """
    from v4t_setup.cli.common import emit, get_context
    from v4t_setup.environment import check_environment, collect
    from v4t_setup.report.document import Outcome, Report, findings_section, lines_section

    shared = get_context(ctx)
    environment = collect(config_file=shared.settings_file)
    findings = check_environment(environment)

    report = Report(
        title="Toolkit",
        subtitle=environment.describe(),
        facts=environment.as_facts(),
    )
    report.add(
        lines_section(
            "Where things go",
            [
                f"staging: {shared.settings.staging_root}",
                f"journals: {shared.settings.journal_root}",
                f"reports: {shared.settings.report_root}",
                f"trusted keys: {shared.settings.trusted_keys or 'none'}",
            ],
        )
    )
    report.add_if(findings_section("Findings", findings))
    report.derive_outcome()
    code = emit(report, fmt, output)
    if strict and report.outcome is not Outcome.PASS:
        code = 1
    raise typer.Exit(code)


@app.command("rules")
def list_rules() -> None:
    """List every rule name any command can report, and where it comes from.

    A finding in a report is a rule name and a message, and the name is what a
    waiver in the settings file matches on. Getting the list out of the code
    beats keeping a copy of it in the README, which was already two names out.
    """
    from v4t_setup.gs1.datasetlint import DEFAULT_DATASET_RULES
    from v4t_setup.gs1.validate import DEFAULT_RULES
    from v4t_setup.preflight.checks import DEFAULT_CHECKS
    from v4t_setup.ruleset import rule_names
    from v4t_setup.templates.linter import DEFAULT_TEMPLATE_RULES

    element_string = rule_names(DEFAULT_RULES)
    dataset = rule_names(DEFAULT_DATASET_RULES)

    # A dataset rule that validates a whole element string reports the inner
    # rule under its own name with the outer one in front, so a waiver on
    # check-digit does not match element-string/check-digit. Those composed
    # names are real rule names and belong in this list.
    composed = [
        f"{outer}/{inner}"
        for outer in dataset
        if outer in ("element-string",)
        for inner in element_string
    ]

    from v4t_setup.gs1.label import RULE as LABEL_RULE

    groups = (
        ("gs1", element_string),
        ("gs1 label", [LABEL_RULE]),
        ("gs1 data file", dataset + composed),
        ("template", rule_names(DEFAULT_TEMPLATE_RULES)),
        ("preflight", rule_names(DEFAULT_CHECKS)),
    )
    for group, names in groups:
        echo_lines([f"{group}: {name}" for name in names])


def run() -> None:
    """Console script entry point."""
    app()


# Without this, python -m v4t_setup.cli.main imports the module, defines the
# commands and exits 0 having done nothing at all. Five CI steps were invoking
# it that way and passing because of it, which is the worst kind of green.
if __name__ == "__main__":  # pragma: no cover
    run()


__all__ = ["app", "main", "run"]
