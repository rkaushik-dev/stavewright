"""Parsing GS1 element strings, in both the forms a terminal deals with.

The human readable form is what an operator types into a template field and
what gets printed under the barcode::

    (01)00125682945601(21)0001

The concatenated form is what the symbol actually encodes. There are no
brackets, and a variable length element is terminated by an FNC1 separator
unless it happens to be the last one::

    0100125682945601210001

Reading the second form is the hard direction, because nothing marks where an
AI ends. The parser takes the longest known AI at the cursor, then consumes
exactly the number of characters that AI's row says, and for a variable length
AI reads up to the next separator or the end of the string.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from v4t_setup.errors import Gs1Error
from v4t_setup.gs1.ai import (
    FNC1,
    ApplicationIdentifier,
    CharacterSet,
    longest_prefix,
    resolve,
)

BRACKETED = re.compile(r"\((\d{2,4})\)")

# GS1 AI encodable character set 82. Anything outside it cannot be marked.
CSET82 = frozenset(
    "!\"%&'()*+,-./0123456789:;<=>?"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ_"
    "abcdefghijklmnopqrstuvwxyz"
)


@dataclass(frozen=True, slots=True)
class Element:
    """One AI and its data, with where it came from."""

    identifier: ApplicationIdentifier
    value: str
    offset: int = 0

    @property
    def ai(self) -> str:
        return self.identifier.ai

    def bracketed(self) -> str:
        """The human readable form of this element alone."""
        return f"({self.ai}){self.value}"

    def concatenated(self, *, separator: bool) -> str:
        """The encoded form, with a separator appended when *separator* is set."""
        return f"{self.ai}{self.value}{FNC1 if separator else ''}"

    def __str__(self) -> str:
        return self.bracketed()


@dataclass(frozen=True, slots=True)
class ElementString:
    """A whole element string, as a sequence of :class:`Element`."""

    elements: tuple[Element, ...] = ()

    def __len__(self) -> int:
        return len(self.elements)

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self.elements)

    def __contains__(self, ai: str) -> bool:
        return any(element.ai == ai for element in self.elements)

    def get(self, ai: str) -> str | None:
        """The value carried by *ai*, or None when it is not present."""
        for element in self.elements:
            if element.ai == ai:
                return element.value
        return None

    def require(self, ai: str) -> str:
        """The value carried by *ai*.

        :raises Gs1Error: when the element string does not carry it.
        """
        value = self.get(ai)
        if value is None:
            raise Gs1Error(
                "element string is missing a required application identifier",
                context={"ai": ai, "present": [e.ai for e in self.elements]},
            )
        return value

    @property
    def ais(self) -> tuple[str, ...]:
        """The AIs in the order they appear."""
        return tuple(element.ai for element in self.elements)

    def bracketed(self) -> str:
        """Render in the human readable form."""
        return "".join(element.bracketed() for element in self.elements)

    def concatenated(self) -> str:
        """Render in the encoded form, inserting separators where they are needed.

        The last element never gets a separator, whatever its AI, because the
        end of the string terminates it.
        """
        parts: list[str] = []
        last = len(self.elements) - 1
        for index, element in enumerate(self.elements):
            needs = element.identifier.requires_fnc1 and index != last
            parts.append(element.concatenated(separator=needs))
        return "".join(parts)

    def encoded_length(self) -> int:
        """Characters in the encoded form. This is what bounds a symbol size."""
        return len(self.concatenated())

    def __str__(self) -> str:
        return self.bracketed()


def _check_charset(identifier: ApplicationIdentifier, value: str, offset: int) -> None:
    if identifier.charset is CharacterSet.NUMERIC:
        if not value.isdigit():
            raise Gs1Error(
                "element data must be numeric",
                context={"ai": identifier.ai, "value": value, "offset": offset},
            )
        return
    bad = sorted({character for character in value if character not in CSET82})
    if bad:
        raise Gs1Error(
            "element data uses characters outside GS1 character set 82",
            context={"ai": identifier.ai, "value": value, "illegal": bad, "offset": offset},
        )


def _check_length(identifier: ApplicationIdentifier, value: str, offset: int) -> None:
    if len(value) < identifier.min_length or len(value) > identifier.max_length:
        expected = (
            str(identifier.min_length)
            if identifier.is_fixed_length
            else f"{identifier.min_length} to {identifier.max_length}"
        )
        raise Gs1Error(
            "element data is the wrong length",
            context={
                "ai": identifier.ai,
                "value": value,
                "length": len(value),
                "expected": expected,
                "offset": offset,
            },
        )


def parse_bracketed(text: str) -> ElementString:
    """Parse the ``(01)...(21)...`` form.

    :raises Gs1Error: on an unknown AI, data of the wrong length or shape, or
        text before the first bracket.
    """
    if not text:
        raise Gs1Error("element string is empty")
    matches = list(BRACKETED.finditer(text))
    if not matches:
        raise Gs1Error("element string has no bracketed AI", context={"value": text})
    if matches[0].start() != 0:
        raise Gs1Error(
            "element string has text before its first AI",
            context={"value": text, "leading": text[: matches[0].start()]},
        )

    elements: list[Element] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        identifier = resolve(match.group(1))
        value = text[match.end() : end]
        _check_length(identifier, value, match.start())
        _check_charset(identifier, value, match.start())
        elements.append(Element(identifier=identifier, value=value, offset=match.start()))
    return ElementString(tuple(elements))


def parse_concatenated(text: str) -> ElementString:
    """Parse the encoded form, honouring FNC1 separators.

    :raises Gs1Error: when an AI at the cursor is not recognised, a fixed length
        element runs off the end, or a variable length element is not
        terminated where it should be.
    """
    if not text:
        raise Gs1Error("element string is empty")

    elements: list[Element] = []
    cursor = 0
    while cursor < len(text):
        if text[cursor] == FNC1:
            # A separator immediately after another one, or at the very start.
            raise Gs1Error(
                "element string has a separator where an AI was expected",
                context={"value": text, "offset": cursor},
            )
        ai = longest_prefix(text[cursor:])
        if ai is None:
            raise Gs1Error(
                "element string has an unrecognised application identifier",
                context={"value": text, "offset": cursor, "at": text[cursor : cursor + 4]},
            )
        identifier = resolve(ai)
        start = cursor + len(ai)

        if identifier.is_fixed_length:
            end = start + identifier.max_length
            if end > len(text):
                raise Gs1Error(
                    "fixed length element runs off the end of the string",
                    context={"ai": ai, "value": text, "offset": cursor},
                )
            value = text[start:end]
            cursor = end
            # A predefined length AI is never followed by a separator, but a
            # fixed length AI that is not predefined still is.
            if identifier.requires_fnc1 and cursor < len(text) and text[cursor] == FNC1:
                cursor += 1
        else:
            separator = text.find(FNC1, start)
            end = len(text) if separator == -1 else separator
            value = text[start:end]
            cursor = end + (0 if separator == -1 else 1)

        _check_length(identifier, value, cursor)
        _check_charset(identifier, value, cursor)
        elements.append(Element(identifier=identifier, value=value, offset=cursor))

    return ElementString(tuple(elements))


def parse(text: str) -> ElementString:
    """Parse either form, choosing by whether the text starts with a bracket."""
    return parse_bracketed(text) if text.startswith("(") else parse_concatenated(text)


__all__ = [
    "BRACKETED",
    "CSET82",
    "Element",
    "ElementString",
    "parse",
    "parse_bracketed",
    "parse_concatenated",
]
