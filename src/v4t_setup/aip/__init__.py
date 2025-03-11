"""Reading the Advanced Installer project that describes an S-Jet 600 image.

``setup.aip`` is XML, but it is not a document. It is a set of MSI tables with
one ``<COMPONENT cid="...">`` element per table and one ``<ROW>`` per row, and
the attribute names on a row are the column names of the table it belongs to.

Reading it is split in two on purpose. :mod:`v4t_setup.aip.reader` produces raw
rows and knows nothing about what they mean, and
:mod:`v4t_setup.aip.normalize` turns those rows into model objects. When
Advanced Installer changes its file format the first half moves and the second
half does not.
"""

from __future__ import annotations

__all__: list[str] = []
