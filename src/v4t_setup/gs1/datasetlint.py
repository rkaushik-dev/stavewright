"""Rules over a whole data file rather than one element string.

Some defects only show up across rows. A serial that repeats is fine on its own
line and wrong in the file. An index that jumps means the operator's export
dropped something. A ``GS1 DATA`` column that does not agree with the ``GTIN``
and ``SERIAL`` columns beside it means the file was hand edited, and the
barcode and the human readable text under it will disagree on the pallet.

Running this against the shipped ``GS1-1.txt`` reports real findings. That file
is example data rather than production data, so the findings are expected, but
they are the same findings the linter would report on a customer file.
"""

from __future__ import annotations

import re

from v4t_setup.errors import Gs1Error
from v4t_setup.finding import Finding, Severity, count_by_rule
from v4t_setup.gs1.checkdigit import mod10_check_digit, verify_gtin
from v4t_setup.gs1.dataset import Dataset, DatasetRow
from v4t_setup.gs1.parse import parse
from v4t_setup.gs1.validate import MarkingContext, validate
from v4t_setup.ruleset import Rule as BaseRule
from v4t_setup.ruleset import run_rules

LOT_CODE = re.compile(r"^[A-Z]{2}:\d{2}$")
ALIGNMENT_MARK = re.compile(r"^[A-Z]$")
EAN13_LENGTH = 13


class DatasetRule(BaseRule[Dataset, "MarkingContext"]):
    """A check over a whole data file."""

    def _finding(  # type: ignore[override]
        self,
        severity: Severity,
        message: str,
        line: int | None = None,
        **extra: object,
    ) -> Finding:
        # The dataset rules were written with the line number third and every
        # one of them passes it positionally. Keeping that shape here is
        # cheaper and clearer than editing forty call sites to name it.
        return self._make(severity, message, line=line, context=dict(extra))


class RaggedRowRule(DatasetRule):
    """Every row should carry every column."""

    name = "ragged-row"

    def check(self, dataset: Dataset, context: MarkingContext) -> list[Finding]:
        return [
            self._finding(
                Severity.WARNING,
                "row stops before the last column",
                row.line,
                missing=list(row.missing_columns),
                width=row.width,
            )
            for row in dataset.ragged_rows
        ]


class GtinColumnRule(DatasetRule):
    """The GTIN column has to be a real GTIN."""

    name = "gtin-column"

    def check(self, dataset: Dataset, context: MarkingContext) -> list[Finding]:
        findings: list[Finding] = []
        for row in dataset:
            gtin = row.get("GTIN")
            if gtin is None:
                continue
            try:
                good = verify_gtin(gtin)
            except Gs1Error as exc:
                findings.append(
                    self._finding(Severity.ERROR, exc.message, row.line, value=gtin)
                )
                continue
            if not good:
                findings.append(
                    self._finding(
                        Severity.ERROR,
                        "GTIN check digit does not match the data",
                        row.line,
                        value=gtin,
                        expected=str(mod10_check_digit(gtin[:-1])),
                        found=gtin[-1],
                    )
                )
        return findings


class ElementStringRule(DatasetRule):
    """Both element string columns must parse and pass the per string rules."""

    name = "element-string"

    columns = ("GS1 DATA", "GS1")

    def check(self, dataset: Dataset, context: MarkingContext) -> list[Finding]:
        findings: list[Finding] = []
        for row in dataset:
            for column in self.columns:
                text = row.get(column)
                if not text:
                    continue
                try:
                    parsed = parse(text)
                except Gs1Error as exc:
                    findings.append(
                        self._finding(
                            Severity.ERROR, exc.message, row.line, column=column, value=text
                        )
                    )
                    continue
                for finding in validate(parsed, context):
                    findings.append(
                        Finding(
                            rule=f"{self.name}/{finding.rule}",
                            severity=finding.severity,
                            message=finding.message,
                            ai=finding.ai,
                            line=row.line,
                            context={**finding.context, "column": column},
                        )
                    )
        return findings


class ColumnAgreementRule(DatasetRule):
    """``GS1 DATA`` must be built from the GTIN and SERIAL columns beside it."""

    name = "column-agreement"

    def check(self, dataset: Dataset, context: MarkingContext) -> list[Finding]:
        findings: list[Finding] = []
        for row in dataset:
            text = row.get("GS1 DATA")
            gtin = row.get("GTIN")
            serial = row.get("SERIAL")
            if not text or gtin is None or serial is None:
                continue
            try:
                parsed = parse(text)
            except Gs1Error:
                continue
            findings.extend(self._compare(row, parsed.get("01"), gtin, "GTIN", "01"))
            findings.extend(self._compare(row, parsed.get("21"), serial, "SERIAL", "21"))
        return findings

    def _compare(
        self,
        row: DatasetRow,
        in_string: str | None,
        in_column: str,
        column: str,
        ai: str,
    ) -> list[Finding]:
        if in_string is None:
            return [
                self._finding(
                    Severity.ERROR,
                    f"element string carries no ({ai}) to match the {column} column",
                    row.line,
                    column=column,
                )
            ]
        if in_string != in_column:
            return [
                self._finding(
                    Severity.ERROR,
                    f"element string ({ai}) disagrees with the {column} column",
                    row.line,
                    column=column,
                    in_string=in_string,
                    in_column=in_column,
                )
            ]
        return []


class DuplicateSerialRule(DatasetRule):
    """A serial number identifies one item and must not repeat."""

    name = "duplicate-serial"

    def check(self, dataset: Dataset, context: MarkingContext) -> list[Finding]:
        first_seen: dict[str, int] = {}
        findings: list[Finding] = []
        for row in dataset:
            serial = row.get("SERIAL")
            if serial is None:
                continue
            if serial in first_seen:
                findings.append(
                    self._finding(
                        Severity.ERROR,
                        "serial number already used earlier in the file",
                        row.line,
                        serial=serial,
                        first_seen=first_seen[serial],
                    )
                )
            else:
                first_seen[serial] = row.line
        return findings


class IndexContinuityRule(DatasetRule):
    """NDX should count from one without gaps or repeats."""

    name = "index-continuity"

    def check(self, dataset: Dataset, context: MarkingContext) -> list[Finding]:
        findings: list[Finding] = []
        expected = 1
        for row in dataset:
            raw = row.get("NDX")
            if raw is None:
                continue
            if not raw.isdigit():
                findings.append(
                    self._finding(Severity.ERROR, "index is not a number", row.line, value=raw)
                )
                continue
            value = int(raw)
            if value != expected:
                findings.append(
                    self._finding(
                        Severity.WARNING,
                        "index is not the next one in sequence",
                        row.line,
                        expected=expected,
                        found=value,
                    )
                )
            expected = value + 1
        return findings


class LotCodeRule(DatasetRule):
    """LOTCODE is two upper case letters, a colon and two digits."""

    name = "lot-code"

    def check(self, dataset: Dataset, context: MarkingContext) -> list[Finding]:
        return [
            self._finding(
                Severity.WARNING,
                "lot code is not in the two letter colon two digit form",
                row.line,
                value=row.get("LOTCODE"),
            )
            for row in dataset
            if row.get("LOTCODE") is not None and not LOT_CODE.match(row.get("LOTCODE") or "")
        ]


class AlignmentMarkRule(DatasetRule):
    """ADX selects a head alignment pattern and is a single upper case letter."""

    name = "alignment-mark"

    def check(self, dataset: Dataset, context: MarkingContext) -> list[Finding]:
        return [
            self._finding(
                Severity.WARNING,
                "alignment mark is not a single upper case letter",
                row.line,
                value=row.get("ADX"),
            )
            for row in dataset
            if row.get("ADX") is not None and not ALIGNMENT_MARK.match(row.get("ADX") or "")
        ]


class Ean13ColumnRule(DatasetRule):
    """The EAN13 column should hold thirteen digits."""

    name = "ean13-column"

    def check(self, dataset: Dataset, context: MarkingContext) -> list[Finding]:
        findings: list[Finding] = []
        for row in dataset:
            value = row.get("EAN13")
            if value is None:
                continue
            if not value.isdigit():
                findings.append(
                    self._finding(Severity.ERROR, "EAN13 is not numeric", row.line, value=value)
                )
                continue
            if len(value) != EAN13_LENGTH:
                findings.append(
                    self._finding(
                        Severity.WARNING,
                        "EAN13 column does not hold thirteen digits",
                        row.line,
                        value=value,
                        length=len(value),
                    )
                )
        return findings


DEFAULT_DATASET_RULES: tuple[DatasetRule, ...] = (
    RaggedRowRule(),
    GtinColumnRule(),
    ElementStringRule(),
    ColumnAgreementRule(),
    DuplicateSerialRule(),
    IndexContinuityRule(),
    LotCodeRule(),
    AlignmentMarkRule(),
    Ean13ColumnRule(),
)


def lint_dataset(
    dataset: Dataset,
    context: MarkingContext | None = None,
    rules: tuple[DatasetRule, ...] = DEFAULT_DATASET_RULES,
) -> list[Finding]:
    """Run *rules* over *dataset*, sorted by line then rule name."""
    findings = run_rules(rules, dataset, context or MarkingContext())
    return sorted(findings, key=lambda f: (f.line or 0, f.rule))


def summarise(findings: list[Finding]) -> dict[str, int]:
    """How many findings each rule produced, highest first."""
    return count_by_rule(findings)


__all__ = [
    "DEFAULT_DATASET_RULES",
    "AlignmentMarkRule",
    "ColumnAgreementRule",
    "DatasetRule",
    "DuplicateSerialRule",
    "Ean13ColumnRule",
    "ElementStringRule",
    "GtinColumnRule",
    "IndexContinuityRule",
    "LotCodeRule",
    "RaggedRowRule",
    "lint_dataset",
    "summarise",
]
