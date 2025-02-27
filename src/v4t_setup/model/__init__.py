"""The data a commissioning run is planned from.

Objects in here describe a terminal and a bundle. They do not know how to reach
a machine, and they never touch the filesystem. That belongs to
:mod:`v4t_setup.target` and :mod:`v4t_setup.apply`.
"""

from __future__ import annotations

__all__: list[str] = []
