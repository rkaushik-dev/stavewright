"""Building the ordered list of work a commissioning run performs.

A bundle is a set of things that have to end up on a terminal. A plan is those
things in an order that works, which is a different problem: the side by side
runtime assemblies have to be in place before anything binds to them, the C
runtime has to be registered before an OCX that links against it will register
at all, and the FPGA bitstream is pointless until the driver package that talks
to the controller is staged.

Ordering rules live in :mod:`v4t_setup.plan.rules` as separate objects so the
reason for each edge is written down next to the edge.
"""

from __future__ import annotations

__all__: list[str] = []
