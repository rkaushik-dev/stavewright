"""GS1 modulo 10 check digits.

One algorithm covers GTIN-8, GTIN-12, GTIN-13, GTIN-14, SSCC, GLN, GSIN and
SSCC-18. Weight the data digits 3 and 1 alternately starting from the rightmost
one, sum, and the check digit is whatever takes that sum up to the next
multiple of ten.

Getting the alternation backwards is the classic mistake and it is invisible
half the time, because a sum that happens to be even produces the same answer
either way for some inputs. The tests here use real codes with known good check
digits rather than round tripping the implementation against itself.
"""

from __future__ import annotations

from v4t_setup.errors import Gs1Error

GTIN_LENGTHS = (8, 12, 13, 14)
"""The lengths a GTIN is allowed to be, check digit included."""

SSCC_LENGTH = 18
GLN_LENGTH = 13
GSIN_LENGTH = 17


def _digits_only(value: str, *, what: str) -> str:
    if not value:
        raise Gs1Error(f"{what} is empty")
    if not value.isdigit():
        raise Gs1Error(
            f"{what} must be digits only",
            context={"value": value},
        )
    return value


def mod10_check_digit(payload: str) -> int:
    """Compute the check digit for *payload*, which excludes it.

    :param payload: the data digits, without the check digit.
    :raises Gs1Error: when *payload* is empty or not all digits.
    """
    _digits_only(payload, what="check digit payload")
    total = 0
    # The rightmost payload digit carries weight 3, then they alternate leftward.
    for position, character in enumerate(reversed(payload)):
        weight = 3 if position % 2 == 0 else 1
        total += int(character) * weight
    return (10 - total % 10) % 10


def with_check_digit(payload: str) -> str:
    """Return *payload* with its check digit appended."""
    return f"{payload}{mod10_check_digit(payload)}"


def verify_mod10(value: str) -> bool:
    """True when the last digit of *value* is the correct check digit.

    :raises Gs1Error: when *value* is shorter than two digits or not numeric.
    """
    _digits_only(value, what="check digit candidate")
    if len(value) < 2:
        raise Gs1Error("value is too short to carry a check digit", context={"value": value})
    return mod10_check_digit(value[:-1]) == int(value[-1])


def require_mod10(value: str, *, what: str = "value") -> str:
    """Return *value* unchanged, or raise if its check digit is wrong.

    :raises Gs1Error: with the expected digit in the context, because that is
        what an operator needs in order to correct the data file.
    """
    if not verify_mod10(value):
        raise Gs1Error(
            f"{what} has an incorrect check digit",
            context={
                "value": value,
                "found": value[-1],
                "expected": str(mod10_check_digit(value[:-1])),
            },
        )
    return value


def normalise_gtin(value: str) -> str:
    """Pad a GTIN-8, -12 or -13 out to fourteen digits.

    GTIN-14 is the form AI 01 carries, so anything shorter is zero filled on
    the left. The check digit does not move and does not change.

    :raises Gs1Error: when *value* is not a legal GTIN length.
    """
    _digits_only(value, what="GTIN")
    if len(value) not in GTIN_LENGTHS:
        raise Gs1Error(
            "GTIN must be 8, 12, 13 or 14 digits",
            context={"value": value, "length": len(value)},
        )
    return value.rjust(14, "0")


def verify_gtin(value: str) -> bool:
    """True when *value* is a legal GTIN length and its check digit holds."""
    return verify_mod10(normalise_gtin(value))


def gtin_indicator(value: str) -> int:
    """The indicator digit of a GTIN-14.

    Zero means the code identifies a consumer unit. One through eight identify
    packaging levels, and nine marks a variable measure trade item, which is the
    one that changes how weight bearing AIs are read.
    """
    return int(normalise_gtin(value)[0])


def is_variable_measure(value: str) -> bool:
    """True when the indicator digit says this is a variable measure item."""
    return gtin_indicator(value) == 9


def verify_sscc(value: str) -> bool:
    """True when *value* is an eighteen digit SSCC with a good check digit."""
    _digits_only(value, what="SSCC")
    return len(value) == SSCC_LENGTH and verify_mod10(value)


def verify_gln(value: str) -> bool:
    """True when *value* is a thirteen digit GLN with a good check digit."""
    _digits_only(value, what="GLN")
    return len(value) == GLN_LENGTH and verify_mod10(value)


__all__ = [
    "GLN_LENGTH",
    "GSIN_LENGTH",
    "GTIN_LENGTHS",
    "SSCC_LENGTH",
    "gtin_indicator",
    "is_variable_measure",
    "mod10_check_digit",
    "normalise_gtin",
    "require_mod10",
    "verify_gln",
    "verify_gtin",
    "verify_mod10",
    "verify_sscc",
    "with_check_digit",
]
