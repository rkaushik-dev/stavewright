"""The options more than one command takes.

``--format`` and ``--output`` are on fifteen commands and ``--profile``,
``--terminal`` and ``--aip`` are on eight, each with its own copy of the help
text. Two of those copies had already drifted: one said "The terminal profile."
and another said "A terminal profile, so the head swath is checked too.", which
is fine, and a third said "The profile.", which is not.

A Typer option is a value describing a parameter, not a parameter, so one of
these can be the default for as many commands as want it. What a command must
not share is the variable it lands in, because that is a real parameter with a
real name and the name is what the reader sees.

Anything a single command takes stays with that command. A shared option that
only one caller uses is a worse version of a local one.
"""

from __future__ import annotations

import typer

FORMAT = typer.Option(
    "text", "--format", "-f", help="text, markdown, json, csv or junit."
)

OUTPUT = typer.Option(None, "--output", "-o", help="Write the report here.")

STRICT = typer.Option(False, "--strict", help="Fail on the warnings too.")
"""What a build server wants and a bench laptop does not.

A warning is something an engineer standing at the machine can look at and
decide about. Nobody is standing at a build server, so there it has to be a
failure or it is nothing. Only the exit code moves. The report is the same
either way, which matters because the two are read side by side."""

PROFILE = typer.Option(..., "--profile", "-p", help="The terminal profile.")

OPTIONAL_PROFILE = typer.Option(
    None, "--profile", "-p", help="The terminal profile, when there is one."
)

TERMINAL = typer.Option(None, "--terminal", "-t", help="Which terminal in the file.")

TERMINAL_FILTER = typer.Option(None, "--terminal", "-t", help="Only this terminal.")
"""``--terminal`` means two things and they are not the same thing.

On a command that takes a profile file it picks one terminal out of that file.
On ``run history`` it filters the journals. Sharing one description would make
one of the two wrong, so they are two options that happen to spell the flag the
same way.
"""

AIP = typer.Option(None, "--aip", help="The project file, for the directory table.")

TEMPLATES = typer.Option(
    None, "--templates", help="The template set, so its fonts are checked too."
)

JOURNALS = typer.Option(None, "--journals", help="Where the run journals are.")

PROFILE_DIR = typer.Option(
    None, "--profiles", "-p", help="Where the terminal profiles are. Defaults beside it."
)

BATCH = typer.Option(
    None, "--batch", "-b", help="Never put more than this many machines in one wave."
)

PENDING = typer.Option(
    False, "--pending", help="Only machines the journals say still need doing."
)

OFFLINE = typer.Option(False, "--offline", help="Check against an empty machine.")

__all__ = [
    "AIP",
    "BATCH",
    "FORMAT",
    "JOURNALS",
    "OFFLINE",
    "OPTIONAL_PROFILE",
    "OUTPUT",
    "PENDING",
    "PROFILE",
    "PROFILE_DIR",
    "STRICT",
    "TEMPLATES",
    "TERMINAL",
    "TERMINAL_FILTER",
]
