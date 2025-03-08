"""MSI components and the feature tree that selects them.

A component is the unit the installer installs or does not install. It has a
GUID that must never change, a directory it lands in, a key path that decides
whether it is present, and an attribute word carrying the flags that matter
most on a terminal: whether the key path is a registry value, whether the
component is 64 bit, and whether it is permanent and so survives an uninstall.

Features sit above components and are what an operator ticks in the wizard. A
feature installs when its level is above zero and no higher than the current
``INSTALLLEVEL``, and a child feature with the follow parent flag is dragged
along by its parent regardless of its own level.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import IntFlag

from v4t_setup.errors import ManifestError

GUID_PATTERN = re.compile(
    r"^\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}$"
)

DEFAULT_INSTALL_LEVEL = 100
"""What the S-Jet 600 package sets ``INSTALLLEVEL`` to for a typical install."""


class ComponentAttributes(IntFlag):
    """Bit values from the MSI ``Component`` table ``Attributes`` column."""

    LOCAL_ONLY = 0
    SOURCE_ONLY = 1
    OPTIONAL = 2
    REGISTRY_KEY_PATH = 4
    SHARED_DLL_REF_COUNT = 8
    PERMANENT = 16
    ODBC_DATA_SOURCE = 32
    TRANSITIVE = 64
    NEVER_OVERWRITE = 128
    SIXTY_FOUR_BIT = 256
    DISABLE_REGISTRY_REFLECTION = 512


class FeatureAttributes(IntFlag):
    """Bit values from the MSI ``Feature`` table ``Attributes`` column."""

    FAVOR_LOCAL = 0
    FAVOR_SOURCE = 1
    FOLLOW_PARENT = 2
    FAVOR_ADVERTISE = 4
    DISALLOW_ADVERTISE = 8
    UI_DISALLOW_ABSENT = 16
    NO_UNSUPPORTED_ADVERTISE = 32


@dataclass(frozen=True, slots=True)
class Component:
    """One row of the MSI ``Component`` table."""

    component: str
    component_id: str
    directory: str
    attributes: ComponentAttributes = ComponentAttributes.LOCAL_ONLY
    key_path: str | None = None
    condition: str | None = None

    def __post_init__(self) -> None:
        if not self.component:
            raise ManifestError("component key must not be empty")
        if not GUID_PATTERN.match(self.component_id):
            raise ManifestError(
                "component id is not a braced GUID",
                context={"component": self.component, "component_id": self.component_id},
            )
        if self.is_registry_key_path and self.key_path is None:
            raise ManifestError(
                "component claims a registry key path but names none",
                context={"component": self.component},
            )

    @property
    def is_registry_key_path(self) -> bool:
        """True when the key path names a registry row rather than a file."""
        return bool(self.attributes & ComponentAttributes.REGISTRY_KEY_PATH)

    @property
    def is_64bit(self) -> bool:
        """True for a native component, which is not subject to WOW64 redirection."""
        return bool(self.attributes & ComponentAttributes.SIXTY_FOUR_BIT)

    @property
    def is_permanent(self) -> bool:
        """True when uninstall leaves it behind. The VC80 assemblies are like this."""
        return bool(self.attributes & ComponentAttributes.PERMANENT)

    @property
    def is_transitive(self) -> bool:
        """True when the condition is re-evaluated on every repair, not just install."""
        return bool(self.attributes & ComponentAttributes.TRANSITIVE)

    @property
    def is_shared(self) -> bool:
        """True when the shared DLL reference count is maintained."""
        return bool(self.attributes & ComponentAttributes.SHARED_DLL_REF_COUNT)

    @property
    def never_overwrite(self) -> bool:
        """True when an existing key path stops the component being reinstalled."""
        return bool(self.attributes & ComponentAttributes.NEVER_OVERWRITE)

    @property
    def normalised_id(self) -> str:
        """The GUID upper cased, for comparing against what a terminal reports."""
        return self.component_id.upper()


@dataclass(frozen=True, slots=True)
class Feature:
    """One row of the MSI ``Feature`` table."""

    feature: str
    title: str | None = None
    parent: str | None = None
    level: int = 1
    directory: str | None = None
    attributes: FeatureAttributes = FeatureAttributes.FAVOR_LOCAL
    display: int = 0
    components: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.feature:
            raise ManifestError("feature key must not be empty")
        if self.level < 0:
            raise ManifestError(
                "feature level must not be negative",
                context={"feature": self.feature, "level": self.level},
            )

    @property
    def follows_parent(self) -> bool:
        """True when the feature installs whenever its parent does."""
        return bool(self.attributes & FeatureAttributes.FOLLOW_PARENT)

    @property
    def is_hidden(self) -> bool:
        """True when ``Display`` is zero, which keeps it out of the wizard tree."""
        return self.display == 0

    def is_selected_at(self, install_level: int) -> bool:
        """True when this feature's own level puts it in the install."""
        return 0 < self.level <= install_level


@dataclass
class FeatureTree:
    """The feature table with the parent links resolved."""

    features: dict[str, Feature] = field(default_factory=dict)

    @classmethod
    def from_features(cls, features: list[Feature]) -> FeatureTree:
        """Build a tree, refusing duplicates and dangling parents.

        :raises ManifestError: on a repeated key or a parent that is not present.
        """
        table: dict[str, Feature] = {}
        for feature in features:
            if feature.feature in table:
                raise ManifestError(
                    "feature key appears twice",
                    context={"feature": feature.feature},
                )
            table[feature.feature] = feature
        for feature in table.values():
            if feature.parent is not None and feature.parent not in table:
                raise ManifestError(
                    "feature parent is not in the table",
                    context={"feature": feature.feature, "parent": feature.parent},
                )
        return cls(features=table)

    def __contains__(self, feature: str) -> bool:
        return feature in self.features

    def __len__(self) -> int:
        return len(self.features)

    def __getitem__(self, feature: str) -> Feature:
        try:
            return self.features[feature]
        except KeyError:
            raise ManifestError(
                "no such feature",
                context={"feature": feature},
            ) from None

    def roots(self) -> list[str]:
        """Top level features, sorted by display order then key."""
        rooted = [f for f in self.features.values() if f.parent is None]
        return [f.feature for f in sorted(rooted, key=lambda f: (f.display, f.feature))]

    def children_of(self, feature: str) -> list[str]:
        """Immediate children, sorted by display order then key."""
        kids = [f for f in self.features.values() if f.parent == feature]
        return [f.feature for f in sorted(kids, key=lambda f: (f.display, f.feature))]

    def ancestors_of(self, feature: str) -> list[str]:
        """Parent chain from the immediate parent up to the root."""
        chain: list[str] = []
        seen: set[str] = {feature}
        current = self[feature].parent
        while current is not None:
            if current in seen:
                raise ManifestError(
                    "feature parent chain loops",
                    context={"feature": feature, "at": current},
                )
            seen.add(current)
            chain.append(current)
            current = self[current].parent
        return chain

    def selected(self, install_level: int = DEFAULT_INSTALL_LEVEL) -> set[str]:
        """Which features install at *install_level*.

        A feature is in when its own level qualifies, or when it carries the
        follow parent flag and its parent is in. The pass repeats until nothing
        changes, so a chain of follow parent children all come along together.
        """
        chosen = {
            key for key, feature in self.features.items() if feature.is_selected_at(install_level)
        }
        while True:
            added = {
                key
                for key, feature in self.features.items()
                if key not in chosen and feature.follows_parent and feature.parent in chosen
            }
            if not added:
                return chosen
            chosen |= added

    def components_for(self, install_level: int = DEFAULT_INSTALL_LEVEL) -> set[str]:
        """Every component key reached by the features selected at *install_level*."""
        keys: set[str] = set()
        for feature in self.selected(install_level):
            keys.update(self[feature].components)
        return keys

    def orphan_components(self, components: list[Component]) -> list[str]:
        """Component keys that no feature references.

        An orphan never installs. Advanced Installer produces them when a file
        is removed from a feature but its component row is left behind, and the
        only symptom is a missing file on the terminal.
        """
        referenced: set[str] = set()
        for feature in self.features.values():
            referenced.update(feature.components)
        return sorted(c.component for c in components if c.component not in referenced)


__all__ = [
    "DEFAULT_INSTALL_LEVEL",
    "GUID_PATTERN",
    "Component",
    "ComponentAttributes",
    "Feature",
    "FeatureAttributes",
    "FeatureTree",
]
