"""GS1 six digit dates, and the two things about them that surprise people.

The day may be ``00``. That is not a missing value, it means the last day of
the stated month, and a best before date of ``150800`` is the thirty first of
August. Treating it as an error rejects perfectly good data, and treating it as
day one marks product with a date thirty days early.

The century is not stored. It is worked out from a fifty one year window around
the current year, so ``49`` read in 2025 is 2049 and ``74`` read in 2025 is
1974. That means the same six digits mean different things in different years,
which is why every function here takes a reference year the caller can pin.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date

from stavewright.errors import Gs1Error

DATE_LENGTH = 6
DATE_TIME_LENGTH = 10
END_OF_MONTH_DAY = "00"

_MIN_MONTH = 1
_MAX_MONTH = 12


@dataclass(frozen=True, slots=True)
class Gs1Date:
    """A parsed six digit date.

    :param year: the full four digit year, after the century window is applied.
    :param month: 1 to 12.
    :param day: 1 to 31, or None when the field carried ``00``.
    """

    year: int
    month: int
    day: int | None

    @property
    def is_end_of_month(self) -> bool:
        """True when the day field was ``00``."""
        return self.day is None

    def to_date(self) -> date:
        """The concrete day this refers to.

        An end of month value resolves to the actual last day of that month,
        which is leap year aware.
        """
        if self.day is None:
            return date(self.year, self.month, calendar.monthrange(self.year, self.month)[1])
        return date(self.year, self.month, self.day)

    def format(self) -> str:
        """Back to six digits. Round trips :func:`parse_date`."""
        day = END_OF_MONTH_DAY if self.day is None else f"{self.day:02d}"
        return f"{self.year % 100:02d}{self.month:02d}{day}"

    def __str__(self) -> str:
        if self.day is None:
            return f"{self.year:04d}-{self.month:02d}-end"
        return self.to_date().isoformat()


def apply_century_window(two_digit_year: int, reference_year: int) -> int:
    """Expand a two digit year using the GS1 fifty one year rule.

    A difference of 51 to 99 puts the year in the previous century, and a
    difference of -99 to -50 puts it in the next one. Anything else stays in the
    reference year's century.

    :param two_digit_year: 0 to 99.
    :param reference_year: the four digit year to measure against.
    :raises Gs1Error: when *two_digit_year* is out of range.
    """
    if not 0 <= two_digit_year <= 99:
        raise Gs1Error(
            "two digit year is out of range",
            context={"value": two_digit_year},
        )
    century = reference_year - reference_year % 100
    difference = two_digit_year - reference_year % 100
    if 51 <= difference <= 99:
        return century - 100 + two_digit_year
    if -99 <= difference <= -50:
        return century + 100 + two_digit_year
    return century + two_digit_year


def parse_date(text: str, *, reference_year: int | None = None) -> Gs1Date:
    """Parse a six digit ``YYMMDD`` field.

    :param reference_year: pins the century window. Defaults to the current
        year, which makes the result depend on when it is called, so a plan
        that has to be reproducible passes this explicitly.
    :raises Gs1Error: on a wrong length, non digits, an impossible month, or a
        day that does not exist in that month.
    """
    if len(text) != DATE_LENGTH:
        raise Gs1Error(
            f"GS1 date must be {DATE_LENGTH} digits",
            context={"value": text, "length": len(text)},
        )
    if not text.isdigit():
        raise Gs1Error("GS1 date must be digits only", context={"value": text})

    reference = reference_year if reference_year is not None else date.today().year
    year = apply_century_window(int(text[:2]), reference)
    month = int(text[2:4])
    if not _MIN_MONTH <= month <= _MAX_MONTH:
        raise Gs1Error(
            "GS1 date has an impossible month",
            context={"value": text, "month": month},
        )

    raw_day = text[4:6]
    if raw_day == END_OF_MONTH_DAY:
        return Gs1Date(year=year, month=month, day=None)

    day = int(raw_day)
    last = calendar.monthrange(year, month)[1]
    if day > last:
        raise Gs1Error(
            "GS1 date has a day that month does not have",
            context={"value": text, "year": year, "month": month, "day": day, "last": last},
        )
    return Gs1Date(year=year, month=month, day=day)


def format_date(value: date, *, end_of_month: bool = False) -> str:
    """Render a date as six digits.

    :param end_of_month: write ``00`` in the day field rather than the day.
    """
    day = END_OF_MONTH_DAY if end_of_month else f"{value.day:02d}"
    return f"{value.year % 100:02d}{value.month:02d}{day}"


def parse_date_time(text: str, *, reference_year: int | None = None) -> tuple[Gs1Date, int, int]:
    """Parse a ten digit ``YYMMDDHHMM`` field.

    :returns: the date, the hour and the minute.
    :raises Gs1Error: on a wrong length or an out of range hour or minute.
    """
    if len(text) != DATE_TIME_LENGTH:
        raise Gs1Error(
            f"GS1 date and time must be {DATE_TIME_LENGTH} digits",
            context={"value": text, "length": len(text)},
        )
    parsed = parse_date(text[:DATE_LENGTH], reference_year=reference_year)
    hour = int(text[6:8])
    minute = int(text[8:10])
    if hour > 23:
        raise Gs1Error("hour is out of range", context={"value": text, "hour": hour})
    if minute > 59:
        raise Gs1Error("minute is out of range", context={"value": text, "minute": minute})
    return parsed, hour, minute


def is_expired(value: Gs1Date, *, today: date | None = None) -> bool:
    """True when *value* is strictly before *today*.

    An expiry marked with today's date has not expired, which matches how a
    warehouse reads it.
    """
    reference = today if today is not None else date.today()
    return value.to_date() < reference


__all__ = [
    "DATE_LENGTH",
    "DATE_TIME_LENGTH",
    "END_OF_MONTH_DAY",
    "Gs1Date",
    "apply_century_window",
    "format_date",
    "is_expired",
    "parse_date",
    "parse_date_time",
]
