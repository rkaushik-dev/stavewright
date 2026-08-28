"""The machine a commissioning run acts on.

Everything above this package works against
:class:`stavewright.target.base.TargetSystem` and never imports ``winreg`` or
touches a path directly. There are two implementations: one that talks to a
real Windows box and one that keeps everything in a dictionary.

That is not for the sake of testing alone, although it does mean the whole
toolkit runs on any machine. It is also what makes a dry run honest. A dry run
is a real apply against the in memory target seeded from the real one, so the
same code path produces the same journal and the same report, and nothing is
skipped with an ``if dry_run`` in the middle of an action.
"""

from __future__ import annotations

__all__: list[str] = []
