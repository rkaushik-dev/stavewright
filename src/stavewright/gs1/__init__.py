"""GS1 element strings, and the data a terminal marks them from.

A marking terminal exists to put a code onto product. If that code is malformed
the product is scrap, and nobody finds out until a scanner at the far end of the
line refuses to read a pallet.

The toolkit therefore validates the GS1 side of a commissioning bundle as
seriously as it validates the install side. The template set and its data file
are checked before either one reaches a terminal.
"""

from __future__ import annotations

__all__: list[str] = []
