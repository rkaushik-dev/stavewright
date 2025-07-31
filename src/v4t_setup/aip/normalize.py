"""Turning raw AIP rows into model objects.

This is where the project file stops being XML and starts being something a
plan can be built from. Three things happen that the reader deliberately did
not do.

Cells get typed. ``Attributes="257"`` becomes an :class:`UpgradeAttributes`
flag set, ``IsPseudoRoot="1"`` becomes a bool, and the space packed
``Components`` cell on a feature row becomes a tuple.

Formatted fields get expanded. The upgrade rows in this project are written as
``UpgradeCode="[|UpgradeCode]"`` and ``VersionMax="[|ProductVersion]"``, so
without expansion the upgrade table compares a terminal's installed version
against the literal text ``[|ProductVersion]`` and never matches anything.

Cross table references get checked. A feature naming a component that has no
row, or a file naming a component that has no row, is a project that will build
and then quietly not install something.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from v4t_setup.aip.reader import AipProject, AipRow
from v4t_setup.errors import AipParseError, ManifestError
from v4t_setup.model.component import (
    Component,
    ComponentAttributes,
    Feature,
    FeatureAttributes,
    FeatureTree,
)
from v4t_setup.model.directory import DirectoryRow, DirectoryTable
from v4t_setup.model.fileentry import FileEntry
from v4t_setup.model.registry import RegistryEntry, RegistryRoot
from v4t_setup.util.msiver import MsiVersion, UpgradeAttributes, UpgradeRow
from v4t_setup.util.tokens import expand


@dataclass
class NormalisedProject:
    """Everything the planner needs, taken out of one project file."""

    properties: dict[str, str] = field(default_factory=dict)
    directories: DirectoryTable = field(default_factory=DirectoryTable)
    components: list[Component] = field(default_factory=list)
    features: FeatureTree = field(default_factory=FeatureTree)
    files: list[FileEntry] = field(default_factory=list)
    registry: list[RegistryEntry] = field(default_factory=list)
    upgrades: list[UpgradeRow] = field(default_factory=list)

    @property
    def product_name(self) -> str:
        return self.properties.get("ProductName", "")

    @property
    def manufacturer(self) -> str:
        return self.properties.get("Manufacturer", "")

    @property
    def upgrade_code(self) -> str:
        return self.properties.get("UpgradeCode", "")

    @property
    def all_users(self) -> bool:
        """True when the package installs per machine."""
        return self.properties.get("ALLUSERS", "") == "1"

    @property
    def product_version(self) -> MsiVersion:
        """The declared product version.

        :raises ManifestError: when the property is missing or unparseable.
        """
        raw = self.properties.get("ProductVersion")
        if not raw:
            raise ManifestError("project declares no ProductVersion")
        try:
            return MsiVersion.parse(raw)
        except ValueError as exc:
            raise ManifestError(
                "ProductVersion is not an MSI version",
                context={"value": raw},
            ) from exc

    def component_keys(self) -> set[str]:
        return {component.component for component in self.components}

    def files_for(self, component: str) -> list[FileEntry]:
        """File rows belonging to *component*, sorted by key."""
        return sorted(
            (entry for entry in self.files if entry.component == component),
            key=lambda entry: entry.file,
        )

    def registry_for(self, component: str) -> list[RegistryEntry]:
        """Registry rows belonging to *component*, sorted by key."""
        return sorted(
            (entry for entry in self.registry if entry.component == component),
            key=lambda entry: entry.registry,
        )


def _directory_row(row: AipRow) -> DirectoryRow:
    return DirectoryRow.from_cells(
        directory=row.require("Directory"),
        default_dir=row.require("DefaultDir"),
        parent=row.get("Directory_Parent"),
        is_pseudo_root=row.get("IsPseudoRoot") == "1",
    )


def _component_row(row: AipRow) -> Component:
    return Component(
        component=row.require("Component"),
        component_id=row.require("ComponentId"),
        directory=row.require("Directory_"),
        attributes=ComponentAttributes(row.get_int("Attributes", 0) or 0),
        key_path=row.get("KeyPath"),
        condition=row.get("Condition") or None,
    )


def _feature_row(row: AipRow) -> Feature:
    return Feature(
        feature=row.require("Feature"),
        title=row.get("Title"),
        parent=row.get("Feature_Parent") or None,
        level=row.get_int("Level", 1) or 0,
        directory=row.get("Directory_") or None,
        attributes=FeatureAttributes(row.get_int("Attributes", 0) or 0),
        display=row.get_int("Display", 0) or 0,
        components=tuple(row.get_list("Components")),
    )


def _file_row(row: AipRow) -> FileEntry:
    return FileEntry.from_cells(
        file=row.require("File"),
        component=row.require("Component_"),
        file_name=row.require("FileName"),
        source_path=row.get("SourcePath"),
        attributes=row.get_int("Attributes", 0) or 0,
        self_reg=row.get_bool("SelfReg"),
        version=row.get("Version"),
        language=row.get("Language"),
        sequence=row.get_int("Sequence"),
    )


def _registry_row(row: AipRow) -> RegistryEntry:
    raw_root = row.get_int("Root", 0) or 0
    try:
        root = RegistryRoot(raw_root)
    except ValueError:
        raise AipParseError(
            "registry row has an unknown root",
            context={"registry": row.get("Registry"), "root": raw_root},
        ) from None
    return RegistryEntry(
        registry=row.require("Registry"),
        root=root,
        key=row.require("Key"),
        name=row.get("Name"),
        value=row.get("Value"),
        component=row.require("Component_"),
    )


def _upgrade_row(row: AipRow, properties: dict[str, str]) -> UpgradeRow:
    def field_value(column: str) -> str | None:
        raw = row.get(column)
        if raw is None or raw == "":
            return None
        expanded = expand(raw, properties)
        return expanded or None

    def version(column: str) -> MsiVersion | None:
        text = field_value(column)
        if text is None:
            return None
        try:
            return MsiVersion.parse(text)
        except ValueError as exc:
            raise AipParseError(
                "upgrade row carries a version that will not parse",
                context={"column": column, "value": text},
            ) from exc

    code = field_value("UpgradeCode")
    if code is None:
        raise AipParseError("upgrade row has no UpgradeCode")
    return UpgradeRow(
        upgrade_code=code,
        action_property=row.require("ActionProperty"),
        version_min=version("VersionMin"),
        version_max=version("VersionMax"),
        attributes=UpgradeAttributes(row.get_int("Attributes", 0) or 0),
        language=field_value("Language"),
    )


def _check_references(normalised: NormalisedProject) -> None:
    keys = normalised.component_keys()
    for feature in normalised.features.features.values():
        for component in feature.components:
            if component not in keys:
                raise ManifestError(
                    "feature names a component that has no row",
                    context={"feature": feature.feature, "component": component},
                )
    for entry in normalised.files:
        if entry.component not in keys:
            raise ManifestError(
                "file names a component that has no row",
                context={"file": entry.file, "component": entry.component},
            )
    for row in normalised.registry:
        if row.component not in keys:
            raise ManifestError(
                "registry row names a component that has no row",
                context={"registry": row.registry, "component": row.component},
            )
    # Not `component`, which the feature loop above binds to a str, and not
    # `row`, which the registry loop binds to a RegistryEntry. Three loops in
    # one function is already one too many.
    for component_row in normalised.components:
        if component_row.directory not in normalised.directories:
            raise ManifestError(
                "component names a directory that has no row",
                context={
                    "component": component_row.component,
                    "directory": component_row.directory,
                },
            )


def normalise(project: AipProject, *, check_references: bool = True) -> NormalisedProject:
    """Turn a parsed project into model objects.

    :param check_references: verify that features, files and registry rows all
        name components that exist, and that components name directories that
        exist. Turn it off only to inspect a project that is known broken.
    :raises AipParseError: when a cell will not convert.
    :raises ManifestError: when a cross table reference does not resolve.
    """
    properties = project.properties
    normalised = NormalisedProject(
        properties=properties,
        directories=DirectoryTable.from_rows(
            [_directory_row(row) for row in project.rows("Directory")]
        ),
        components=[_component_row(row) for row in project.rows("Component")],
        features=FeatureTree.from_features(
            [_feature_row(row) for row in project.rows("Feature")]
        ),
        files=[_file_row(row) for row in project.rows("File")],
        registry=[_registry_row(row) for row in project.rows("Registry")],
        upgrades=[_upgrade_row(row, properties) for row in project.rows("Upgrade")],
    )
    if check_references:
        _check_references(normalised)
    return normalised


__all__ = ["NormalisedProject", "normalise"]
