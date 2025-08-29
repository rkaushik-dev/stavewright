"""Checks a template set has to pass before it goes onto a terminal.

The expensive failures here are all quiet ones. A field bound to a column the
data file does not have prints blank. A font the terminal does not have falls
back to whatever the front end picks and the human readable line goes out of
alignment with the bars. A placeholder value left in a barcode block marks
fourteen zeros onto every item in the batch.

None of those raise anything on the terminal. They come back as a scanner
rejection at the far end of the line, by which point the pallet is wrapped.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from v4t_setup.finding import Finding, Severity
from v4t_setup.gs1.symbology import Shape, all_symbologies, fits_head, is_known, resolve
from v4t_setup.model.profile import TerminalProfile
from v4t_setup.ruleset import Rule as BaseRule
from v4t_setup.ruleset import run_rules
from v4t_setup.templates.fieldspec import FieldSpec, find_field_specs
from v4t_setup.templates.spf import SpfTemplate

SYSTEM_FONTS = frozenset({"Arial", "Courier New", "Tahoma", "Times New Roman", "Verdana"})
"""Fonts a Windows line PC has without the bundle installing anything."""

BUNDLED_FONTS = frozenset({"OCR-B"})
"""Fonts the package ships. ``ocrb.ttf`` is a component in the AIP."""

SUPPORTED_SYMBOLOGIES = frozenset(entry.name for entry in all_symbologies())
"""What the head firmware can render.

Taken from the symbology table rather than written out again. The two lists
were already one name apart when this was a literal, and a name the linter
accepts but the capacity model does not know is worse than either.
"""

PLACEHOLDER_VALUES = frozenset({"0" * n for n in range(8, 21)})
"""All zero runs, which is what an unbound barcode block is left holding."""

TEMPLATE_BLOCK = "TEMPLATE"
BARCODE_BLOCK = "BARCODELABEL"


@dataclass(frozen=True, slots=True)
class TemplateContext:
    """What the rules need to know about where a template is going."""

    profile: TerminalProfile | None = None
    dataset_columns: frozenset[str] = frozenset()
    available_fonts: frozenset[str] = field(default_factory=lambda: SYSTEM_FONTS | BUNDLED_FONTS)
    supported_symbologies: frozenset[str] = SUPPORTED_SYMBOLOGIES
    element_string_length: int | None = None
    """How many characters the barcode blocks will actually carry.

    A template on its own does not know, because the data comes from the job.
    A caller that has the data file passes the longest element string in it and
    the capacity rules become checkable.
    """

    module_dots: int = 2
    """Dots per module the head is set to."""


class TemplateRule(BaseRule[SpfTemplate, TemplateContext]):
    """A check over one template."""


class StructureRule(TemplateRule):
    """A template has exactly one TEMPLATE block and at least one label."""

    name = "structure"

    def check(self, template: SpfTemplate, context: TemplateContext) -> list[Finding]:
        findings: list[Finding] = []
        headers = template.of_kind(TEMPLATE_BLOCK)
        if not headers:
            findings.append(
                self._finding(Severity.ERROR, "template has no TEMPLATE block")
            )
        elif len(headers) > 1:
            findings.append(
                self._finding(
                    Severity.ERROR,
                    "template has more than one TEMPLATE block",
                    count=len(headers),
                )
            )
        if len(template) < 2:
            findings.append(
                self._finding(
                    Severity.WARNING,
                    "template has no label blocks and would print nothing",
                    blocks=list(template.kinds),
                )
            )
        return findings


class BlockOrderRule(TemplateRule):
    """The TEMPLATE block comes first."""

    name = "block-order"

    def check(self, template: SpfTemplate, context: TemplateContext) -> list[Finding]:
        if not template.kinds or template.kinds[0] == TEMPLATE_BLOCK:
            return []
        return [
            self._finding(
                Severity.WARNING,
                "TEMPLATE block is not the first block",
                order=list(template.kinds),
            )
        ]


class DuplicateIndexRule(TemplateRule):
    """Two blocks of the same kind must not share an instance number."""

    name = "duplicate-index"

    def check(self, template: SpfTemplate, context: TemplateContext) -> list[Finding]:
        seen: dict[tuple[str, int], int] = {}
        findings: list[Finding] = []
        for block in template:
            if block.index is None:
                continue
            key = (block.kind, block.index)
            if key in seen:
                findings.append(
                    self._finding(
                        Severity.ERROR,
                        "two blocks share a kind and instance number",
                        kind=block.kind,
                        index=block.index,
                        first_at=seen[key],
                        second_at=block.offset,
                    )
                )
            else:
                seen[key] = block.offset
        return findings


class SymbologyRule(TemplateRule):
    """Every symbology a barcode block names must be one the head can render."""

    name = "symbology"

    def check(self, template: SpfTemplate, context: TemplateContext) -> list[Finding]:
        findings: list[Finding] = []
        for block in template.of_kind(BARCODE_BLOCK):
            named = [
                text
                for text in block.strings()
                if text in context.supported_symbologies or _looks_like_symbology(text)
            ]
            if not named:
                findings.append(
                    self._finding(
                        Severity.WARNING,
                        "barcode block names no symbology this toolkit recognises",
                        index=block.index,
                        strings=block.strings(),
                    )
                )
                continue
            for symbology in named:
                if symbology not in context.supported_symbologies:
                    findings.append(
                        self._finding(
                            Severity.ERROR,
                            "symbology is not one the print head can render",
                            index=block.index,
                            symbology=symbology,
                        )
                    )
        return findings


class PlaceholderRule(TemplateRule):
    """A barcode block still holding an all zero value was never bound."""

    name = "placeholder"

    def check(self, template: SpfTemplate, context: TemplateContext) -> list[Finding]:
        findings: list[Finding] = []
        for block in template.of_kind(BARCODE_BLOCK):
            placeholders = sorted(
                {text for text in block.strings() if text in PLACEHOLDER_VALUES}
            )
            if placeholders:
                findings.append(
                    self._finding(
                        Severity.WARNING,
                        "barcode block still holds a placeholder value",
                        index=block.index,
                        values=placeholders,
                    )
                )
        return findings


class FontRule(TemplateRule):
    """Every font a field names has to be on the terminal."""

    name = "font"

    def check(self, template: SpfTemplate, context: TemplateContext) -> list[Finding]:
        findings: list[Finding] = []
        for spec in _specs(template):
            if spec.font is None:
                continue
            if spec.font.name not in context.available_fonts:
                findings.append(
                    self._finding(
                        Severity.ERROR,
                        "field names a font the terminal does not have",
                        font=spec.font.name,
                        available=sorted(context.available_fonts),
                    )
                )
            if spec.font.size <= 0:
                findings.append(
                    self._finding(
                        Severity.ERROR, "field has a font size of zero or less",
                        font=spec.font.name, size=spec.font.size,
                    )
                )
        return findings


class BoundFieldRule(TemplateRule):
    """A field bound by name needs that name in the data file."""

    name = "bound-field"

    def check(self, template: SpfTemplate, context: TemplateContext) -> list[Finding]:
        if not context.dataset_columns:
            return []
        return [
            self._finding(
                Severity.ERROR,
                "field is bound to a column the data file does not supply",
                reference=spec.reference,
                columns=sorted(context.dataset_columns),
            )
            for spec in _specs(template)
            if spec.is_bound and spec.reference not in context.dataset_columns
        ]


class SourceSlotRule(TemplateRule):
    """Two fields must not draw from the same source slot."""

    name = "source-slot"

    def check(self, template: SpfTemplate, context: TemplateContext) -> list[Finding]:
        seen: set[int] = set()
        findings: list[Finding] = []
        for spec in _specs(template):
            if spec.source is None:
                continue
            if spec.source in seen:
                findings.append(
                    self._finding(
                        Severity.WARNING,
                        "two fields draw from the same source slot",
                        slot=spec.source,
                    )
                )
            seen.add(spec.source)
        return findings


def _looks_like_symbology(text: str) -> bool:
    """Whether a string in a barcode block is trying to name a symbology.

    A block payload carries plenty of printable text that is not a symbology,
    so the linter only complains about something that looks like an attempt.
    """
    if is_known(text):
        return True
    upper = text.upper()
    return any(
        token in upper for token in ("EAN", "UPC", "CODE", "GS1", "ITF", "QR", "MATRIX")
    )


def _specs(template: SpfTemplate) -> list[FieldSpec]:
    return find_field_specs(template.all_strings())


class CapacityRule(TemplateRule):
    """The symbology has to hold the data, and the symbol has to fit the head."""

    name = "capacity"

    def check(self, template: SpfTemplate, context: TemplateContext) -> list[Finding]:
        length = context.element_string_length
        if length is None:
            return []
        findings: list[Finding] = []
        for block in template.of_kind(BARCODE_BLOCK):
            for name in block.strings():
                if not is_known(name):
                    continue
                symbology = resolve(name)
                if not symbology.accepts_length(length):
                    findings.append(
                        self._finding(
                            Severity.ERROR,
                            "the data is not a length this symbology encodes",
                            index=block.index,
                            symbology=name,
                            length=length,
                            accepts=f"{symbology.min_characters}..{symbology.max_characters}",
                        )
                    )
                    continue
                if context.profile is None or symbology.shape is not Shape.MATRIX:
                    continue
                swath = context.profile.machine.swath_dots
                if not fits_head(symbology, length, swath, context.module_dots):
                    findings.append(
                        self._finding(
                            Severity.ERROR,
                            "the symbol is wider than the print head swath",
                            index=block.index,
                            symbology=name,
                            side_dots=symbology.modules_for(length) * context.module_dots,
                            swath_dots=swath,
                        )
                    )
        return findings


DEFAULT_TEMPLATE_RULES: tuple[TemplateRule, ...] = (
    StructureRule(),
    BlockOrderRule(),
    DuplicateIndexRule(),
    SymbologyRule(),
    CapacityRule(),
    PlaceholderRule(),
    FontRule(),
    BoundFieldRule(),
    SourceSlotRule(),
)


def lint_template(
    template: SpfTemplate,
    context: TemplateContext | None = None,
    rules: tuple[TemplateRule, ...] = DEFAULT_TEMPLATE_RULES,
) -> list[Finding]:
    """Run *rules* over *template*, in rule order."""
    return run_rules(rules, template, context or TemplateContext())


__all__ = [
    "BARCODE_BLOCK",
    "BUNDLED_FONTS",
    "DEFAULT_TEMPLATE_RULES",
    "PLACEHOLDER_VALUES",
    "SUPPORTED_SYMBOLOGIES",
    "SYSTEM_FONTS",
    "TEMPLATE_BLOCK",
    "BlockOrderRule",
    "BoundFieldRule",
    "CapacityRule",
    "DuplicateIndexRule",
    "FontRule",
    "PlaceholderRule",
    "SourceSlotRule",
    "StructureRule",
    "SymbologyRule",
    "TemplateContext",
    "TemplateRule",
    "lint_template",
]
