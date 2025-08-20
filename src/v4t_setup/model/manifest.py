"""The manifest, which is the thing a release engineer actually signs.

Everything downstream trusts the manifest and nothing else. The staging tree is
built from it, the plan is built from it, and reconcile compares a terminal
against it. If the manifest is right and the signature verifies then a bad
terminal is a terminal problem, and that is the property the whole design is
arranged around.

Serialisation is JSON rather than TOML. Manifests are generated and verified,
not hand edited, and JSON has one canonical form that a signature can be taken
over. TOML has several.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from v4t_setup.__about__ import (
    MANIFEST_SCHEMA_VERSION,
    MANUFACTURER,
    PRODUCT_NAME,
    UPGRADE_CODE,
    __version__,
)
from v4t_setup.clock import now as utc_now
from v4t_setup.errors import ManifestError, ManifestSchemaError
from v4t_setup.model.payload import PayloadItem, PayloadKind, duplicate_ids
from v4t_setup.model.profile import Architecture, TerminalModel
from v4t_setup.util.digest import Digest
from v4t_setup.util.fsops import atomic_write_text
from v4t_setup.util.msiver import MsiVersion


def _item_to_dict(item: PayloadItem) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "item_id": item.item_id,
        "kind": item.kind.value,
        "name": item.name,
    }
    if item.directory:
        payload["directory"] = item.directory
    if item.component:
        payload["component"] = item.component
    if item.source is not None:
        payload["source"] = str(item.source)
    if item.digest is not None:
        payload["digest"] = str(item.digest)
    if item.architecture is not None:
        payload["architecture"] = item.architecture.value
    if item.machines:
        payload["machines"] = sorted(machine.value for machine in item.machines)
    if item.language_ids:
        payload["language_ids"] = sorted(item.language_ids)
    if item.provides:
        payload["provides"] = list(item.provides)
    if item.requires:
        payload["requires"] = list(item.requires)
    if item.detail:
        payload["detail"] = item.detail
    return payload


def _item_from_dict(payload: dict[str, Any]) -> PayloadItem:
    try:
        kind = PayloadKind(payload["kind"])
    except KeyError:
        raise ManifestError("payload item has no kind", context={"item": payload}) from None
    except ValueError:
        raise ManifestError(
            "payload item has an unknown kind",
            context={"kind": payload.get("kind"), "item": payload.get("item_id")},
        ) from None

    architecture = payload.get("architecture")
    machines = payload.get("machines") or []
    try:
        return PayloadItem(
            item_id=payload.get("item_id", ""),
            kind=kind,
            name=payload.get("name", ""),
            directory=payload.get("directory"),
            component=payload.get("component"),
            source=PurePosixPath(payload["source"]) if payload.get("source") else None,
            digest=Digest.parse(payload["digest"]) if payload.get("digest") else None,
            architecture=Architecture(architecture) if architecture else None,
            machines=frozenset(TerminalModel(value) for value in machines),
            language_ids=frozenset(payload.get("language_ids") or []),
            provides=tuple(payload.get("provides") or ()),
            requires=tuple(payload.get("requires") or ()),
            detail=dict(payload.get("detail") or {}),
        )
    except ValueError as exc:
        raise ManifestError(
            "payload item has an unreadable field",
            context={"item": payload.get("item_id"), "detail": str(exc)},
        ) from exc


@dataclass
class Manifest:
    """A signed description of one commissioning bundle."""

    product: str = PRODUCT_NAME
    manufacturer: str = MANUFACTURER
    version: MsiVersion = field(default_factory=lambda: MsiVersion(1, 0, 0))
    upgrade_code: str = UPGRADE_CODE
    schema_version: int = MANIFEST_SCHEMA_VERSION
    generated_at: datetime | None = None
    generator: str = f"v4t-setup {__version__}"
    items: list[PayloadItem] = field(default_factory=list)
    notes: str = ""
    source: Path | None = None

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self.items)

    def __contains__(self, item_id: str) -> bool:
        return any(item.item_id == item_id for item in self.items)

    def get(self, item_id: str) -> PayloadItem | None:
        for item in self.items:
            if item.item_id == item_id:
                return item
        return None

    def require(self, item_id: str) -> PayloadItem:
        """One item by id.

        :raises ManifestError: when the manifest has no such item.
        """
        item = self.get(item_id)
        if item is None:
            raise ManifestError("manifest has no such item", context={"item": item_id})
        return item

    def of_kind(self, kind: PayloadKind) -> list[PayloadItem]:
        return [item for item in self.items if item.kind is kind]

    def validate(self) -> None:
        """Check what can be checked without touching the payload.

        :raises ManifestError: on a duplicate item id, a missing product name,
            or a source path that climbs out of the bundle.
        """
        if not self.product:
            raise ManifestError("manifest has no product name")
        repeated = duplicate_ids(self.items)
        if repeated:
            raise ManifestError(
                "manifest has duplicate item ids",
                context={"items": repeated},
            )
        for item in self.items:
            if item.source is not None and ".." in item.source.parts:
                raise ManifestError(
                    "payload source climbs out of the bundle",
                    context={"item": item.item_id, "source": str(item.source)},
                )
            if item.source is not None and item.source.is_absolute():
                raise ManifestError(
                    "payload source must be relative to the bundle",
                    context={"item": item.item_id, "source": str(item.source)},
                )

    def to_dict(self) -> dict[str, Any]:
        """The full serialised form."""
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "product": self.product,
            "manufacturer": self.manufacturer,
            "version": str(self.version),
            "upgrade_code": self.upgrade_code,
            "generator": self.generator,
            "items": [_item_to_dict(item) for item in self.items],
        }
        if self.generated_at is not None:
            payload["generated_at"] = self.generated_at.isoformat()
        if self.notes:
            payload["notes"] = self.notes
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any], *, source: Path | None = None) -> Manifest:
        """Rebuild a manifest from its serialised form.

        :raises ManifestSchemaError: when the schema version is newer than this
            toolkit understands.
        :raises ManifestError: on any other unreadable field.
        """
        schema = payload.get("schema_version", MANIFEST_SCHEMA_VERSION)
        if not isinstance(schema, int):
            raise ManifestError("schema_version is not a number", context={"value": schema})
        if schema > MANIFEST_SCHEMA_VERSION:
            raise ManifestSchemaError(
                "manifest was written by a newer toolkit",
                context={"found": schema, "understood": MANIFEST_SCHEMA_VERSION},
            )
        raw_version = payload.get("version", "1.0.0")
        try:
            version = MsiVersion.parse(raw_version)
        except ValueError as exc:
            raise ManifestError(
                "manifest version is not an MSI version",
                context={"value": raw_version},
            ) from exc
        stamp = payload.get("generated_at")
        return cls(
            product=payload.get("product", ""),
            manufacturer=payload.get("manufacturer", ""),
            version=version,
            upgrade_code=payload.get("upgrade_code", ""),
            schema_version=schema,
            generated_at=datetime.fromisoformat(stamp) if stamp else None,
            generator=payload.get("generator", ""),
            items=[_item_from_dict(entry) for entry in payload.get("items", [])],
            notes=payload.get("notes", ""),
            source=source,
        )

    def canonical(self) -> str:
        """The exact bytes a signature is taken over.

        Sorted keys, no whitespace, and ``generated_at`` included. Two builds
        of the same payload at different times are different manifests and
        should not share a signature.
        """
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    def digest(self) -> Digest:
        """Content digest of the canonical form."""
        return Digest("sha256", hashlib.sha256(self.canonical().encode("utf-8")).hexdigest())

    def dumps(self, *, indent: int = 2) -> str:
        """Human readable JSON, for a manifest that lands in a repository."""
        return json.dumps(self.to_dict(), sort_keys=True, indent=indent) + "\n"

    def write(self, path: Path) -> None:
        """Write the manifest, atomically."""
        atomic_write_text(path, self.dumps())

    def describe(self) -> str:
        where = self.source.name if self.source else "(in memory)"
        return f"{where}: {self.product} {self.version}, {len(self.items)} items"


def loads(text: str, *, source: Path | None = None) -> Manifest:
    """Parse a manifest from JSON text.

    :raises ManifestError: when the text is not a JSON object.
    """
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ManifestError(
            "manifest is not valid JSON",
            context={"detail": str(exc), "source": str(source) if source else None},
        ) from exc
    if not isinstance(payload, dict):
        raise ManifestError("manifest is not a JSON object")
    return Manifest.from_dict(payload, source=source)


def read_manifest(path: Path) -> Manifest:
    """Read and parse a manifest from disk.

    :raises ManifestError: when the file cannot be read or does not parse.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(
            "cannot read manifest",
            context={"path": str(path), "detail": str(exc)},
        ) from exc
    return loads(text, source=path)


__all__ = ["Manifest", "loads", "read_manifest", "utc_now"]
