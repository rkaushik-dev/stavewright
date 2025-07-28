"""Reading terminal profiles off the bench laptop.

One TOML file per terminal, kept under revision control by the commissioning
group, so that what went onto a machine is answerable from the repository
rather than from somebody's memory.

A file may hold one terminal at the top level or several under a ``[terminals]``
table. The second form is what a line uses, because six machines on the same
line share almost everything and having six files that differ in one field is
how they drift apart.

Shared values go at the top level and each terminal overrides what it needs::

    machine = "s-jet600"
    architecture = "x64"
    fpga_revision = "PCDC-1.4"
    languages = ["pl-PL"]

    [terminals.LINE-PC-04]
    head_count = 2

    [terminals.LINE-PC-05]
    head_count = 1
    hasp_serial = "HASP-0042"

TOML has no null, so a terminal that needs to drop an inherited value says so
with ``clear``::

    [terminals.LINE-PC-09]
    machine = "s-jet-mini"
    clear = ["fpga_revision"]

That is more typing than writing ``fpga_revision = false``, and it is worth it.
A false there is indistinguishable from somebody meaning it.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from v4t_setup.errors import ConfigError
from v4t_setup.model.profile import TerminalProfile

TERMINALS_TABLE = "terminals"
PROFILE_SUFFIX = ".toml"
CLEAR_KEY = "clear"


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ConfigError(
            "cannot read profile file",
            context={"path": str(path), "detail": str(exc)},
        ) from exc
    try:
        return tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(
            "profile file is not valid TOML",
            context={"path": str(path), "detail": str(exc)},
        ) from exc


def _build(payload: dict[str, Any], *, where: str) -> TerminalProfile:
    try:
        return TerminalProfile(**payload)
    except ValidationError as exc:
        raise ConfigError(
            "terminal profile is not valid",
            context={"where": where, "detail": exc.errors(include_url=False)},
        ) from exc


def parse_profiles(
    payload: dict[str, Any],
    *,
    where: str = "(in memory)",
) -> dict[str, TerminalProfile]:
    """Turn one parsed TOML document into profiles, keyed by terminal id.

    :raises ConfigError: when a profile does not validate, a terminal table is
        not a table, a single terminal file has no ``terminal_id``, or a
        ``clear`` list names something that is not a profile field.
    """
    terminals = payload.get(TERMINALS_TABLE)
    if terminals is None:
        if "terminal_id" not in payload:
            raise ConfigError(
                "profile file has no terminals and no terminal_id",
                context={"where": where},
            )
        profile = _build(payload, where=where)
        return {profile.terminal_id: profile}

    if not isinstance(terminals, dict):
        raise ConfigError(
            "the terminals entry is not a table",
            context={"where": where},
        )

    shared = {
        key: value
        for key, value in payload.items()
        if key != TERMINALS_TABLE and not isinstance(value, dict)
    }
    profiles: dict[str, TerminalProfile] = {}
    for terminal_id, overrides in terminals.items():
        if not isinstance(overrides, dict):
            raise ConfigError(
                "a terminal entry is not a table",
                context={"where": where, "terminal": terminal_id},
            )
        merged = {**shared, **overrides, "terminal_id": terminal_id}
        for name in _clear_list(merged.pop(CLEAR_KEY, ()), where, terminal_id):
            merged.pop(name, None)
        profiles[terminal_id] = _build(merged, where=f"{where}[{terminal_id}]")
    return profiles


def _clear_list(value: object, where: str, terminal_id: str) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list | tuple):
        raise ConfigError(
            "clear must be a list of field names",
            context={"where": where, "terminal": terminal_id},
        )
    names = [str(name) for name in value]
    known = set(TerminalProfile.model_fields)
    unknown = sorted(name for name in names if name not in known)
    if unknown:
        raise ConfigError(
            "clear names something that is not a profile field",
            context={"where": where, "terminal": terminal_id, "unknown": unknown},
        )
    return names


def read_profiles(path: Path) -> dict[str, TerminalProfile]:
    """Read every profile in one file.

    :raises ConfigError: when the file cannot be read or does not validate.
    """
    return parse_profiles(_read_toml(path), where=str(path))


def read_profile(path: Path, terminal_id: str | None = None) -> TerminalProfile:
    """Read one profile.

    :param terminal_id: which terminal, when the file holds several. A file
        with exactly one terminal does not need it.
    :raises ConfigError: when the terminal is not in the file, or the file
        holds several and none was named.
    """
    profiles = read_profiles(path)
    if terminal_id is not None:
        try:
            return profiles[terminal_id]
        except KeyError:
            raise ConfigError(
                "the profile file has no such terminal",
                context={"path": str(path), "terminal": terminal_id, "known": sorted(profiles)},
            ) from None
    if len(profiles) == 1:
        return next(iter(profiles.values()))
    raise ConfigError(
        "the profile file holds several terminals, name one",
        context={"path": str(path), "known": sorted(profiles)},
    )


def load_directory(root: Path) -> dict[str, TerminalProfile]:
    """Read every ``.toml`` in *root*, merging the terminals they declare.

    The settings file is skipped. A bench keeps ``v4t-setup.toml`` in the same
    directory as its profiles, and trying to read it as one fails with a
    message about missing terminals that tells the operator nothing.

    :raises ConfigError: when two files declare the same terminal, which means
        somebody has copied a file and forgotten to rename the table.
    """
    from v4t_setup.config.settings import CONFIG_FILE_NAME

    if not root.is_dir():
        raise ConfigError("profile directory does not exist", context={"path": str(root)})
    found: dict[str, TerminalProfile] = {}
    origin: dict[str, str] = {}
    for path in sorted(root.glob(f"*{PROFILE_SUFFIX}")):
        if path.name == CONFIG_FILE_NAME:
            continue
        for terminal_id, profile in read_profiles(path).items():
            if terminal_id in found:
                raise ConfigError(
                    "two profile files declare the same terminal",
                    context={
                        "terminal": terminal_id,
                        "first": origin[terminal_id],
                        "second": str(path),
                    },
                )
            found[terminal_id] = profile
            origin[terminal_id] = str(path)
    return found


def describe_profiles(profiles: dict[str, TerminalProfile]) -> list[str]:
    """One line per terminal, sorted, for a listing."""
    return [profiles[key].describe() for key in sorted(profiles)]


__all__ = [
    "PROFILE_SUFFIX",
    "TERMINALS_TABLE",
    "describe_profiles",
    "load_directory",
    "parse_profiles",
    "read_profile",
    "read_profiles",
]
