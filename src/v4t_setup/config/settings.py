"""The settings object and the layers it is built from.

Every layer is a plain mapping and they are merged shallowly, one key at a
time. Deep merging was tried and taken out again: a site that sets two waived
checks and an operator who waives a third ended up with all three, which is
almost never what either of them meant. A key is either overridden or it is
not.

The environment prefix is ``V4T_``. A nested key is not addressable from the
environment on purpose. Anything that needs one is a site file decision, not
something to be set from a shell on a factory floor.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from v4t_setup.__about__ import BASE_INSTALL_LEVEL
from v4t_setup.errors import ConfigError
from v4t_setup.logging import DEFAULT_LEVEL, LEVELS

ENV_PREFIX = "V4T_"
CONFIG_FILE_NAME = "v4t-setup.toml"
CONFIG_SECTION = "v4t-setup"

BOOLEAN_TRUE = frozenset({"1", "true", "yes", "on"})
BOOLEAN_FALSE = frozenset({"0", "false", "no", "off"})


class Settings(BaseModel):
    """Everything the toolkit can be told before it is given a terminal."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    staging_root: Path = Path("_staging")
    """Where staged bundles are kept on the bench laptop."""

    journal_root: Path = Path("_journal")
    """Where apply journals are written. One file per run."""

    report_root: Path = Path("reports")
    """Where reports land when the operator does not name a file."""

    source_root: Path = Path()
    """Where a bundle's payload is read from when staging."""

    trusted_keys: Path | None = None
    """A directory of PEM public keys this bench will accept."""

    require_signature: bool = True
    """Refuse a bundle with no valid signature. Turn this off only on a bench."""

    log_level: str = DEFAULT_LEVEL
    log_file: Path | None = None

    install_level: int = Field(default=BASE_INSTALL_LEVEL, ge=1, le=32767)
    """``INSTALLLEVEL``. Raising it pulls the optional features in."""

    reference_year: int | None = Field(default=None, ge=1970, le=9999)
    """Pins the GS1 century window so a validation run is reproducible."""

    default_languages: tuple[str, ...] = ()
    """Languages a profile gets when it does not name any."""

    waived_checks: dict[str, str] = Field(default_factory=dict)
    """Preflight checks this site does not run, mapped to why."""

    rollback_on_failure: bool = True
    """Whether a failed apply undoes what it has done so far."""

    @field_validator("log_level")
    @classmethod
    def _known_level(cls, value: str) -> str:
        upper = value.upper()
        if upper not in LEVELS:
            raise ValueError(f"unknown log level {value!r}, expected one of {', '.join(LEVELS)}")
        return upper

    @field_validator("default_languages", mode="before")
    @classmethod
    def _split_languages(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return value

    @field_validator("waived_checks", mode="before")
    @classmethod
    def _waivers_are_strings(cls, value: object) -> object:
        if isinstance(value, Mapping):
            return {str(key): str(reason) for key, reason in value.items()}
        return value

    def journal_for(self, run_id: str) -> Path:
        """Where the journal for one run goes."""
        return self.journal_root / f"run-{run_id}.jsonl"

    def describe(self) -> str:
        """One line for a verbose header."""
        signing = "signature required" if self.require_signature else "signature not required"
        return f"staging {self.staging_root}, {signing}, install level {self.install_level}"


def _coerce(value: str) -> Any:
    lowered = value.strip().lower()
    if lowered in BOOLEAN_TRUE:
        return True
    if lowered in BOOLEAN_FALSE:
        return False
    if lowered.lstrip("-").isdigit():
        return int(value)
    return value


def from_environment(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Read the ``V4T_`` prefixed variables into a settings layer.

    Values are coerced: ``true`` and ``0`` become booleans, digits become
    integers, everything else stays a string and pydantic sorts it out.
    """
    source = environ if environ is not None else os.environ
    layer: dict[str, Any] = {}
    for name, value in source.items():
        if not name.startswith(ENV_PREFIX):
            continue
        key = name[len(ENV_PREFIX) :].lower()
        if key:
            layer[key] = _coerce(value)
    return layer


def read_config_file(path: Path) -> dict[str, Any]:
    """Read one TOML settings file.

    A file may put the settings at the top level or under a ``[v4t-setup]``
    table, so a shared project file can carry them beside other tools.

    :raises ConfigError: when the file cannot be read or is not valid TOML.
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ConfigError(
            "cannot read configuration file",
            context={"path": str(path), "detail": str(exc)},
        ) from exc
    try:
        payload = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(
            "configuration file is not valid TOML",
            context={"path": str(path), "detail": str(exc)},
        ) from exc
    section = payload.get(CONFIG_SECTION)
    if isinstance(section, dict):
        return dict(section)
    # No section, so the settings are at the top level and anything else in the
    # file belongs to another tool. Filtering on "is not a table" was the first
    # attempt and it silently dropped [waived_checks], which is a setting and a
    # table. Filter on the field names instead.
    known = set(Settings.model_fields)
    return {key: value for key, value in payload.items() if key in known}


def find_config_file(start: Path) -> Path | None:
    """Look for a settings file in *start* and then upwards.

    Stops at the filesystem root. A bench laptop keeps one at the top of the
    commissioning directory and every terminal folder under it inherits it.
    """
    current = start.resolve()
    for candidate in [current, *current.parents]:
        path = candidate / CONFIG_FILE_NAME
        if path.is_file():
            return path
    return None


def merge(*layers: Mapping[str, Any]) -> dict[str, Any]:
    """Merge settings layers, later layers winning, one key at a time.

    Shallow on purpose. See the module docstring.
    """
    merged: dict[str, Any] = {}
    for layer in layers:
        for key, value in layer.items():
            if value is not None:
                merged[key] = value
    return merged


def load_settings(
    *,
    files: list[Path] | None = None,
    environ: Mapping[str, str] | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> Settings:
    """Build a :class:`Settings` from the layers.

    :param files: settings files, lowest priority first.
    :param environ: the environment, or None to read the real one.
    :param overrides: what the operator typed. Highest priority. ``None``
        values are ignored so a CLI can pass every flag unconditionally.
    :raises ConfigError: when a file will not read, or the merged settings do
        not validate.
    """
    layers = [read_config_file(path) for path in (files or [])]
    layers.append(from_environment(environ))
    layers.append(dict(overrides or {}))
    merged = merge(*layers)
    try:
        return Settings(**merged)
    except ValidationError as exc:
        raise ConfigError(
            "settings are not valid",
            context={"detail": exc.errors(include_url=False)},
        ) from exc


__all__ = [
    "CONFIG_FILE_NAME",
    "CONFIG_SECTION",
    "ENV_PREFIX",
    "Settings",
    "find_config_file",
    "from_environment",
    "load_settings",
    "merge",
    "read_config_file",
]
