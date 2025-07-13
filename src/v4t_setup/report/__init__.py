"""Turning what the toolkit found into something somebody reads.

There is one document model and several renderers. A plan, a preflight run, a
drift report and a GS1 validation all become the same shape, so the console,
the JSON file that goes into the ticket, the CSV somebody opens in a
spreadsheet and the JUnit XML the build server reads all come from one place.

The alternative, which this replaced, was every subsystem growing its own
``print`` calls and its own idea of what a table looks like.
"""

from __future__ import annotations

__all__: list[str] = []
