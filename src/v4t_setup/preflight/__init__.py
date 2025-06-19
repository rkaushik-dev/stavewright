"""Everything that gets checked before a plan is allowed to touch a terminal.

Preflight is cheap and apply is not. A run that gets four steps in and then
finds there is no room on the disk has already registered two OCX controls and
put a driver in the store, and taking that back is work.

The checks here read the machine and the plan and answer one question each. An
error stops the run. A warning does not, but it is written into the report and
into the commissioning log, so when somebody asks in six weeks why that terminal
only speaks English there is an answer.
"""

from __future__ import annotations

__all__: list[str] = []
