"""A template set: the templates a job uses, and the data file behind them.

The shipped ``Matricode/Quattro/Templates`` directory is one of these. Four
``.spf`` files and a ``GS1-1.txt`` beside them, and nothing anywhere says they
belong together except that they are in the same folder.

Linting one template at a time misses everything interesting. A template bound
to a column is fine on its own and broken against a data file that does not
have it. A symbology is fine until you know how long the element strings are.
Two templates using the same source slot for different fields is only visible
across the set.

So this reads the directory, finds the data file, and lints the templates with
the data file's columns and its longest element string in the context. What
comes back is what a bench engineer would find by loading each one into the
front end and printing it, minus the ink.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path

from stavewright.errors import TemplateError
from stavewright.finding import Finding, Severity, reporter
from stavewright.gs1.dataset import Dataset, read_dataset
from stavewright.model.profile import TerminalProfile
from stavewright.templates.fieldspec import FieldSpec, find_field_specs
from stavewright.templates.linter import TemplateContext, lint_template
from stavewright.templates.spf import SpfTemplate, is_template, read_spf

TEMPLATE_SUFFIX = ".spf"
DATA_SUFFIXES = (".txt", ".csv")

SET_RULE = "template-set"
_finding = reporter(SET_RULE)


@dataclass(frozen=True, slots=True)
class TemplateEntry:
    """One template in a set."""

    name: str
    path: Path
    template: SpfTemplate

    @property
    def specs(self) -> list[FieldSpec]:
        return find_field_specs(self.template.all_strings())

    @property
    def fonts(self) -> frozenset[str]:
        return frozenset(spec.font.name for spec in self.specs if spec.font is not None)

    @property
    def bound_columns(self) -> frozenset[str]:
        return frozenset(
            spec.reference for spec in self.specs if spec.reference is not None
        )

    def describe(self) -> str:
        return f"{self.name} ({len(self.template)} blocks, {self.template.size} bytes)"


@dataclass(frozen=True, slots=True)
class TemplateSet:
    """Every template in one directory, and the data file beside them."""

    root: Path
    entries: tuple[TemplateEntry, ...] = ()
    dataset: Dataset | None = None
    data_path: Path | None = None

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self) -> Iterator[TemplateEntry]:
        return iter(self.entries)

    def __contains__(self, name: str) -> bool:
        return any(entry.name == name for entry in self.entries)

    @property
    def names(self) -> list[str]:
        return [entry.name for entry in self.entries]

    @property
    def fonts(self) -> frozenset[str]:
        """Every font the set names. What preflight has to find on the terminal."""
        return frozenset().union(*(entry.fonts for entry in self.entries)) if self.entries else (
            frozenset()
        )

    @property
    def columns(self) -> frozenset[str]:
        """The data file's columns, or nothing when there is no data file."""
        return frozenset(self.dataset.header) if self.dataset else frozenset()

    def get(self, name: str) -> TemplateEntry:
        """One template by name.

        :raises TemplateError: when the set has no such template.
        """
        for entry in self.entries:
            if entry.name == name:
                return entry
        raise TemplateError(
            "the template set has no such template",
            context={"root": str(self.root), "name": name, "known": self.names},
        )

    def longest_element_string(self) -> int | None:
        """The longest element string in the data file, in characters.

        None when there is no data file, which is what tells the capacity rules
        they have nothing to work from.
        """
        if self.dataset is None:
            return None
        lengths = [
            len(text)
            for row in self.dataset
            for text in (row.item_element_string, row.case_element_string)
            if text
        ]
        return max(lengths) if lengths else None

    def describe(self) -> str:
        data = self.data_path.name if self.data_path else "no data file"
        count = len(self.entries)
        return f"{self.root.name}: {count} template{'' if count == 1 else 's'}, {data}"


def read_template_set(root: Path, *, data_file: Path | None = None) -> TemplateSet:
    """Read every template in *root*, and the data file beside them.

    :param data_file: which file holds the job data. When it is not given the
        directory is searched, and a directory with more than one candidate is
        refused rather than guessed at.
    :raises TemplateError: when the directory does not exist, holds no
        templates, or holds more than one possible data file.
    """
    if not root.is_dir():
        raise TemplateError("template directory does not exist", context={"root": str(root)})

    entries: list[TemplateEntry] = []
    for path in sorted(root.glob(f"*{TEMPLATE_SUFFIX}")):
        if not is_template(path):
            continue
        entries.append(TemplateEntry(name=path.stem, path=path, template=read_spf(path)))
    if not entries:
        raise TemplateError("no templates in this directory", context={"root": str(root)})

    chosen = data_file if data_file is not None else _find_data_file(root)
    dataset = read_dataset(chosen) if chosen is not None else None
    return TemplateSet(
        root=root,
        entries=tuple(entries),
        dataset=dataset,
        data_path=chosen,
    )


def _find_data_file(root: Path) -> Path | None:
    candidates = sorted(
        path for suffix in DATA_SUFFIXES for path in root.glob(f"*{suffix}")
    )
    if not candidates:
        return None
    if len(candidates) > 1:
        raise TemplateError(
            "more than one file here could be the data file, name one",
            context={"root": str(root), "candidates": [path.name for path in candidates]},
        )
    return candidates[0]


def lint_template_set(
    templates: TemplateSet,
    profile: TerminalProfile | None = None,
    *,
    available_fonts: frozenset[str] | None = None,
) -> list[Finding]:
    """Lint every template with what the rest of the set knows.

    The per template rules do the work. This supplies the context none of them
    could get on their own: the data file's columns, and how long the element
    strings in it actually are.
    """
    context = TemplateContext(
        profile=profile,
        dataset_columns=templates.columns,
        element_string_length=templates.longest_element_string(),
    )
    if available_fonts is not None:
        context = replace(context, available_fonts=available_fonts)
    findings: list[Finding] = []
    for entry in templates:
        for finding in lint_template(entry.template, context):
            findings.append(_with_subject(finding, entry.name))
    findings.extend(_set_findings(templates))
    return findings


def _with_subject(finding: Finding, name: str) -> Finding:
    """Stamp the template name onto a finding that came from one template.

    Without it a set of four templates produces four identical messages and
    nothing says which file to open.
    """
    if finding.subject:
        return finding
    return Finding(
        rule=finding.rule,
        severity=finding.severity,
        message=finding.message,
        ai=finding.ai,
        line=finding.line,
        subject=name,
        context=finding.context,
    )


def _set_findings(templates: TemplateSet) -> list[Finding]:
    findings: list[Finding] = []

    if templates.dataset is None:
        findings.append(
            _finding(
                Severity.WARNING,
                "no data file beside these templates, so nothing checked the bindings",
                templates.root.name,
            )
        )

    lowered: dict[str, str] = {}
    for entry in templates:
        key = entry.name.lower()
        if key in lowered:
            findings.append(
                _finding(
                    Severity.ERROR,
                    "two templates differ only in case, which Windows cannot keep apart",
                    entry.name,
                    other=lowered[key],
                )
            )
        else:
            lowered[key] = entry.name

    unused = templates.columns - frozenset().union(
        *(entry.bound_columns for entry in templates)
    ) if templates.entries else frozenset()
    if unused and templates.dataset is not None:
        findings.append(
            _finding(
                Severity.INFO,
                "the data file carries columns no template prints",
                templates.root.name,
                columns=sorted(unused),
            )
        )
    return findings


__all__ = [
    "DATA_SUFFIXES",
    "SET_RULE",
    "TEMPLATE_SUFFIX",
    "TemplateEntry",
    "TemplateSet",
    "lint_template_set",
    "read_template_set",
]
