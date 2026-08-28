"""Rules an element string has to satisfy before a terminal will mark it.

Parsing tells you the string is well formed. That is not the same as saying it
is correct, and the difference is what ends up on product. A GTIN with a bad
check digit parses perfectly. So does a best before date in 1998, a serial that
is twenty one characters long once the customer's prefix is added, and a symbol
that is wider than the print head.

Rules are objects rather than a function with branches so that a site can
switch one off without editing this file. The default set is what the
commissioning group applies to every bundle.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from stavewright.errors import Gs1Error
from stavewright.finding import Finding, Severity, has_errors, worst_severity
from stavewright.gs1.checkdigit import mod10_check_digit, verify_mod10
from stavewright.gs1.dates import parse_date
from stavewright.gs1.parse import ElementString
from stavewright.ruleset import Rule as BaseRule
from stavewright.ruleset import run_rules

PRIMARY_IDENTIFIERS = ("00", "01", "02", "8004", "8018")
"""AIs that identify the thing being marked. Exactly one should be present."""

ALLOWED_PAIRS = frozenset({frozenset({"00", "02"})})
"""Primary identifiers that may appear together after all.

``(00)`` identifies a pallet and ``(02)`` says what is on it, and the two
together are the standard logistic label. This rule refused that pairing
until the label rules were written and the canonical pallet label failed it.
Everything else on the list identifies one thing, so two of them means two
things and the reader cannot know which.
"""

DATE_AIS = ("11", "12", "13", "15", "16", "17")


@dataclass(frozen=True, slots=True)
class MarkingContext:
    """What the rules need to know about where this string is going."""

    reference_year: int | None = None
    today: date | None = None
    max_encoded_length: int | None = None
    require_primary: bool = True


class Rule(BaseRule[ElementString, "MarkingContext"]):
    """A check over one element string."""


class CheckDigitRule(Rule):
    """Every AI whose row says it carries a check digit must have a good one."""

    name = "check-digit"

    def check(self, string: ElementString, context: MarkingContext) -> list[Finding]:
        findings: list[Finding] = []
        for element in string:
            if not element.identifier.check_digit:
                continue
            if not verify_mod10(element.value):
                findings.append(
                    self._finding(
                        Severity.ERROR,
                        "check digit does not match the data",
                        element.ai,
                        value=element.value,
                        expected=str(mod10_check_digit(element.value[:-1])),
                        found=element.value[-1],
                    )
                )
        return findings


class PrimaryIdentifierRule(Rule):
    """Exactly one primary identifier, and it comes first."""

    name = "primary-identifier"

    def check(self, string: ElementString, context: MarkingContext) -> list[Finding]:
        present = [ai for ai in string.ais if ai in PRIMARY_IDENTIFIERS]
        if not present:
            if not context.require_primary:
                return []
            return [
                self._finding(
                    Severity.ERROR,
                    "element string carries no primary identifier",
                    None,
                    expected=list(PRIMARY_IDENTIFIERS),
                )
            ]
        findings: list[Finding] = []
        if len(present) > 1 and frozenset(present) not in ALLOWED_PAIRS:
            findings.append(
                self._finding(
                    Severity.ERROR,
                    "element string carries more than one primary identifier",
                    None,
                    found=present,
                )
            )
        if string.ais[0] not in PRIMARY_IDENTIFIERS:
            findings.append(
                self._finding(
                    Severity.WARNING,
                    "primary identifier is not the first element",
                    present[0],
                    order=list(string.ais),
                )
            )
        return findings


class DuplicateAiRule(Rule):
    """No AI appears twice."""

    name = "duplicate-ai"

    def check(self, string: ElementString, context: MarkingContext) -> list[Finding]:
        seen: dict[str, int] = {}
        for ai in string.ais:
            seen[ai] = seen.get(ai, 0) + 1
        return [
            self._finding(Severity.ERROR, "application identifier appears more than once", ai,
                          count=count)
            for ai, count in sorted(seen.items())
            if count > 1
        ]


class DateSanityRule(Rule):
    """Date AIs must parse, and an expiry in the past is worth saying out loud."""

    name = "date-sanity"

    def check(self, string: ElementString, context: MarkingContext) -> list[Finding]:
        findings: list[Finding] = []
        for element in string:
            if element.ai not in DATE_AIS:
                continue
            try:
                parsed = parse_date(element.value, reference_year=context.reference_year)
            except Gs1Error as exc:
                findings.append(
                    self._finding(Severity.ERROR, exc.message, element.ai, value=element.value)
                )
                continue
            if context.today is not None and parsed.to_date() < context.today:
                findings.append(
                    self._finding(
                        Severity.WARNING,
                        "date is in the past",
                        element.ai,
                        value=element.value,
                        resolved=parsed.to_date().isoformat(),
                    )
                )
        return findings


class DateOrderRule(Rule):
    """Production date must not be after a best before or expiry date."""

    name = "date-order"

    def check(self, string: ElementString, context: MarkingContext) -> list[Finding]:
        produced = string.get("11")
        if produced is None:
            return []
        findings: list[Finding] = []
        try:
            start = parse_date(produced, reference_year=context.reference_year).to_date()
        except Gs1Error:
            return []
        for later_ai in ("15", "16", "17"):
            value = string.get(later_ai)
            if value is None:
                continue
            try:
                end = parse_date(value, reference_year=context.reference_year).to_date()
            except Gs1Error:
                continue
            if end < start:
                findings.append(
                    self._finding(
                        Severity.ERROR,
                        "date is before the production date",
                        later_ai,
                        production=start.isoformat(),
                        value=end.isoformat(),
                    )
                )
        return findings


class ExclusiveDateRule(Rule):
    """A best before and an expiry on the same item is ambiguous."""

    name = "exclusive-dates"

    def check(self, string: ElementString, context: MarkingContext) -> list[Finding]:
        both = [ai for ai in ("15", "17") if ai in string]
        if len(both) < 2:
            return []
        return [
            self._finding(
                Severity.WARNING,
                "best before and expiry are both present, which scanners read differently",
                None,
                found=both,
            )
        ]


class EncodedLengthRule(Rule):
    """The symbol has to fit the print head."""

    name = "encoded-length"

    def check(self, string: ElementString, context: MarkingContext) -> list[Finding]:
        limit = context.max_encoded_length
        if limit is None:
            return []
        actual = string.encoded_length()
        if actual <= limit:
            return []
        return [
            self._finding(
                Severity.ERROR,
                "encoded element string is longer than the head can mark",
                None,
                length=actual,
                limit=limit,
            )
        ]


DEFAULT_RULES: tuple[Rule, ...] = (
    CheckDigitRule(),
    PrimaryIdentifierRule(),
    DuplicateAiRule(),
    DateSanityRule(),
    DateOrderRule(),
    ExclusiveDateRule(),
    EncodedLengthRule(),
)


def validate(
    string: ElementString,
    context: MarkingContext | None = None,
    rules: tuple[Rule, ...] = DEFAULT_RULES,
) -> list[Finding]:
    """Run *rules* over *string*, returning every finding in rule order."""
    return run_rules(rules, string, context or MarkingContext())


# Finding, Severity, has_errors and worst_severity live in stavewright.finding
# now. They are re-exported here because the GS1 side of the toolkit is where
# most callers meet them first.

__all__ = [
    "ALLOWED_PAIRS",
    "DATE_AIS",
    "DEFAULT_RULES",
    "PRIMARY_IDENTIFIERS",
    "CheckDigitRule",
    "DateOrderRule",
    "DateSanityRule",
    "DuplicateAiRule",
    "EncodedLengthRule",
    "ExclusiveDateRule",
    "Finding",
    "MarkingContext",
    "PrimaryIdentifierRule",
    "Rule",
    "Severity",
    "has_errors",
    "validate",
    "worst_severity",
]
