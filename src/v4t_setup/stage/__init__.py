"""Getting a bundle's payload onto the bench laptop and ready to install.

Staging sits between the release build and the terminal. It takes a manifest,
gathers the bytes each item names, checks them against the digests the release
engineer signed, and lays them out so an apply can read them without going back
to wherever they came from.

That last part matters more than it looks. A commissioning run happens on a
factory floor with no network. Everything it will need has to be on the laptop
before it walks onto the line, and the way to be sure of that is to stage it and
verify the staging tree while there is still a network to fix it from.
"""

from __future__ import annotations

__all__: list[str] = []
