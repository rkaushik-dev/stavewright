"""The human readable interpretation, which is the text under the bars.

Every GS1 symbol carries its data twice: once as bars a scanner reads and once
as text a person reads when the scanner will not. The second is the HRI and it
has rules of its own, which the front end gets right and hand built templates
frequently do not.

The rules that matter:

The AI goes in brackets and the data does not. ``(01)01001000173250`` is
correct; ``0101001000173250`` is the encoded form and printing that as HRI is
wrong, because a person keying it in has no way to see where the AI ends.

Dates are printed as they are stored, six digits, not expanded. An operator
reading ``150807`` against a label that says ``07/08/15`` in the corner has to
be able to tell they are the same thing.

There is no FNC1 in HRI. The separator is a control character with no glyph, so
a template that renders the concatenated form prints a gap or a box and nobody
can read the field either side of it.

Anything a scanner cannot key back in is a defect, because the whole point of
the HRI is the case where the scanner has already failed.
"""

from __future__ import annotations

from dataclasses import dataclass

from v4t_setup.finding import Finding, Severity
from v4t_setup.gs1.ai import FNC1
from v4t_setup.gs1.parse import CSET82, ElementString, parse

HRI_RULE = "hri"

NON_PRINTING = frozenset({FNC1, "\x00", "\r", "\n", "\t"})
"""Characters with no glyph. None of these belong in printed text."""

MAX_HRI_CHARACTERS = 48
"""Past this the text under a symbol is too small to read at line speed.

Not a specification limit. It is what the quality group measured on a moving
line with a 300 dpi head and it is the number they sign templates off against.
"""


@dataclass(frozen=True, slots=True)
class HriLine:
    """One line of human readable text and where it came from."""

    text: str
    ai: str = ""

    def __len__(self) -> int:
        return len(self.text)


def render(string: ElementString) -> str:
    """The HRI for a whole element string, on one line.

    Brackets round every AI, nothing round the data, no separators.
    """
    return "".join(f"({element.ai}){element.value}" for element in string)


def render_lines(string: ElementString) -> list[HriLine]:
    """The HRI split one element per line.

    What a template does when the single line form is too wide. A person
    reading two lines is fine; a person reading one line that has wrapped
    somewhere arbitrary is not.
    """
    return [HriLine(text=f"({element.ai}){element.value}", ai=element.ai) for element in string]


def is_printable(text: str) -> bool:
    """True when every character in *text* has a glyph a head can mark."""
    return not any(character in NON_PRINTING for character in text)


def check_hri(text: str, string: ElementString | None = None) -> list[Finding]:
    """Check a piece of human readable text against the rules.

    :param string: what the symbol actually encodes, when it is known. Given
        it, the check can say the text and the bars disagree, which is the
        defect that gets product rejected rather than merely queried.
    """
    findings: list[Finding] = []

    if not text:
        return [
            Finding(
                rule=HRI_RULE,
                severity=Severity.ERROR,
                message="the symbol has no human readable text under it",
            )
        ]

    bad = sorted({character for character in text if character in NON_PRINTING})
    if bad:
        findings.append(
            Finding(
                rule=HRI_RULE,
                severity=Severity.ERROR,
                message="the human readable text contains a character with no glyph",
                context={"codepoints": [f"U+{ord(c):04X}" for c in bad]},
            )
        )

    outside = sorted({c for c in text if c not in CSET82 and c not in "()"})
    if outside:
        findings.append(
            Finding(
                rule=HRI_RULE,
                severity=Severity.WARNING,
                message="the human readable text uses characters outside GS1 set 82",
                context={"characters": outside},
            )
        )

    if "(" not in text:
        findings.append(
            Finding(
                rule=HRI_RULE,
                severity=Severity.ERROR,
                message="the human readable text has no bracketed AI, it is the encoded form",
                context={"text": text[:40]},
            )
        )

    if len(text) > MAX_HRI_CHARACTERS:
        findings.append(
            Finding(
                rule=HRI_RULE,
                severity=Severity.WARNING,
                message="the human readable text is too long to read at line speed",
                context={"length": len(text), "limit": MAX_HRI_CHARACTERS},
            )
        )

    if string is not None and text != render(string):
        findings.append(
            Finding(
                rule=HRI_RULE,
                severity=Severity.ERROR,
                message="the human readable text does not match what the symbol encodes",
                context={"printed": text, "encoded": render(string)},
            )
        )

    return findings


def check_element_string(text: str) -> list[Finding]:
    """Parse *text* as an element string and check its own HRI form.

    The convenience form for a template that stores the element string and
    renders the HRI from it, which is what the front end does.
    """
    try:
        parsed = parse(text)
    except Exception:
        return check_hri(text)
    return check_hri(render(parsed), parsed)


__all__ = [
    "HRI_RULE",
    "MAX_HRI_CHARACTERS",
    "NON_PRINTING",
    "HriLine",
    "check_element_string",
    "check_hri",
    "is_printable",
    "render",
    "render_lines",
]
