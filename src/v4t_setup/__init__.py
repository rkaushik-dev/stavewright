"""V4T-Setup, the commissioning toolkit for S-Jet 600 marking terminals.

A terminal leaves the factory as a bare industrial PC. Commissioning it means
putting an FPGA bitstream on the print head controller, staging an FTDI driver
package, planting two side by side runtime assemblies with their publisher
policies, registering a pair of OCX controls into both the native and the WOW64
hive, dropping the GS1 template set the customer signed off, and writing the
product registry block. The order matters and most of it is not reversible by
hand once a line is running.

This package models all of that as data, plans it as a graph, and applies it
through a journal so a half finished bench can be rolled back.

Nothing here talks to a real machine on import. The Windows surface lives behind
:mod:`v4t_setup.target`, and every code path in the toolkit is exercised against
the in memory implementation of it.
"""

from __future__ import annotations

from v4t_setup.__about__ import (
    MANUFACTURER,
    PRODUCT_NAME,
    PRODUCT_VERSION,
    UPGRADE_CODE,
    __version__,
)

__all__ = [
    "MANUFACTURER",
    "PRODUCT_NAME",
    "PRODUCT_VERSION",
    "UPGRADE_CODE",
    "__version__",
]
