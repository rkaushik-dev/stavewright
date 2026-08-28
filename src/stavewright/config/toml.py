"""Reading a TOML file and saying which one when it will not read.

Three modules were doing this, each with its own wording for the same two
failures, and the third copy went in without anybody noticing the first two.
The wording matters more than it looks: an operator on a bench at six in the
morning gets one line of output, and "cannot read profile file" against a path
is the difference between fixing it and phoning somebody.

So the message stays caller supplied. What is shared is the reading, the UTF-8
decode, and the guarantee that both failures arrive as a ConfigError carrying
the path.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from stavewright.errors import ConfigError

ENCODING = "utf-8"


def load_toml(path: Path, *, what: str) -> dict[str, Any]:
    """Read and parse one TOML file.

    :param what: what this file is to the caller, such as ``"profile file"``.
        It goes straight into both messages, so write it as a noun phrase that
        finishes "cannot read ..." and "... is not valid TOML".
    :raises ConfigError: when the file cannot be read, is not UTF-8, or is not
        valid TOML. The path is in the context either way.
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ConfigError(
            f"cannot read {what}",
            context={"path": str(path), "detail": str(exc)},
        ) from exc
    return parse_toml(raw, what=what, where=str(path))


def parse_toml(raw: bytes, *, what: str, where: str) -> dict[str, Any]:
    """Parse TOML that is already in memory.

    Split out from :func:`load_toml` for the case where the bytes came from
    somewhere that is not a file, such as a staged bundle.

    :raises ConfigError: when the bytes are not UTF-8 or not valid TOML.
    """
    try:
        return tomllib.loads(raw.decode(ENCODING))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(
            f"{what} is not valid TOML",
            context={"path": where, "detail": str(exc)},
        ) from exc


__all__ = ["ENCODING", "load_toml", "parse_toml"]
