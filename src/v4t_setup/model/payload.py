"""What a bundle contains, before anything decides how to install it.

A payload item is the declarative half of a step. It says "the FTDI driver
package belongs on this terminal", and the planner works out that this means
staging an INF, a CAT and a SYS in that order and only on a machine that has a
controller to talk to.

Items carry their own applicability. That is what lets one bundle serve a Mini
and a four head S-Jet 600 without a second manifest: the bitstream item names
the machines it is for, the language packs name their LCIDs, and the OCX pair
names its architecture.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any

from v4t_setup.errors import ManifestError
from v4t_setup.model.profile import Architecture, TerminalModel, TerminalProfile
from v4t_setup.util.digest import Digest


class PayloadKind(StrEnum):
    """What sort of thing an item is."""

    FILE = "file"
    DIRECTORY = "directory"
    REGISTRY = "registry"
    COM_SERVER = "com-server"
    SXS_ASSEMBLY = "sxs-assembly"
    SXS_POLICY = "sxs-policy"
    DRIVER_PACKAGE = "driver-package"
    FPGA_IMAGE = "fpga-image"
    FONT = "font"
    SHORTCUT = "shortcut"
    TEMPLATE_SET = "template-set"
    LANGUAGE_PACK = "language-pack"
    REDISTRIBUTABLE = "redistributable"


NEEDS_SOURCE = frozenset(
    {
        PayloadKind.FILE,
        PayloadKind.COM_SERVER,
        PayloadKind.SXS_ASSEMBLY,
        PayloadKind.SXS_POLICY,
        PayloadKind.DRIVER_PACKAGE,
        PayloadKind.FPGA_IMAGE,
        PayloadKind.FONT,
        PayloadKind.TEMPLATE_SET,
        PayloadKind.LANGUAGE_PACK,
        PayloadKind.REDISTRIBUTABLE,
    }
)
"""Kinds that copy bytes and so must name where those bytes come from."""

NEEDS_DIRECTORY = frozenset(
    {
        PayloadKind.FILE,
        PayloadKind.DIRECTORY,
        PayloadKind.COM_SERVER,
        PayloadKind.FPGA_IMAGE,
        PayloadKind.TEMPLATE_SET,
        PayloadKind.LANGUAGE_PACK,
    }
)
"""Kinds that land somewhere the directory table can resolve."""


@dataclass(frozen=True, slots=True)
class PayloadItem:
    """One thing a bundle puts on a terminal."""

    item_id: str
    kind: PayloadKind
    name: str
    directory: str | None = None
    component: str | None = None
    source: PurePosixPath | None = None
    digest: Digest | None = None
    architecture: Architecture | None = None
    machines: frozenset[TerminalModel] = frozenset()
    language_ids: frozenset[int] = frozenset()
    provides: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.item_id:
            raise ManifestError("payload item id must not be empty")
        if not self.name:
            raise ManifestError("payload item must have a name", context={"item": self.item_id})
        if self.kind in NEEDS_SOURCE and self.source is None:
            raise ManifestError(
                "payload item of this kind must name a source",
                context={"item": self.item_id, "kind": self.kind.value},
            )
        if self.kind in NEEDS_DIRECTORY and not self.directory:
            raise ManifestError(
                "payload item of this kind must name a directory",
                context={"item": self.item_id, "kind": self.kind.value},
            )

    @property
    def is_universal(self) -> bool:
        """True when the item goes onto every terminal a bundle targets."""
        return (
            self.architecture is None
            and not self.machines
            and not self.language_ids
        )

    def applies_to(self, profile: TerminalProfile) -> bool:
        """True when this item belongs on *profile*.

        An empty constraint means "any". A machine list that does not include
        the profile's machine, an architecture that does not match, or a
        language set the profile did not ask for all exclude the item.
        """
        return self.excluded_because(profile) is None

    def excluded_because(self, profile: TerminalProfile) -> str | None:
        """Why this item does not apply, for a plan dump. None when it does."""
        if self.architecture is not None and self.architecture is not profile.architecture:
            return (
                f"item is {self.architecture.value} and "
                f"the terminal is {profile.architecture.value}"
            )
        if self.machines and profile.machine not in self.machines:
            wanted = ", ".join(sorted(machine.value for machine in self.machines))
            return f"item is only for {wanted}"
        if self.language_ids and not (self.language_ids & set(profile.language_ids)):
            wanted = ", ".join(str(lcid) for lcid in sorted(self.language_ids))
            return f"item is for language {wanted} which this terminal does not install"
        return None

    def describe(self) -> str:
        """One line for a manifest listing."""
        where = f" into {self.directory}" if self.directory else ""
        return f"{self.kind.value} {self.name}{where}"


def select(items: list[PayloadItem], profile: TerminalProfile) -> list[PayloadItem]:
    """The items that apply to *profile*, in the order they were given."""
    return [item for item in items if item.applies_to(profile)]


def rejected(items: list[PayloadItem], profile: TerminalProfile) -> dict[str, str]:
    """Item ids that do not apply, mapped to why."""
    result: dict[str, str] = {}
    for item in items:
        reason = item.excluded_because(profile)
        if reason is not None:
            result[item.item_id] = reason
    return result


def by_kind(items: list[PayloadItem]) -> dict[str, list[PayloadItem]]:
    """Group items by kind, each group keeping the caller's order."""
    grouped: dict[str, list[PayloadItem]] = {}
    for item in items:
        grouped.setdefault(item.kind.value, []).append(item)
    return dict(sorted(grouped.items()))


def duplicate_ids(items: list[PayloadItem]) -> list[str]:
    """Item ids that appear more than once, sorted."""
    seen: set[str] = set()
    repeated: set[str] = set()
    for item in items:
        if item.item_id in seen:
            repeated.add(item.item_id)
        seen.add(item.item_id)
    return sorted(repeated)


__all__ = [
    "NEEDS_DIRECTORY",
    "NEEDS_SOURCE",
    "PayloadItem",
    "PayloadKind",
    "by_kind",
    "duplicate_ids",
    "rejected",
    "select",
]
