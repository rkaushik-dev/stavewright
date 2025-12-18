"""``v4t-setup template`` — reading and checking print templates.

``show`` is for when somebody sends you a ``.spf`` and asks what is in it.
``lint`` is for before a template set goes onto a terminal.
"""

from __future__ import annotations

from pathlib import Path

import typer

from v4t_setup.cli.common import (
    DEFAULT_FORMAT,
    emit,
    get_context,
    handle_errors,
    require_directory,
    require_file,
)
from v4t_setup.config.profiles import read_profile
from v4t_setup.gs1.dataset import EXPECTED_COLUMNS
from v4t_setup.report.builders import template_report, template_set_report
from v4t_setup.report.document import table_section
from v4t_setup.templates.fieldspec import find_field_specs
from v4t_setup.templates.linter import TemplateContext, lint_template
from v4t_setup.templates.pack import lint_template_set, read_template_set
from v4t_setup.templates.spf import read_spf

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.command("show")
@handle_errors
def show(
    ctx: typer.Context,
    path: Path = typer.Argument(..., help="A .spf template."),
    fmt: str = typer.Option(DEFAULT_FORMAT, "--format", "-f", help="text, json, csv or junit."),
) -> None:
    """Show what is inside a template."""
    get_context(ctx)
    require_file(path, "template")
    template = read_spf(path)
    report = template_report(template, [])
    specs = find_field_specs(template.all_strings())
    if specs:
        report.add(
            table_section(
                "Fields",
                ["slot", "font", "size", "bound to"],
                [
                    [
                        "" if spec.source is None else str(spec.source),
                        spec.font.name if spec.font else "",
                        str(spec.font.size) if spec.font else "",
                        spec.reference or (f'"{spec.literal}"' if spec.is_literal else ""),
                    ]
                    for spec in specs
                ],
            )
        )
    raise typer.Exit(emit(report, fmt))


@app.command("lint")
@handle_errors
def lint(
    ctx: typer.Context,
    path: Path = typer.Argument(..., help="A .spf template."),
    with_dataset: bool = typer.Option(
        False, "--with-dataset", help="Check field bindings against the standard columns."
    ),
    fmt: str = typer.Option(DEFAULT_FORMAT, "--format", "-f", help="text, json, csv or junit."),
    output: Path = typer.Option(None, "--output", "-o", help="Write the report here."),
) -> None:
    """Check a template before it goes onto a terminal."""
    get_context(ctx)
    require_file(path, "template")
    template = read_spf(path)
    context = TemplateContext(
        dataset_columns=frozenset(EXPECTED_COLUMNS) if with_dataset else frozenset()
    )
    findings = lint_template(template, context)
    raise typer.Exit(emit(template_report(template, findings), fmt, output))


@app.command("lint-set")
@handle_errors
def lint_set(
    ctx: typer.Context,
    directory: Path = typer.Argument(..., help="A directory of templates."),
    data_file: Path = typer.Option(
        None, "--data", "-d", help="The job data file. Found beside the templates otherwise."
    ),
    profile_path: Path = typer.Option(
        None, "--profile", "-p", help="A terminal profile, so the head swath is checked too."
    ),
    fmt: str = typer.Option(DEFAULT_FORMAT, "--format", "-f", help="text, json, csv or junit."),
    output: Path = typer.Option(None, "--output", "-o", help="Write the report here."),
) -> None:
    """Check every template in a directory against the data file beside them.

    This used to lint each template on its own with a fixed column list, which
    could not see the two defects that actually matter: a field bound to a
    column the job does not supply, and a symbology that will not hold the
    element strings in it.
    """
    get_context(ctx)
    require_directory(directory, "template directory")
    if data_file is not None:
        require_file(data_file, "data file")
    profile = None
    if profile_path is not None:
        require_file(profile_path, "terminal profile")
        profile = read_profile(profile_path)
    templates = read_template_set(directory, data_file=data_file)
    findings = lint_template_set(templates, profile)
    raise typer.Exit(emit(template_set_report(templates, findings), fmt, output))


__all__ = ["app"]
