"""Print templates.

A template is what the front end lays a job out with: where the barcode sits,
what font the human readable line uses, which field each block draws its data
from. They are stored as ``.spf`` files under
``Matricode/Quattro/Templates`` and the commissioning bundle carries whichever
set the customer signed off.

The toolkit does not write templates. It reads them well enough to check that
the set going onto a terminal is internally consistent and fits the print head
that terminal has.
"""

from __future__ import annotations

__all__: list[str] = []
