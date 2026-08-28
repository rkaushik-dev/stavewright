"""Where the toolkit gets the time from.

There were three ``utc_now`` functions, one in the plan module, one in the
manifest module and one inline in the journal. All three did the same thing and
all three meant a test that wanted to pin a timestamp had to patch a different
name.

Everything that stamps something now goes through :func:`now`, and a test that
needs the clock to stand still uses :func:`frozen`.

Timezone aware, always. A commissioning record with a naive timestamp is
ambiguous the moment a bundle built in one place is applied in another, and
this product ships to factories in a dozen countries.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

_override: datetime | None = None


def now() -> datetime:
    """The current time, in UTC, timezone aware.

    Returns whatever :func:`frozen` was given while a freeze is in effect.
    """
    return _override if _override is not None else datetime.now(UTC)


def stamp() -> str:
    """:func:`now` as an ISO 8601 string. What the journal writes."""
    return now().isoformat()


@contextmanager
def frozen(at: datetime) -> Iterator[datetime]:
    """Pin the clock for the duration of a block.

    :param at: what :func:`now` should return. Must be timezone aware, because
        a frozen naive time is the same ambiguity this module exists to avoid
        and a test that pins one proves nothing about production.
    :raises ValueError: when *at* is naive.
    """
    global _override
    if at.tzinfo is None:
        raise ValueError("frozen() needs a timezone aware datetime")
    previous = _override
    _override = at
    try:
        yield at
    finally:
        _override = previous


def is_frozen() -> bool:
    """True while a :func:`frozen` block is in effect."""
    return _override is not None


def utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    """Build a timezone aware UTC datetime. Shorthand for tests and fixtures."""
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


__all__ = ["frozen", "is_frozen", "now", "stamp", "utc"]
