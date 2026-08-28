"""``stavewright gs1`` — checking what is going to be marked onto product.

Used by quality rather than by commissioning, so it works on a data file or a
single element string and never needs a terminal, a profile or a bundle.
"""

from __future__ import annotations

from pathlib import Path

import typer

from stavewright.cli import options
from stavewright.cli.common import (
    AppContext,
    echo_lines,
    fail,
    finish,
    get_context,
    handle_errors,
    require_file,
)
from stavewright.gs1.ai import all_identifiers
from stavewright.gs1.dataset import read_dataset
from stavewright.gs1.datasetlint import lint_dataset
from stavewright.gs1.label import LabelKind, LabelSpec, check_label
from stavewright.gs1.label import check_dataset as check_dataset_labels
from stavewright.gs1.label import describe_rules as describe_label_rules
from stavewright.gs1.parse import ElementString, parse
from stavewright.gs1.validate import MarkingContext, validate
from stavewright.report.builders.quality import gs1_report
from stavewright.report.document import Report, findings_section, lines_section

app = typer.Typer(no_args_is_help=True, add_completion=False)


def _context(shared: AppContext, max_length: int | None) -> MarkingContext:
    return MarkingContext(
        reference_year=shared.settings.reference_year,
        max_encoded_length=max_length,
    )


def _label_kind(kind: str) -> LabelKind:
    """One ``--label`` value, or stop with the list of kinds."""
    try:
        return LabelKind(kind)
    except ValueError:
        fail(
            f"no label kind called {kind}, expected one of "
            f"{', '.join(member.value for member in LabelKind)}"
        )


def _label_spec(kind: str, parsed: ElementString, require: list[str] | None) -> LabelSpec:
    """Turn a ``--label`` value into a spec for one element string."""
    return LabelSpec(
        kind=_label_kind(kind),
        string=parsed,
        extra_required=tuple(require or ()),
    )


@app.command("labels")
@handle_errors
def list_label_rules(ctx: typer.Context) -> None:
    """List what each kind of label has to carry."""
    get_context(ctx)
    echo_lines(describe_label_rules())


@app.command("check")
@handle_errors
def check_dataset(
    ctx: typer.Context,
    path: Path = typer.Argument(..., help="A GS1 data file, such as GS1-1.txt."),
    label: str = typer.Option(
        None, "--label", help="Also check every row as an item, case or pallet label."
    ),
    require: list[str] = typer.Option(
        None, "--require", help="An AI this customer insists on. Repeat it."
    ),
    label_rows: int = typer.Option(
        5, "--label-rows", help="Stop the label check after this many bad rows. 0 for all."
    ),
    fmt: str = options.FORMAT,
    output: Path = options.OUTPUT,
    strict: bool = options.STRICT,
) -> None:
    """Validate a whole data file.

    With ``--label`` it also asks whether each row will print the right kind of
    thing, which the element string rules cannot answer. That one stops after a
    few bad rows by default: a file with the wrong AIs in it has them in every
    row, and a hundred copies of one message is a report nobody finishes.
    """
    shared = get_context(ctx)
    require_file(path, "data file")
    dataset = read_dataset(path)
    findings = lint_dataset(dataset, _context(shared, None))
    if label is not None:
        findings.extend(
            check_dataset_labels(
                dataset,
                _label_kind(label),
                extra_required=tuple(require or ()),
                limit=label_rows,
            )
        )
    finish(gs1_report(dataset, findings), fmt, output, strict=strict)


@app.command("parse")
@handle_errors
def parse_element_string(
    ctx: typer.Context,
    text: str = typer.Argument(..., help="An element string, bracketed or concatenated."),
    max_length: int = typer.Option(
        None, "--max-length", help="Longest encoded string the head can mark."
    ),
    label: str = typer.Option(
        None, "--label", help="Also check it as an item, case or pallet label."
    ),
    require: list[str] = typer.Option(
        None, "--require", help="An AI this customer insists on. Repeat it."
    ),
    fmt: str = options.FORMAT,
    output: Path = options.OUTPUT,
    strict: bool = options.STRICT,
) -> None:
    """Parse one element string and check it.

    Without ``--label`` this says whether the data is well formed. With one it
    also says whether it is the right data for that kind of label, which is a
    different question and the one that gets pallets rejected.
    """
    shared = get_context(ctx)
    parsed = parse(text)
    findings = validate(parsed, _context(shared, max_length))
    if label is not None:
        findings.extend(check_label(_label_spec(label, parsed, require)))
    report = Report(
        title="GS1 element string",
        subtitle=parsed.bracketed(),
        facts={
            "elements": len(parsed),
            "encoded_length": parsed.encoded_length(),
            **({"label": label} if label is not None else {}),
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
    finish(report, fmt, output, strict=strict)


@app.command("identifiers")
@handle_errors
def list_identifiers(
    ctx: typer.Context,
    fmt: str = options.FORMAT,
    output: Path = options.OUTPUT,
    strict: bool = options.STRICT,
) -> None:
    """List the application identifiers this build knows."""
    get_context(ctx)
    report = Report(title="Application identifiers")
    from stavewright.report.document import table_section

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
    finish(report, fmt, output, strict=strict)


__all__ = ["app"]
