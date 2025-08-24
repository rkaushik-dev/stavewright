"""What each barcode symbology can actually carry, and how wide it comes out.

The template says which symbology a block uses. Whether the data will fit is a
separate question and it is the one that bites, because a template that works
for a fourteen digit GTIN silently fails the day somebody adds a twenty
character serial to the same block.

Two limits matter on a marking terminal.

Capacity: how many characters of the right kind the symbology encodes at all.
EAN-13 takes exactly twelve digits plus a check digit and nothing else, so a
GS1 element string cannot go in one however short it is.

Width: how many modules wide the symbol comes out, which against the head's dot
pitch decides whether it fits the product. A GS1-128 grows about eleven modules
per character, so a fifty character element string is over five hundred modules
before the quiet zone.

The two shapes grow differently and the module keeps them apart. A linear
symbol grows smoothly, so many modules per character. A matrix symbol grows in
steps out of the specification's version table, and asking for one character
more can take it up a whole version. Modelling a matrix with a rate produces a
number that looks reasonable and is wrong.

The numbers here are the encodation rules, not a renderer. Nothing in this
module draws anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from v4t_setup.errors import Gs1Error

QUIET_ZONE_MODULES = 10
"""Modules of clear space each side. GS1 asks for ten on the linear symbols."""

# Square DataMatrix versions, smallest first, with their numeric capacity.
DATA_MATRIX_SIDES: tuple[tuple[int, int], ...] = (
    (10, 6), (12, 10), (14, 16), (16, 24), (18, 36), (20, 44),
    (22, 60), (24, 72), (26, 88), (32, 124), (36, 172), (40, 228),
    (44, 288), (48, 348), (52, 408), (64, 560), (72, 736), (80, 912),
    (88, 1152), (96, 1392), (104, 1632), (120, 2100), (132, 2608), (144, 3116),
)
"""(side in modules, capacity in numeric characters), from the DataMatrix spec."""

# QR versions 1 to 10 at L error correction. Side is 21 + 4 * (version - 1).
QR_SIDES: tuple[tuple[int, int], ...] = (
    (21, 41), (25, 77), (29, 127), (33, 187), (37, 255),
    (41, 322), (45, 370), (49, 461), (53, 552), (57, 652),
)
"""(side in modules, capacity in numeric characters) for QR versions 1 to 10."""


class Encoding(StrEnum):
    """What a symbology will accept."""

    NUMERIC = "numeric"
    ALPHANUMERIC = "alphanumeric"
    BINARY = "binary"


class Shape(StrEnum):
    LINEAR = "linear"
    MATRIX = "matrix"


def smallest_side(
    sides: tuple[tuple[int, int], ...],
    characters: int,
    name: str = "matrix",
) -> int:
    """The smallest version in *sides* that holds *characters*.

    :raises Gs1Error: when the table is empty or nothing in it is big enough.
    """
    if not sides:
        raise Gs1Error("matrix symbology has no version table", context={"symbology": name})
    for side, capacity in sides:
        if characters <= capacity:
            return side
    raise Gs1Error(
        "more data than this symbology will hold",
        context={"symbology": name, "characters": characters, "largest": sides[-1][1]},
    )


@dataclass(frozen=True, slots=True)
class Symbology:
    """One symbology and its limits."""

    name: str
    shape: Shape
    encoding: Encoding
    min_characters: int
    max_characters: int
    modules_per_character: float = 0
    fixed_modules: int = 0
    carries_ai: bool = False
    """True when the symbology can carry a GS1 element string with FNC1."""

    sides: tuple[tuple[int, int], ...] = ()
    """The version table, for a matrix symbology.

    A matrix grows in steps rather than smoothly, so its size comes out of this
    rather than from :attr:`modules_per_character`, which stays zero for them.
    """

    @property
    def is_fixed_length(self) -> bool:
        return self.min_characters == self.max_characters

    def accepts_length(self, length: int) -> bool:
        return self.min_characters <= length <= self.max_characters

    def accepts_characters(self, data: str) -> bool:
        """True when every character is in this symbology's set."""
        if self.encoding is Encoding.NUMERIC:
            return data.isdigit()
        if self.encoding is Encoding.ALPHANUMERIC:
            return all(32 <= ord(character) <= 126 for character in data)
        return True

    def modules_for(self, length: int) -> int:
        """How wide a symbol of *length* characters comes out, in modules.

        A matrix symbology returns the side of the square, because that is the
        number that has to fit across the head swath.

        :raises Gs1Error: when a matrix symbology has no version big enough.
        """
        if self.shape is Shape.MATRIX:
            return smallest_side(self.sides, length, self.name)
        return int(self.fixed_modules + round(self.modules_per_character * length))

    def width_modules(self, length: int, *, quiet_zone: bool = True) -> int:
        """Total width including the quiet zone, when there is one.

        Matrix symbologies get nothing added. Their quiet zone is one module
        and it is already inside the side count.
        """
        modules = self.modules_for(length)
        if quiet_zone and self.shape is Shape.LINEAR:
            modules += 2 * QUIET_ZONE_MODULES
        return modules

    def width_dots(self, length: int, module_dots: int = 2, *, quiet_zone: bool = True) -> int:
        """Width in printer dots.

        :param module_dots: dots per module. Two is the usual setting on these
            heads. One produces a symbol most scanners will not read at line
            speed.
        :raises Gs1Error: when *module_dots* is less than one.
        """
        if module_dots < 1:
            raise Gs1Error(
                "a module cannot be narrower than one dot",
                context={"module_dots": module_dots},
            )
        return self.width_modules(length, quiet_zone=quiet_zone) * module_dots

    def describe(self) -> str:
        length = (
            str(self.min_characters)
            if self.is_fixed_length
            else f"{self.min_characters}..{self.max_characters}"
        )
        return f"{self.name} ({self.shape.value}, {self.encoding.value}, {length})"


_TABLE: tuple[Symbology, ...] = (
    Symbology("EAN-13", Shape.LINEAR, Encoding.NUMERIC, 13, 13, fixed_modules=95),
    Symbology("EAN-13+2", Shape.LINEAR, Encoding.NUMERIC, 15, 15, fixed_modules=122),
    Symbology("EAN-13+5", Shape.LINEAR, Encoding.NUMERIC, 18, 18, fixed_modules=149),
    Symbology("EAN-8", Shape.LINEAR, Encoding.NUMERIC, 8, 8, fixed_modules=67),
    Symbology("UPC-A", Shape.LINEAR, Encoding.NUMERIC, 12, 12, fixed_modules=95),
    Symbology("UPC-E", Shape.LINEAR, Encoding.NUMERIC, 8, 8, fixed_modules=51),
    Symbology("ITF-14", Shape.LINEAR, Encoding.NUMERIC, 14, 14, 9, fixed_modules=17),
    Symbology("CODE128", Shape.LINEAR, Encoding.ALPHANUMERIC, 1, 80, 11, fixed_modules=35),
    Symbology(
        "GS1-128",
        Shape.LINEAR,
        Encoding.ALPHANUMERIC,
        1,
        48,
        11,
        fixed_modules=46,
        carries_ai=True,
    ),
    Symbology(
        "DataMatrix",
        Shape.MATRIX,
        Encoding.BINARY,
        1,
        DATA_MATRIX_SIDES[-1][1],
        sides=DATA_MATRIX_SIDES,
    ),
    Symbology(
        "GS1 DataMatrix",
        Shape.MATRIX,
        Encoding.BINARY,
        1,
        DATA_MATRIX_SIDES[-1][1],
        carries_ai=True,
        sides=DATA_MATRIX_SIDES,
    ),
    Symbology("QR", Shape.MATRIX, Encoding.BINARY, 1, QR_SIDES[-1][1], sides=QR_SIDES),
)

BY_NAME: dict[str, Symbology] = {entry.name: entry for entry in _TABLE}


def all_symbologies() -> tuple[Symbology, ...]:
    """Every symbology this build knows, in table order."""
    return _TABLE


def resolve(name: str) -> Symbology:
    """Look a symbology up by name.

    :raises Gs1Error: when this build does not know it.
    """
    try:
        return BY_NAME[name]
    except KeyError:
        raise Gs1Error(
            "unknown symbology",
            context={"name": name, "known": sorted(BY_NAME)},
        ) from None


def is_known(name: str) -> bool:
    return name in BY_NAME


def data_matrix_side(characters: int) -> int:
    """The smallest square DataMatrix that holds *characters* numeric digits.

    :raises Gs1Error: when nothing in the table is big enough.
    """
    return smallest_side(DATA_MATRIX_SIDES, characters, "DataMatrix")


def fits_head(
    symbology: Symbology,
    length: int,
    swath_dots: int,
    module_dots: int = 2,
) -> bool:
    """True when a symbol of *length* fits across the head's swath.

    Only meaningful for a matrix symbology, where the height equals the width.
    A linear symbol's height is set by the template and its width runs along
    the product rather than across the head, so it always fits.
    """
    if symbology.shape is not Shape.MATRIX:
        return True
    try:
        return symbology.modules_for(length) * module_dots <= swath_dots
    except Gs1Error:
        # More data than the symbology holds at all, so it certainly does not
        # fit. Returning False rather than raising keeps this usable from a
        # lint rule that is already reporting the capacity problem separately.
        return False


__all__ = [
    "BY_NAME",
    "DATA_MATRIX_SIDES",
    "QR_SIDES",
    "QUIET_ZONE_MODULES",
    "Encoding",
    "Shape",
    "Symbology",
    "all_symbologies",
    "data_matrix_side",
    "fits_head",
    "is_known",
    "resolve",
    "smallest_side",
]
