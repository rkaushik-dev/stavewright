"""Small pieces with no dependency on the rest of the toolkit.

Nothing in here imports from :mod:`stavewright.model` or above. That rule keeps
the MSI primitives usable from the AIP reader, which runs before any model
object exists.
"""

from __future__ import annotations

__all__: list[str] = []
