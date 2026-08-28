"""The little language a template block uses to describe a printed field.

Both text blocks in the shipped ``T1.spf`` carry one of these::

    F:Arial,12,0,0,S:3,ERT
    F:Arial,12,0,0,S:12,#Cognex Code

It is comma separated, but not positionally, because the font run spends four
of those commas on its own. The parser walks the tokens instead:

``F:``
    opens a font run and takes the next three tokens as size, bold and italic.
``S:``
    the source slot the block draws its data from.
``#``
    a literal, and it is the only token allowed to contain spaces.
anything else
    a data reference by name, such as ``ERT``.

A block with a literal prints the same text on every item. A block with a data
reference prints whatever the data file supplies for that name, and if the file
does not supply it the block prints nothing and nobody notices until the codes
come back unreadable.
"""

from __future__ import annotations

from dataclasses import dataclass

from stavewright.errors import TemplateError

FONT_PREFIX = "F:"
SOURCE_PREFIX = "S:"
LITERAL_PREFIX = "#"
FONT_ARGUMENTS = 3
"""How many tokens follow the font name: size, bold, italic."""


@dataclass(frozen=True, slots=True)
class FontSpec:
    """The ``F:`` run."""

    name: str
    size: int
    bold: bool = False
    italic: bool = False

    def __str__(self) -> str:
        return f"{FONT_PREFIX}{self.name},{self.size},{int(self.bold)},{int(self.italic)}"

    def describe(self) -> str:
        styles = [word for word, on in (("bold", self.bold), ("italic", self.italic)) if on]
        suffix = f" {' '.join(styles)}" if styles else ""
        return f"{self.name} {self.size}pt{suffix}"


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """A whole parsed specification."""

    font: FontSpec | None = None
    source: int | None = None
    literal: str | None = None
    reference: str | None = None
    extras: tuple[str, ...] = ()

    @property
    def is_literal(self) -> bool:
        """True when this block prints fixed text."""
        return self.literal is not None

    @property
    def is_bound(self) -> bool:
        """True when this block draws from a named data field."""
        return self.reference is not None

    def __str__(self) -> str:
        parts: list[str] = []
        if self.font is not None:
            parts.append(str(self.font))
        if self.source is not None:
            parts.append(f"{SOURCE_PREFIX}{self.source}")
        if self.literal is not None:
            parts.append(f"{LITERAL_PREFIX}{self.literal}")
        if self.reference is not None:
            parts.append(self.reference)
        parts.extend(self.extras)
        return ",".join(parts)

    def describe(self) -> str:
        font = self.font.describe() if self.font else "no font"
        if self.literal is not None:
            what = f'literal "{self.literal}"'
        elif self.reference is not None:
            what = f"field {self.reference}"
        else:
            what = "no data"
        slot = f" from slot {self.source}" if self.source is not None else ""
        return f"{what}{slot}, {font}"


def _parse_flag(token: str, what: str, text: str) -> bool:
    if token == "0":
        return False
    if token == "1":
        return True
    raise TemplateError(
        f"font {what} flag must be 0 or 1",
        context={"value": token, "spec": text},
    )


def parse_field_spec(text: str) -> FieldSpec:
    """Parse one specification string.

    :raises TemplateError: on an empty string, a truncated font run, a
        non numeric font size or source slot, or a flag that is not 0 or 1.
    """
    if not text.strip():
        raise TemplateError("field specification is empty")

    tokens = text.split(",")
    font: FontSpec | None = None
    source: int | None = None
    literal: str | None = None
    reference: str | None = None
    extras: list[str] = []

    position = 0
    while position < len(tokens):
        token = tokens[position]
        if token.startswith(FONT_PREFIX):
            remaining = len(tokens) - position - 1
            if remaining < FONT_ARGUMENTS:
                raise TemplateError(
                    "font run is truncated",
                    context={"spec": text, "expected": FONT_ARGUMENTS, "found": remaining},
                )
            name = token[len(FONT_PREFIX) :]
            raw_size, raw_bold, raw_italic = tokens[position + 1 : position + 4]
            if not raw_size.isdigit():
                raise TemplateError(
                    "font size is not a number",
                    context={"value": raw_size, "spec": text},
                )
            font = FontSpec(
                name=name,
                size=int(raw_size),
                bold=_parse_flag(raw_bold, "bold", text),
                italic=_parse_flag(raw_italic, "italic", text),
            )
            position += 1 + FONT_ARGUMENTS
            continue
        if token.startswith(SOURCE_PREFIX):
            raw = token[len(SOURCE_PREFIX) :]
            if not raw.isdigit():
                raise TemplateError(
                    "source slot is not a number",
                    context={"value": raw, "spec": text},
                )
            source = int(raw)
        elif token.startswith(LITERAL_PREFIX):
            literal = token[len(LITERAL_PREFIX) :]
        elif token:
            if reference is None:
                reference = token
            else:
                extras.append(token)
        position += 1

    return FieldSpec(
        font=font,
        source=source,
        literal=literal,
        reference=reference,
        extras=tuple(extras),
    )


def try_parse_field_spec(text: str) -> FieldSpec | None:
    """Parse, or return None rather than raising.

    Block payloads contain plenty of printable runs that are not field
    specifications, so the scanner needs a way to ask without handling an
    exception per candidate.
    """
    if FONT_PREFIX not in text and SOURCE_PREFIX not in text:
        return None
    try:
        return parse_field_spec(text)
    except TemplateError:
        return None


def find_field_specs(strings: list[str]) -> list[FieldSpec]:
    """Parse every string that looks like a specification, in order."""
    found = [try_parse_field_spec(text) for text in strings]
    return [spec for spec in found if spec is not None]


__all__ = [
    "FONT_ARGUMENTS",
    "FONT_PREFIX",
    "LITERAL_PREFIX",
    "SOURCE_PREFIX",
    "FieldSpec",
    "FontSpec",
    "find_field_specs",
    "parse_field_spec",
    "try_parse_field_spec",
]
