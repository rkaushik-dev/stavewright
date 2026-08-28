"""MSI product versions and the upgrade table rules built on them.

An MSI ``ProductVersion`` is not a semantic version. It is three numbers with
hard field limits, and any fourth field is carried around but ignored the moment
anything compares two of them. Installer authors get caught by that constantly:
``1.0.0.5`` does not upgrade ``1.0.0.4``, it is the same version, and the
install turns into a repair that reinstalls nothing.

The S-Jet 600 package declares ``SecureCustomProperties`` of ``OLDPRODUCTS`` and
``AI_NEWERPRODUCTFOUND``, which is the two row shape Advanced Installer emits:
one row that detects and removes older products, one that only detects newer
ones so the install can refuse. Both are modelled here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import IntFlag
from functools import total_ordering

MAX_MAJOR = 255
MAX_MINOR = 255
MAX_BUILD = 65535

_VERSION_PATTERN = re.compile(r"^(\d{1,5})(?:\.(\d{1,5}))?(?:\.(\d{1,5}))?(?:\.(\d{1,10}))?$")


class UpgradeAttributes(IntFlag):
    """Bit values from the MSI ``Upgrade`` table ``Attributes`` column."""

    NONE = 0
    MIGRATE_FEATURES = 1
    ONLY_DETECT = 2
    IGNORE_REMOVE_FAILURE = 4
    VERSION_MIN_INCLUSIVE = 256
    VERSION_MAX_INCLUSIVE = 512
    LANGUAGES_EXCLUSIVE = 1024


@total_ordering
@dataclass(frozen=True, slots=True)
class MsiVersion:
    """A three field product version with an optional ignored fourth field.

    Ordering, equality and hashing all use the first three fields only. That is
    deliberately the same blindness the Windows Installer has, so a plan built
    here reaches the same conclusion the terminal will.
    """

    major: int
    minor: int = 0
    build: int = 0
    extra: int | None = None

    def __post_init__(self) -> None:
        for name, value, limit in (
            ("major", self.major, MAX_MAJOR),
            ("minor", self.minor, MAX_MINOR),
            ("build", self.build, MAX_BUILD),
        ):
            if not 0 <= value <= limit:
                raise ValueError(f"{name} field must be between 0 and {limit}, got {value}")
        if self.extra is not None and self.extra < 0:
            raise ValueError("fourth field must not be negative")

    @classmethod
    def parse(cls, text: str) -> MsiVersion:
        """Parse ``1``, ``1.0``, ``1.0.0`` or ``1.0.0.5``.

        :raises ValueError: on anything else, including a fifth field.
        """
        match = _VERSION_PATTERN.match(text.strip())
        if match is None:
            raise ValueError(f"not an MSI product version: {text!r}")
        major, minor, build, extra = match.groups()
        return cls(
            major=int(major),
            minor=int(minor or 0),
            build=int(build or 0),
            extra=int(extra) if extra is not None else None,
        )

    @property
    def comparable(self) -> tuple[int, int, int]:
        """The part the installer actually looks at."""
        return (self.major, self.minor, self.build)

    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}.{self.build}"
        return f"{base}.{self.extra}" if self.extra is not None else base

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, MsiVersion):
            return NotImplemented
        return self.comparable == other.comparable

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, MsiVersion):
            return NotImplemented
        return self.comparable < other.comparable

    def __hash__(self) -> int:
        return hash(self.comparable)

    def differs_only_in_fourth_field(self, other: MsiVersion) -> bool:
        """True when two versions look different but the installer sees one.

        This is the check that catches a build pipeline bumping only the
        revision and expecting a major upgrade.
        """
        return self == other and self.extra != other.extra


@dataclass(frozen=True, slots=True)
class UpgradeRow:
    """One row of the MSI ``Upgrade`` table.

    :param upgrade_code: the family this row detects, normally our own.
    :param version_min: lower bound, or None for open ended.
    :param version_max: upper bound, or None for open ended.
    :param attributes: inclusivity and removal behaviour.
    :param action_property: the property set when this row matches, such as
        ``OLDPRODUCTS``.
    :param language: comma separated language ids, or None for any.
    """

    upgrade_code: str
    action_property: str
    version_min: MsiVersion | None = None
    version_max: MsiVersion | None = None
    attributes: UpgradeAttributes = UpgradeAttributes.NONE
    language: str | None = None

    @property
    def detect_only(self) -> bool:
        """True when a match blocks the install rather than scheduling a removal."""
        return bool(self.attributes & UpgradeAttributes.ONLY_DETECT)

    @property
    def min_inclusive(self) -> bool:
        return bool(self.attributes & UpgradeAttributes.VERSION_MIN_INCLUSIVE)

    @property
    def max_inclusive(self) -> bool:
        return bool(self.attributes & UpgradeAttributes.VERSION_MAX_INCLUSIVE)

    def matches(self, upgrade_code: str, version: MsiVersion, language: int | None = None) -> bool:
        """True when an installed product falls in this row's window."""
        if upgrade_code.upper() != self.upgrade_code.upper():
            return False
        if self.version_min is not None:
            if version < self.version_min:
                return False
            if version == self.version_min and not self.min_inclusive:
                return False
        if self.version_max is not None:
            if version > self.version_max:
                return False
            if version == self.version_max and not self.max_inclusive:
                return False
        return self._language_matches(language)

    def _language_matches(self, language: int | None) -> bool:
        if self.language is None or language is None:
            return True
        listed = {int(part) for part in self.language.split(",") if part.strip()}
        exclusive = bool(self.attributes & UpgradeAttributes.LANGUAGES_EXCLUSIVE)
        return (language not in listed) if exclusive else (language in listed)


@dataclass(frozen=True, slots=True)
class InstalledProduct:
    """What a terminal already has from our own product family."""

    product_code: str
    upgrade_code: str
    version: MsiVersion
    language: int | None = None


@dataclass(frozen=True, slots=True)
class UpgradeDecision:
    """The outcome of running the upgrade table against a terminal."""

    removable: tuple[InstalledProduct, ...] = ()
    blocking: tuple[InstalledProduct, ...] = ()
    properties: tuple[str, ...] = ()

    @property
    def is_blocked(self) -> bool:
        """True when a newer product is present and the install must not proceed."""
        return bool(self.blocking)


def evaluate_upgrades(
    installed: list[InstalledProduct],
    rows: list[UpgradeRow],
) -> UpgradeDecision:
    """Run the upgrade table over what is already on the terminal.

    Products matching a detect only row land in :attr:`UpgradeDecision.blocking`
    and everything else that matched goes to
    :attr:`UpgradeDecision.removable`. A product that matches both kinds of row
    is treated as blocking, which is the conservative reading and matches what
    the installer does when ``AI_NEWERPRODUCTFOUND`` is set.
    """
    removable: list[InstalledProduct] = []
    blocking: list[InstalledProduct] = []
    properties: list[str] = []
    for product in installed:
        matched = [
            row
            for row in rows
            if row.matches(product.upgrade_code, product.version, product.language)
        ]
        if not matched:
            continue
        for row in matched:
            if row.action_property not in properties:
                properties.append(row.action_property)
        if any(row.detect_only for row in matched):
            blocking.append(product)
        else:
            removable.append(product)
    return UpgradeDecision(
        removable=tuple(removable),
        blocking=tuple(blocking),
        properties=tuple(properties),
    )


__all__ = [
    "MAX_BUILD",
    "MAX_MAJOR",
    "MAX_MINOR",
    "InstalledProduct",
    "MsiVersion",
    "UpgradeAttributes",
    "UpgradeDecision",
    "UpgradeRow",
    "evaluate_upgrades",
]
