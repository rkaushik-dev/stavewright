"""What has to be on a label, as opposed to what is allowed in a barcode.

The element string rules answer whether the data is well formed. They cannot
answer whether it is the right data, because that depends on what the label is
for, and a pallet label and an item mark carry different things while both
parsing perfectly.

Three kinds go out of this plant:

An item mark. A GTIN and something that tells one item from another, which is
either a serial or a batch and a date. A GTIN on its own is legal and useless:
nothing can be recalled by it.

A case label. A GTIN for the case, a batch, and a count of what is inside.
The count is the one people leave off, and a case that says nothing about how
many is a case somebody has to open.

A pallet label. An SSCC, which is the thing the whole logistics chain scans,
and normally the GTIN of what is on it and how many. A pallet without an SSCC
is a pallet that cannot be tracked, and that is an error rather than a warning.

The pallet rules are the ones worth reading twice. The GTIN goes in ``(02)``,
not ``(01)``, and the count in ``(37)``, not ``(30)``. ``(00)`` and ``(01)``
are both primary identifiers and an element string may only carry one, so a
pallet label with ``(01)`` on it does not just break a convention: it fails the
element string rules as well. This table said ``(01)`` and ``(30)`` for a day
and the validator was what noticed.

The AIs each kind must, may and must not carry are tables rather than code,
because they change when a customer's specification changes and everybody who
has to argue about them can read a table.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from v4t_setup.errors import Gs1Error
from v4t_setup.finding import Finding, Severity, reporter
from v4t_setup.gs1.ai import resolve
from v4t_setup.gs1.checkdigit import mod10_check_digit
from v4t_setup.gs1.dataset import Dataset
from v4t_setup.gs1.parse import ElementString, parse

RULE = "label"

SSCC = "00"
GTIN = "01"
CONTENT = "02"
BATCH = "10"
PRODUCTION_DATE = "11"
BEST_BEFORE = "15"
EXPIRY = "17"
SERIAL = "21"
COUNT = "30"
CONTAINED_COUNT = "37"
"""Count of trade items in a logistic unit. ``(30)`` is the count on a trade
item itself, and the two are not interchangeable."""

SSCC_LENGTH = 18


class LabelKind(StrEnum):
    """What a label is for. Different labels, different rules."""

    ITEM = "item"
    CASE = "case"
    PALLET = "pallet"


@dataclass(frozen=True, slots=True)
class LabelRules:
    """What one kind of label has to carry."""

    kind: LabelKind
    required: tuple[str, ...] = ()
    """Missing any of these is an error."""

    one_of: tuple[tuple[str, ...], ...] = ()
    """Each group needs at least one member present. Missing is an error."""

    expected: tuple[str, ...] = ()
    """Missing any of these is a warning. Legal, and somebody will ask."""

    forbidden: tuple[str, ...] = ()
    """Present is an error. Usually because it belongs on a different label."""


RULES: dict[LabelKind, LabelRules] = {
    LabelKind.ITEM: LabelRules(
        kind=LabelKind.ITEM,
        required=(GTIN,),
        one_of=((SERIAL, BATCH),),
        expected=(),
        forbidden=(SSCC, COUNT),
    ),
    LabelKind.CASE: LabelRules(
        kind=LabelKind.CASE,
        required=(GTIN, BATCH),
        one_of=(),
        expected=(COUNT,),
        forbidden=(SSCC,),
    ),
    LabelKind.PALLET: LabelRules(
        kind=LabelKind.PALLET,
        required=(SSCC,),
        one_of=(),
        expected=(CONTENT, CONTAINED_COUNT),
        forbidden=(SERIAL, GTIN),
    ),
}


@dataclass(frozen=True, slots=True)
class LabelSpec:
    """A label to check, and what it is for."""

    kind: LabelKind
    string: ElementString
    reference: str = ""
    """Where this came from, for the report. A template name or a row."""

    extra_required: tuple[str, ...] = field(default=())
    """AIs a particular customer insists on, on top of the standard ones."""


def _title(ai: str) -> str:
    try:
        return resolve(ai).data_title
    except Exception:
        return ai


TAKES_AN = frozenset({"SSCC"})
"""Data titles read aloud as starting with a vowel.

SSCC is "ess ess see see", so it takes "an" while its first letter says
otherwise. A letter test alone gets this one wrong in both directions: it says
"an SERIAL" as well. These messages go in front of customers, so it is worth
the four lines.
"""


def _article(word: str) -> str:
    """"a" or "an" for one word, spoken rather than spelled."""
    return "an" if word in TAKES_AN or word[:1].upper() in "AEIOU" else "a"


def _named(ai: str) -> str:
    """The data title with the right article in front."""
    title = _title(ai)
    return f"{_article(title)} {title}"


def _label(kind: LabelKind) -> str:
    """"an item label", "a pallet label"."""
    return f"{_article(kind.value)} {kind.value} label"


def _sentence(text: str) -> str:
    """Capitalise the first letter and leave the rest, which is a data title."""
    return text[:1].upper() + text[1:]


def _finding(severity: Severity, message: str, spec: LabelSpec, **context: object) -> Finding:
    """A finding about one label.

    Not :func:`~v4t_setup.finding.reporter` directly, because the subject and
    one context key both come off the spec and every call site has one. This
    is that shared helper with the spec unpacked.
    """
    make = reporter(RULE, label=spec.kind.value)
    return make(severity, message, spec.reference or spec.kind.value, **context)


def check_sscc(value: str) -> str | None:
    """Why *value* is not an SSCC, or None when it is one.

    Its own function because an SSCC is the identifier the whole logistics
    chain keys on, and a wrong one is not found until a pallet is missing.
    """
    if len(value) != SSCC_LENGTH:
        return f"an SSCC is {SSCC_LENGTH} digits, this one is {len(value)}"
    if not value.isdigit():
        return "an SSCC is digits only"
    expected = str(mod10_check_digit(value[:-1]))
    if value[-1] != expected:
        return f"the check digit should be {expected}"
    return None


def check_label(spec: LabelSpec) -> list[Finding]:
    """Check one label against the rules for its kind."""
    rules = RULES[spec.kind]
    present = {element.ai for element in spec.string}
    findings: list[Finding] = []

    for ai in (*rules.required, *spec.extra_required):
        if ai not in present:
            findings.append(
                _finding(
                    Severity.ERROR,
                    f"{_label(spec.kind)} has to carry {_named(ai)}",
                    spec,
                    ai=ai,
                )
            )

    for group in rules.one_of:
        if not present & set(group):
            titles = " or ".join(_named(ai) for ai in group)
            findings.append(
                _finding(
                    Severity.ERROR,
                    f"{_label(spec.kind)} needs {titles} to tell one from another",
                    spec,
                    one_of=list(group),
                )
            )

    for ai in rules.expected:
        if ai not in present:
            findings.append(
                _finding(
                    Severity.WARNING,
                    f"{_label(spec.kind)} normally carries {_named(ai)}",
                    spec,
                    ai=ai,
                )
            )

    for ai in rules.forbidden:
        if ai in present:
            findings.append(
                _finding(
                    Severity.ERROR,
                    f"{_sentence(_named(ai))} does not belong on {_label(spec.kind)}",
                    spec,
                    ai=ai,
                )
            )

    for element in spec.string:
        if element.ai == SSCC:
            reason = check_sscc(element.value)
            if reason is not None:
                findings.append(
                    _finding(
                        Severity.ERROR,
                        f"the SSCC is not usable: {reason}",
                        spec,
                        ai=SSCC,
                        value=element.value,
                    )
                )
    return findings


def check_labels(specs: list[LabelSpec]) -> list[Finding]:
    """Check several labels, in order."""
    return [finding for spec in specs for finding in check_label(spec)]


def check_dataset(
    dataset: Dataset,
    kind: LabelKind = LabelKind.ITEM,
    *,
    extra_required: tuple[str, ...] = (),
    limit: int = 0,
) -> list[Finding]:
    """Check every element string in a data file as a label of *kind*.

    The element string rules already run over a data file. This asks the other
    question: whether what each row will print is the right kind of thing.

    :param limit: stop after this many rows have produced findings. A data file
        with the wrong AIs in it has the wrong AIs in every row, and a hundred
        and thirty copies of one message is a report nobody reads to the end.
        Zero means no limit.
    """
    findings: list[Finding] = []
    seen = 0
    for row in dataset:
        text = row.item_element_string if kind is LabelKind.ITEM else row.case_element_string
        if not text:
            continue
        try:
            parsed = parse(text)
        except Gs1Error:
            # Not this module's finding to make. The dataset rules already
            # report a row whose element string will not parse, and saying it
            # twice in one report helps nobody.
            continue
        found = check_label(
            LabelSpec(
                kind=kind,
                string=parsed,
                reference=f"line {row.line}",
                extra_required=extra_required,
            )
        )
        if not found:
            continue
        findings.extend(found)
        seen += 1
        if limit and seen >= limit:
            findings.append(
                _finding(
                    Severity.INFO,
                    f"stopped after {limit} rows, the rest will say the same",
                    LabelSpec(kind=kind, string=parsed, reference=f"line {row.line}"),
                    rows_checked=seen,
                )
            )
            break
    return findings


def describe_rules() -> list[str]:
    """One line per label kind, for a help screen."""
    lines: list[str] = []
    for kind in LabelKind:
        rules = RULES[kind]
        parts = [f"needs {', '.join(_title(ai) for ai in rules.required)}"]
        for group in rules.one_of:
            parts.append("and " + " or ".join(_named(ai) for ai in group))
        if rules.expected:
            parts.append(f"expects {', '.join(_title(ai) for ai in rules.expected)}")
        if rules.forbidden:
            parts.append(f"never {', '.join(_title(ai) for ai in rules.forbidden)}")
        lines.append(f"{kind.value}: {'; '.join(parts)}")
    return lines


__all__ = [
    "BATCH",
    "CONTAINED_COUNT",
    "CONTENT",
    "COUNT",
    "GTIN",
    "RULE",
    "RULES",
    "SERIAL",
    "SSCC",
    "SSCC_LENGTH",
    "LabelKind",
    "LabelRules",
    "LabelSpec",
    "check_dataset",
    "check_label",
    "check_labels",
    "check_sscc",
    "describe_rules",
]
