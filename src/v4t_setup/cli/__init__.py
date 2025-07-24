"""The command line, which is how anybody actually uses this.

Commands are thin. Each one loads what it needs, calls into the library, hands
the result to a report builder and prints it. Anything that looks like logic in
here belongs somewhere else, because the command line is the one part that
cannot be tested without going through argument parsing.
"""

from __future__ import annotations

__all__: list[str] = []
