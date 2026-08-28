"""The data a commissioning run is planned from.

Objects in here describe a terminal and a bundle. They do not know how to reach
a machine, and they never touch the filesystem. That belongs to
:mod:`stavewright.target` and :mod:`stavewright.apply`.
"""

from __future__ import annotations

__all__: list[str] = []
