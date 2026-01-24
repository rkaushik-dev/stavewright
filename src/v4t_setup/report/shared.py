"""What more than one report builder needs.

Its own module rather than a private name in one of them, so that importing
the fleet builders does not drag in the apply executor to get at a five line
helper.
"""

from __future__ import annotations

MAX_TABLE_ROWS = 200
"""Beyond this a text table stops being readable. JSON keeps everything."""


def truncate(
    rows: list[list[str]], limit: int = MAX_TABLE_ROWS
) -> tuple[list[list[str]], str]:
    """Cut a table down, and say so in a note when it was cut."""
    if len(rows) <= limit:
        return rows, ""
    return rows[:limit], f"showing the first {limit} of {len(rows)} rows"


__all__ = ["MAX_TABLE_ROWS", "truncate"]
