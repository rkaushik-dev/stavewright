"""Turning a moment or a duration into a table cell.

Every builder was writing ``x.isoformat(sep=" ") if x else ""`` and a fresh
``total_seconds() / 60`` with its own number of decimal places. Fourteen of the
first and five of the second, and the decimal places were already inconsistent:
the schedule showed hours to one place and the plan showed minutes to one in
one table and none in another, in the same document.

None of these are clever. They are here so that changing how this toolkit
writes a timestamp is one edit rather than fourteen, and so that two tables in
one report cannot disagree about it.

Only the table cells go through here. :func:`v4t_setup.report.document.fact_text`
does the same job for the facts block, and the JSON renderer writes full ISO
because the thing reading it is a program.
"""

from __future__ import annotations

from datetime import datetime, timedelta

BLANK = ""
"""What a missing value renders as. Not "none", not "-", not "n/a"."""


def moment(value: datetime | None) -> str:
    """A timestamp for a person: a space rather than a T, blank when absent."""
    return value.isoformat(sep=" ") if value is not None else BLANK


def day(value: datetime | None) -> str:
    """Just the date, for a column where the time is noise."""
    return value.date().isoformat() if value is not None else BLANK


def minutes(span: timedelta | None, places: int = 1) -> str:
    """A duration in minutes."""
    if span is None:
        return BLANK
    return f"{span.total_seconds() / 60:.{places}f}"


def hours(span: timedelta | None, places: int = 1) -> str:
    """A duration in hours, which is how a maintenance window is talked about."""
    if span is None:
        return BLANK
    return f"{span.total_seconds() / 3600:.{places}f}"


def count(value: int | None) -> str:
    """A number, blank when nobody counted. Zero is a count and prints as one."""
    return BLANK if value is None else str(value)


__all__ = ["BLANK", "count", "day", "hours", "minutes", "moment"]
