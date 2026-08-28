"""The Application Identifiers this product knows how to mark.

Not the whole GS1 General Specifications. The set here is what the S-Jet 600
templates actually carry plus the neighbours an operator is likely to reach
for, and the table is explicit rather than generated so that adding one is a
deliberate act with a review attached.

Two properties of an AI drive everything downstream. Whether its data part is a
fixed length, and whether its length is *predefined*, which is a different
question. A predefined length AI is one of a short list whose length every
decoder knows without being told, so no FNC1 separator follows it. AI 21 has a
maximum length rather than a fixed one and so always needs the separator; AI 15
is exactly six digits and never does. Get that backwards and the scanner reads
the batch code as part of the serial.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from stavewright.errors import Gs1Error

PREDEFINED_LENGTH_PREFIXES = frozenset(
    {
        "00", "01", "02", "03", "04",
        "11", "12", "13", "14", "15", "16", "17", "18", "19",
        "20",
        "31", "32", "33", "34", "35", "36",
        "41",
    }
)
"""AI prefixes whose element strings never need an FNC1 after them.

Straight out of the General Specifications figure. Membership is decided by the
first two digits of the AI, so 3103 is predefined because 31 is.
"""

FNC1 = "\x1d"
"""The separator character, GS in ASCII, that terminates a variable length AI."""


class CharacterSet(StrEnum):
    """Which characters the data part may contain."""

    NUMERIC = "N"
    ALPHANUMERIC = "AN"


class DataKind(StrEnum):
    """How the data part should be interpreted once it has been extracted."""

    IDENTIFIER = "identifier"
    TEXT = "text"
    DATE = "date"
    DATE_TIME = "date-time"
    MEASURE = "measure"
    COUNT = "count"


@dataclass(frozen=True, slots=True)
class ApplicationIdentifier:
    """One row of the AI table."""

    ai: str
    title: str
    data_title: str
    min_length: int
    max_length: int
    charset: CharacterSet = CharacterSet.NUMERIC
    kind: DataKind = DataKind.IDENTIFIER
    has_decimal_indicator: bool = False
    check_digit: bool = False

    @property
    def is_fixed_length(self) -> bool:
        """True when the data part is always the same number of characters."""
        return self.min_length == self.max_length

    @property
    def has_predefined_length(self) -> bool:
        """True when no FNC1 separator follows this AI's element string."""
        return self.ai[:2] in PREDEFINED_LENGTH_PREFIXES

    @property
    def requires_fnc1(self) -> bool:
        """True when a separator is needed unless this is the last element."""
        return not self.has_predefined_length

    @property
    def family(self) -> str:
        """The AI with any decimal indicator digit removed."""
        return self.ai[:-1] if self.has_decimal_indicator else self.ai

    def describe(self) -> str:
        """One line for a lint message."""
        length = (
            str(self.min_length)
            if self.is_fixed_length
            else f"{self.min_length}..{self.max_length}"
        )
        return f"({self.ai}) {self.data_title} {self.charset.value}{length}"


def _fixed(
    ai: str,
    title: str,
    data_title: str,
    length: int,
    charset: CharacterSet = CharacterSet.NUMERIC,
    kind: DataKind = DataKind.IDENTIFIER,
    check_digit: bool = False,
) -> ApplicationIdentifier:
    return ApplicationIdentifier(
        ai=ai,
        title=title,
        data_title=data_title,
        min_length=length,
        max_length=length,
        charset=charset,
        kind=kind,
        check_digit=check_digit,
    )


def _variable(
    ai: str,
    title: str,
    data_title: str,
    max_length: int,
    charset: CharacterSet = CharacterSet.ALPHANUMERIC,
    kind: DataKind = DataKind.TEXT,
    min_length: int = 1,
) -> ApplicationIdentifier:
    return ApplicationIdentifier(
        ai=ai,
        title=title,
        data_title=data_title,
        min_length=min_length,
        max_length=max_length,
        charset=charset,
        kind=kind,
    )


_TABLE: tuple[ApplicationIdentifier, ...] = (
    _fixed("00", "Serial Shipping Container Code", "SSCC", 18, check_digit=True),
    _fixed("01", "Global Trade Item Number", "GTIN", 14, check_digit=True),
    _fixed("02", "GTIN of contained trade items", "CONTENT", 14, check_digit=True),
    _variable("10", "Batch or lot number", "BATCH/LOT", 20),
    _fixed("11", "Production date", "PROD DATE", 6, kind=DataKind.DATE),
    _fixed("12", "Due date", "DUE DATE", 6, kind=DataKind.DATE),
    _fixed("13", "Packaging date", "PACK DATE", 6, kind=DataKind.DATE),
    _fixed("15", "Best before date", "BEST BEFORE", 6, kind=DataKind.DATE),
    _fixed("16", "Sell by date", "SELL BY", 6, kind=DataKind.DATE),
    _fixed("17", "Expiration date", "USE BY OR EXPIRY", 6, kind=DataKind.DATE),
    _fixed("20", "Internal product variant", "VARIANT", 2),
    _variable("21", "Serial number", "SERIAL", 20),
    _variable("22", "Consumer product variant", "CPV", 20),
    _variable("240", "Additional item identification", "ADDITIONAL ID", 30),
    _variable("241", "Customer part number", "CUST. PART No.", 30),
    _variable("30", "Variable count of items", "VAR. COUNT", 8, CharacterSet.NUMERIC,
              DataKind.COUNT),
    ApplicationIdentifier(
        ai="3103",
        title="Net weight, kilograms",
        data_title="NET WEIGHT (kg)",
        min_length=6,
        max_length=6,
        charset=CharacterSet.NUMERIC,
        kind=DataKind.MEASURE,
        has_decimal_indicator=True,
    ),
    ApplicationIdentifier(
        ai="3203",
        title="Net weight, pounds",
        data_title="NET WEIGHT (lb)",
        min_length=6,
        max_length=6,
        charset=CharacterSet.NUMERIC,
        kind=DataKind.MEASURE,
        has_decimal_indicator=True,
    ),
    _variable("37", "Count of trade items", "COUNT", 8, CharacterSet.NUMERIC, DataKind.COUNT),
    _variable("400", "Customer purchase order number", "ORDER NUMBER", 30),
    _fixed("410", "Ship to global location number", "SHIP TO LOC", 13, check_digit=True),
    _fixed("414", "Physical location number", "LOC No.", 13, check_digit=True),
    _variable("8004", "Global Individual Asset Identifier", "GIAI", 30),
    _fixed("8018", "Global Service Relation Number", "GSRN", 18, check_digit=True),
    _variable("90", "Mutually agreed information", "INTERNAL", 30),
    _variable("91", "Company internal information", "INTERNAL", 90),
    _variable("99", "Company internal information", "INTERNAL", 90),
)

BY_AI: dict[str, ApplicationIdentifier] = {entry.ai: entry for entry in _TABLE}

MIN_AI_LENGTH = 2
MAX_AI_LENGTH = 4


def all_identifiers() -> tuple[ApplicationIdentifier, ...]:
    """Every AI the toolkit knows, in table order."""
    return _TABLE


def is_known(ai: str) -> bool:
    """True when *ai* has a row, decimal indicator forms included."""
    try:
        resolve(ai)
    except Gs1Error:
        return False
    return True


def resolve(ai: str) -> ApplicationIdentifier:
    """Look an AI up, allowing for the decimal indicator digit.

    ``3103`` is a row in its own right. ``3105`` is not, but it is the same AI
    with a different decimal position, so it resolves to the ``3103`` row and
    the caller reads the position off the last character itself.

    :raises Gs1Error: when nothing matches.
    """
    if not ai.isdigit():
        raise Gs1Error("application identifier must be digits", context={"ai": ai})
    if not MIN_AI_LENGTH <= len(ai) <= MAX_AI_LENGTH:
        raise Gs1Error(
            f"application identifier must be {MIN_AI_LENGTH} to {MAX_AI_LENGTH} digits",
            context={"ai": ai},
        )
    if ai in BY_AI:
        return BY_AI[ai]
    if len(ai) == 4:
        family = f"{ai[:3]}3"
        candidate = BY_AI.get(family)
        if candidate is not None and candidate.has_decimal_indicator:
            return candidate
    raise Gs1Error("unknown application identifier", context={"ai": ai})


def decimal_position(ai: str) -> int:
    """The implied number of decimal places for a measure AI.

    :raises Gs1Error: when *ai* does not carry a decimal indicator.
    """
    entry = resolve(ai)
    if not entry.has_decimal_indicator:
        raise Gs1Error(
            "application identifier has no decimal indicator",
            context={"ai": ai},
        )
    return int(ai[-1])


def longest_prefix(text: str) -> str | None:
    """The longest known AI at the start of *text*, or None.

    Element strings are not self delimiting at the AI, so parsing has to try
    four digits, then three, then two. Two digit AIs are tried last on purpose:
    ``3103`` starts with ``31`` and there is no AI ``31`` in this table, but the
    same shape does occur elsewhere in the specification.
    """
    for length in (4, 3, 2):
        candidate = text[:length]
        if len(candidate) == length and is_known(candidate):
            return candidate
    return None


__all__ = [
    "BY_AI",
    "FNC1",
    "MAX_AI_LENGTH",
    "MIN_AI_LENGTH",
    "PREDEFINED_LENGTH_PREFIXES",
    "ApplicationIdentifier",
    "CharacterSet",
    "DataKind",
    "all_identifiers",
    "decimal_position",
    "is_known",
    "longest_prefix",
    "resolve",
]
