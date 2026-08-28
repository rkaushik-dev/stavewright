"""Turning the shipped project file into a bundle manifest.

This closes the loop. ``setup.aip`` has always been the description of what an
S-Jet 600 image contains, and until now the toolkit could read it but a manifest
still had to be written by hand. Now the AIP is the input and the manifest is
generated, which means the two cannot drift.

Three things the AIP does not say, and which the builder has to be told:

Which components are COM servers. ``SelfReg`` is false on both OCX rows because
Advanced Installer registers them out of band, so the file table gives no hint
and :data:`COM_COMPONENTS` names them.

Which language each resource DLL is for. The component names say it plainly
enough to a person but there is no column for it, so the locale table is
consulted by component name.

Which machines the FPGA bitstream is for. The AIP builds one image for the
whole range and the machine gating is a commissioning decision rather than a
packaging one.

Source paths need translating rather than copying across. The AIP is relative
to the project directory and most of the built binaries come from
``..\\Release``, which climbs out of it. A bundle is a self contained thing
that gets carried to a factory, so nothing in it can point outside itself. The
builder flattens every source under ``payload/`` and keeps the original in the
item's detail so a release engineer can still see where it came from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath

from stavewright.aip.normalize import NormalisedProject
from stavewright.errors import ManifestError
from stavewright.model.locale import BY_COMPONENT
from stavewright.model.manifest import Manifest, utc_now
from stavewright.model.payload import PayloadItem, PayloadKind
from stavewright.model.profile import Architecture, TerminalModel
from stavewright.util.digest import Digest, digest_file, digest_tree

COM_COMPONENTS = frozenset({"C1Sizer.ocx", "C1Sizer.ocx_1", "vsFlex8n.ocx", "vsFlex8n.ocx_1"})
"""Components that need registering. SelfReg says false for all four."""

NATIVE_COMPONENTS = frozenset({"C1Sizer.ocx_1", "vsFlex8n.ocx_1"})
"""The ones that go into the native hive rather than through WOW64."""

FPGA_COMPONENTS = frozenset({"pcdc_top.rbf"})
FPGA_MACHINES = frozenset({TerminalModel.SJET_600, TerminalModel.SJET_600_TIJ})

FONT_COMPONENTS = {"ocrb.ttf": "OCR-B"}

DRIVER_COMPONENTS = {"ftdibus.inf": "ftdibus.inf", "ftdiport.inf": "ftdiport.inf"}
"""Components that are really a driver package rather than a plain file."""


REDISTRIBUTABLE_SOURCES = ("vcredist_x86",)
"""Path fragments that mark a payload as a redistributable rather than a file."""

PAYLOAD_PREFIX = "payload"
"""Every source in a generated manifest lives under this."""


def bundle_source(aip_source: PurePosixPath) -> PurePosixPath:
    """Where an AIP source path lives inside a bundle.

    ``..\\Release\\V4T.exe`` becomes ``payload/Release/V4T.exe``. The parent
    segments are dropped rather than resolved, because what matters is that the
    result is inside the bundle and stable, not that it retraces the layout of
    somebody's build tree.
    """
    parts = [part for part in aip_source.parts if part not in ("..", ".")]
    return PurePosixPath(PAYLOAD_PREFIX).joinpath(*parts)


@dataclass
class BuildReport:
    """What the builder made and what it could not resolve."""

    manifest: Manifest
    missing_sources: dict[str, str] = field(default_factory=dict)
    digested: int = 0

    @property
    def is_complete(self) -> bool:
        """True when every item that names a source had one on disk."""
        return not self.missing_sources

    def lines(self) -> list[str]:
        result = [self.manifest.describe(), f"{self.digested} items digested"]
        result.extend(
            f"no payload for {item}: {source}"
            for item, source in sorted(self.missing_sources.items())
        )
        return result


def language_for(component: str) -> str | None:
    """The locale table key for a component, or None when it is not a language.

    Component keys in this project carry the file extension, so the resource
    DLL component is ``LangPolish.dll`` while the locale table knows it as
    ``LangPolish``. Missing that is how every language pack came out of the
    first build as a plain file with no locale gating on it.
    """
    stem = component[:-4] if component.lower().endswith(".dll") else component
    return stem if stem in BY_COMPONENT else None


def _kind_for(component: str, file_name: str, source: PurePosixPath | None) -> PayloadKind:
    if component in COM_COMPONENTS:
        return PayloadKind.COM_SERVER
    if component in FPGA_COMPONENTS:
        return PayloadKind.FPGA_IMAGE
    if component in FONT_COMPONENTS:
        return PayloadKind.FONT
    if language_for(component) is not None:
        return PayloadKind.LANGUAGE_PACK
    if file_name in DRIVER_COMPONENTS:
        return PayloadKind.DRIVER_PACKAGE
    if source is not None and any(part in str(source) for part in REDISTRIBUTABLE_SOURCES):
        return PayloadKind.REDISTRIBUTABLE
    return PayloadKind.FILE


def _detail_for(component: str, kind: PayloadKind, file_name: str) -> dict[str, object]:
    detail: dict[str, object] = {}
    if component in NATIVE_COMPONENTS:
        detail["native"] = True
    if kind is PayloadKind.FONT:
        detail["font"] = FONT_COMPONENTS[component]
    if kind is PayloadKind.DRIVER_PACKAGE:
        detail["inf"] = DRIVER_COMPONENTS[file_name]
    return detail


def _constraints(component: str, kind: PayloadKind) -> tuple[
    Architecture | None, frozenset[TerminalModel], frozenset[int]
]:
    architecture = None
    machines: frozenset[TerminalModel] = frozenset()
    languages: frozenset[int] = frozenset()
    if kind is PayloadKind.FPGA_IMAGE:
        machines = FPGA_MACHINES
    if kind is PayloadKind.LANGUAGE_PACK:
        key = language_for(component)
        assert key is not None
        languages = frozenset({BY_COMPONENT[key].lcid})
    if component in NATIVE_COMPONENTS:
        architecture = Architecture.X64
    return architecture, machines, languages


def build_manifest(
    project: NormalisedProject,
    payload_root: Path,
    *,
    install_level: int = 100,
    digest_payload: bool = True,
    notes: str = "",
) -> BuildReport:
    """Build a manifest from a normalised project and a payload tree.

    :param payload_root: where the source paths in the AIP resolve against.
    :param install_level: only components reachable at this level are included.
    :param digest_payload: read every source and record its digest. Turn it off
        for a quick look at what a manifest would contain.
    :raises ManifestError: when the project selects no components at all, which
        means the install level or the feature table is wrong.
    """
    selected = project.features.components_for(install_level)
    if not selected:
        raise ManifestError(
            "no components are selected at this install level",
            context={"install_level": install_level},
        )

    by_component = {component.component: component for component in project.components}
    items: list[PayloadItem] = []
    report = BuildReport(manifest=Manifest())

    for component_key in sorted(selected):
        component = by_component.get(component_key)
        if component is None:
            continue
        files = project.files_for(component_key)
        registry = project.registry_for(component_key)

        if not files and registry:
            items.append(
                PayloadItem(
                    item_id=f"reg.{component_key}",
                    kind=PayloadKind.REGISTRY,
                    name=component_key,
                    component=component_key,
                    detail={"rows": [row.registry for row in registry]},
                )
            )
            continue

        if not files:
            items.append(
                PayloadItem(
                    item_id=f"dir.{component_key}",
                    kind=PayloadKind.DIRECTORY,
                    name=component_key,
                    directory=component.directory,
                    component=component_key,
                )
            )
            continue

        for entry in files:
            aip_source = entry.source_relative()
            kind = _kind_for(component_key, entry.target_name, aip_source)
            architecture, machines, languages = _constraints(component_key, kind)
            detail = _detail_for(component_key, kind, entry.target_name)

            source = None
            digest = None
            if aip_source is not None:
                source = bundle_source(aip_source)
                detail["aip_source"] = str(aip_source)
                if digest_payload:
                    digest, missing = _digest_source(payload_root, aip_source)
                    if missing:
                        report.missing_sources[entry.file] = str(aip_source)
                    else:
                        report.digested += 1

            items.append(
                PayloadItem(
                    item_id=entry.file,
                    kind=kind,
                    name=entry.target_name,
                    directory=None if kind is PayloadKind.DRIVER_PACKAGE else component.directory,
                    component=component_key,
                    source=source,
                    digest=digest,
                    architecture=architecture,
                    machines=machines,
                    language_ids=languages,
                    detail=detail,
                )
            )

    report.manifest = Manifest(
        product=project.product_name or Manifest().product,
        manufacturer=project.manufacturer or Manifest().manufacturer,
        version=project.product_version,
        upgrade_code=project.upgrade_code or Manifest().upgrade_code,
        generated_at=utc_now(),
        items=items,
        notes=notes,
    )
    report.manifest.validate()
    return report


def _digest_source(root: Path, source: PurePosixPath) -> tuple[Digest | None, bool]:
    """Digest whatever *source* points at, and say whether it was missing.

    The second half of the pair is the interesting one. A source that is not
    there is not an error at build time, because the AIP names files the
    working copy does not always have, but it does have to reach the report.
    """
    candidate = root / Path(*PureWindowsPath(str(source)).parts)
    if candidate.is_dir():
        return digest_tree(candidate), False
    if candidate.is_file():
        return digest_file(candidate), False
    return None, True


__all__ = [
    "COM_COMPONENTS",
    "DRIVER_COMPONENTS",
    "FONT_COMPONENTS",
    "FPGA_COMPONENTS",
    "FPGA_MACHINES",
    "NATIVE_COMPONENTS",
    "REDISTRIBUTABLE_SOURCES",
    "BuildReport",
    "build_manifest",
    "bundle_source",
]
