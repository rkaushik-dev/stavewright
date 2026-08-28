"""The licence dongle a terminal runs behind.

An S-Jet 600 will start without a key and refuse to mark. What the key enables
is not one bit but a set: which machines it covers, how many heads it will
drive, whether the verification camera is licensed, and when it stops.

None of this talks to the HASP runtime. The toolkit reads what a key says out
of the terminal or off a build sheet and reasons about whether it covers the
plan. Actually querying the dongle belongs to the front end.
"""

from __future__ import annotations

__all__: list[str] = []
