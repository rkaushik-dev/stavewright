"""Applying a plan to a terminal, and undoing it when that goes wrong.

The apply layer is built around one assumption: the run will be interrupted.
Somebody will trip over the power lead, the bench will lock up, the operator
will hit the wrong thing. What matters is that the next person can tell exactly
how far it got and either finish it or take it back.

That is what the journal is for. Every step writes a record before it runs and
another when it finishes, the records are hash chained so a truncated or edited
journal is detectable, and a rollback is a replay of the journal backwards.
"""

from __future__ import annotations

__all__: list[str] = []
