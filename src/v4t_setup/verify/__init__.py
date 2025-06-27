"""Checking that a terminal still is what the manifest says it should be.

Commissioning is the easy half. The hard half is six weeks later when a line
stops and somebody has to answer whether anything on that machine has moved
since it was signed off.

Reconcile answers that by comparing what is on the terminal against the plan
that was applied to it, and reporting the differences one at a time.
"""

from __future__ import annotations

__all__: list[str] = []
