"""What this installation of the toolkit is, and whether it will work.

The first question on every support call is which version of what is on the
laptop, and the answer used to be somebody reading a pip list down the phone.
This collects it, and checks the handful of things that make the difference
between a toolkit that runs and one that fails halfway through a command.

Nothing here reads a bundle, a profile or a terminal. It is about the tool.
"""

from __future__ import annotations

import importlib.metadata
import os
import platform
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from v4t_setup.__about__ import __version__
from v4t_setup.finding import Finding, Severity, reporter

RULE = "environment"
_finding = reporter(RULE)

MINIMUM_PYTHON = (3, 11)
"""What the code needs. StrEnum, the match statement and PEP 604 unions."""

REQUIRED_PACKAGES = ("typer", "pydantic", "cryptography", "structlog", "rich")
"""Runtime dependencies. A missing one is an ImportError halfway through a
command rather than at startup, because several are imported lazily."""

MIN_TEMP_BYTES = 512 * 1024 * 1024
"""Staging writes through a temporary file, so a full temp directory fails a
command that has already started changing things."""


@dataclass(frozen=True, slots=True)
class Environment:
    """Everything worth knowing about this installation."""

    toolkit_version: str
    python_version: str
    python_executable: str
    platform_name: str
    is_windows: bool
    packages: dict[str, str] = field(default_factory=dict)
    missing_packages: tuple[str, ...] = ()
    temp_free_bytes: int | None = None
    config_file: Path | None = None

    def describe(self) -> str:
        where = "on Windows" if self.is_windows else f"on {self.platform_name}"
        return f"v4t-setup {self.toolkit_version}, Python {self.python_version} {where}"

    def as_facts(self) -> dict[str, object]:
        return {
            "toolkit": self.toolkit_version,
            "python": self.python_version,
            "platform": self.platform_name,
            "executable": self.python_executable,
            "settings": self.config_file,
            **{f"package.{name}": version for name, version in sorted(self.packages.items())},
        }


def _version_of(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def collect(config_file: Path | None = None) -> Environment:
    """Read the environment. Touches nothing and changes nothing."""
    found: dict[str, str] = {}
    missing: list[str] = []
    for name in REQUIRED_PACKAGES:
        version = _version_of(name)
        if version is None:
            missing.append(name)
        else:
            found[name] = version

    temp = Path(os.environ.get("TMPDIR") or os.environ.get("TEMP") or "/tmp")
    free = shutil.disk_usage(temp).free if temp.is_dir() else None

    return Environment(
        toolkit_version=__version__,
        python_version=platform.python_version(),
        python_executable=sys.executable,
        platform_name=platform.platform(),
        is_windows=sys.platform == "win32",
        packages=found,
        missing_packages=tuple(missing),
        temp_free_bytes=free,
        config_file=config_file,
    )


def check_environment(environment: Environment) -> list[Finding]:
    """What is wrong with this installation.

    Being off Windows is a note rather than a warning. Every command that does
    not touch a terminal works there, and the whole test suite runs there on
    purpose, so a developer's laptop is not a broken installation.
    """
    findings: list[Finding] = []

    current = tuple(int(part) for part in environment.python_version.split(".")[:2])
    if current < MINIMUM_PYTHON:
        findings.append(
            _finding(
                Severity.ERROR,
                "this Python is older than the toolkit needs",
                "python",
                found=environment.python_version,
                wanted=".".join(str(part) for part in MINIMUM_PYTHON),
            )
        )

    for name in environment.missing_packages:
        findings.append(
            _finding(
                Severity.ERROR,
                "a package the toolkit imports is not installed",
                name,
            )
        )

    if not environment.is_windows:
        findings.append(
            _finding(
                Severity.INFO,
                "this is not Windows, so the commands that touch a terminal will refuse",
                "platform",
                platform=environment.platform_name,
            )
        )

    if environment.temp_free_bytes is not None and environment.temp_free_bytes < MIN_TEMP_BYTES:
        findings.append(
            _finding(
                Severity.WARNING,
                "the temporary directory is nearly full and staging writes through it",
                "temp",
                free_bytes=environment.temp_free_bytes,
                wanted_bytes=MIN_TEMP_BYTES,
            )
        )

    if environment.config_file is None:
        findings.append(
            _finding(
                Severity.WARNING,
                "no settings file was found, so every command needs its flags typed out",
                "settings",
            )
        )
    return findings


__all__ = [
    "MINIMUM_PYTHON",
    "MIN_TEMP_BYTES",
    "REQUIRED_PACKAGES",
    "RULE",
    "Environment",
    "check_environment",
    "collect",
]
