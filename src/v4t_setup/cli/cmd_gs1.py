"""``v4t-setup gs1`` — checking what is going to be marked onto product.

Used by quality rather than by commissioning, so it works on a data file or a
single element string and never needs a terminal, a profile or a bundle.
"""

from __future__ import annotations

from pathlib import Path

import typer

from v4t_setup.cli.common import (
    DEFAULT_FORMAT,
    AppContext,
    emit,
    get_context,
    handle_errors,
    require_file,
)
from v4t_setup.gs1.ai import all_identifiers
from v4t_setup.gs1.dataset import read_dataset
from v4t_setup.gs1.datasetlint import lint_dataset
from v4t_setup.gs1.parse import parse
from v4t_setup.gs1.validate import MarkingContext, validate
from v4t_setup.report.builders import gs1_report
from v4t_setup.report.document import Report, findings_section, lines_section

app = typer.Typer(no_args_is_help=True, add_completion=False)


def _context(shared: AppContext, max_length: int | None) -> MarkingContext:
    return MarkingContext(
        reference_year=shared.settings.reference_year,
        max_encoded_length=max_length,
    )


@app.command("check")
@handle_errors
def check_dataset(
    ctx: typer.Context,
    path: Path = typer.Argument(..., help="A GS1 data file, such as GS1-1.txt."),
    fmt: str = typer.Option(DEFAULT_FORMAT, "--format", "-f", help="text, json, csv or junit."),
    output: Path = typer.Option(None, "--output", "-o", help="Write the report here."),
) -> None:
    """Validate a whole data file."""
    shared = get_context(ctx)
    require_file(path, "data file")
    dataset = read_dataset(path)
    findings = lint_dataset(dataset, _context(shared, None))
    raise typer.Exit(emit(gs1_report(dataset, findings), fmt, output))


@app.command("parse")
@handle_errors
def parse_element_string(
    ctx: typer.Context,
    text: str = typer.Argument(..., help="An element string, bracketed or concatenated."),
    max_length: int = typer.Option(
        None, "--max-length", help="Longest encoded string the head can mark."
    ),
    fmt: str = typer.Option(DEFAULT_FORMAT, "--format", "-f", help="text, json, csv or junit."),
) -> None:
    """Parse one element string and check it."""
    shared = get_context(ctx)
    parsed = parse(text)
    findings = validate(parsed, _context(shared, max_length))
    report = Report(
        title="GS1 element string",
        subtitle=parsed.bracketed(),
        facts={
            "elements": str(len(parsed)),
            "encoded_length": str(parsed.encoded_length()),
        },
    )
    report.add(
        lines_section(
            "Elements",
            [
                f"({element.ai}) {element.identifier.data_title}: {element.value}"
                for element in parsed
            ],
        )
    )
    report.add_if(findings_section("Findings", findings))
    report.derive_outcome()
    raise typer.Exit(emit(report, fmt))


@app.command("identifiers")
@handle_errors
def list_identifiers(
    ctx: typer.Context,
    fmt: str = typer.Option(DEFAULT_FORMAT, "--format", "-f", help="text, json, csv or junit."),
) -> None:
    """List the application identifiers this build knows."""
    get_context(ctx)
    report = Report(title="Application identifiers")
    from v4t_setup.report.document import table_section

    report.add(
        table_section(
            "Known",
            ["ai", "title", "length", "separator"],
            [
                [
                    entry.ai,
                    entry.data_title,
                    str(entry.min_length)
                    if entry.is_fixed_length
                    else f"{entry.min_length}..{entry.max_length}",
                    "yes" if entry.requires_fnc1 else "no",
                ]
                for entry in all_identifiers()
            ],
        )
    )
    raise typer.Exit(emit(report, fmt))


__all__ = ["app"]
