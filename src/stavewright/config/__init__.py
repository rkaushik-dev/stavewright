"""Where the toolkit gets its settings from.

Four layers, lowest first: the built in defaults, a site file that the
commissioning group keeps under revision control, environment variables, and
whatever the operator typed on the command line.

The site file is the one that matters. A bench laptop that has been set up
correctly should need no flags at all, so that the difference between two runs
is the terminal and not what somebody remembered to type.
"""

from __future__ import annotations

__all__: list[str] = []
