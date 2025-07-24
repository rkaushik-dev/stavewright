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
from v4t_setup.gs1.dataset import EXPECTED_COLUMNS
from v4t_setup.report.builders import template_report
from v4t_setup.report.document import Report, findings_section, table_section
from v4t_setup.templates.fieldspec import find_field_specs
from v4t_setup.templates.linter import TemplateContext, lint_template
from v4t_setup.templates.spf import is_template, read_spf

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
    with_dataset: bool = typer.Option(
        False, "--with-dataset", help="Check field bindings against the standard columns."
    ),
    fmt: str = typer.Option(DEFAULT_FORMAT, "--format", "-f", help="text, json, csv or junit."),
    output: Path = typer.Option(None, "--output", "-o", help="Write the report here."),
) -> None:
    """Check every template in a directory."""
    get_context(ctx)
    require_directory(directory, "template directory")
    context = TemplateContext(
        dataset_columns=frozenset(EXPECTED_COLUMNS) if with_dataset else frozenset()
    )
    report = Report(title="Template set", subtitle=str(directory))
    rows: list[list[str]] = []
    findings = []
    for candidate in sorted(directory.iterdir()):
        if not candidate.is_file() or not is_template(candidate):
            continue
        template = read_spf(candidate)
        found = lint_template(template, context)
        findings.extend(
            type(finding)(
                rule=finding.rule,
                severity=finding.severity,
                message=finding.message,
                ai=finding.ai,
                line=finding.line,
                subject=candidate.name,
                context=finding.context,
            )
            for finding in found
        )
        rows.append([candidate.name, str(len(template)), str(len(found))])
    report.add(table_section("Templates", ["file", "blocks", "findings"], rows))
    report.add_if(findings_section("Findings", findings))
    report.facts["templates"] = str(len(rows))
    report.derive_outcome()
    raise typer.Exit(emit(report, fmt, output))


__all__ = ["app"]
