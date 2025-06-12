"""Materialising a bundle onto the bench laptop.

Staging walks the manifest, finds the bytes each item names, checks them
against the digest the release engineer signed, and files them. Files go into
the content store. Directories, which is how the FTDI package and the side by
side assemblies arrive, are copied whole into a tree because the things that
consume them want a real path rather than bytes.

The result is a directory that can be carried onto a factory floor and used
with no network, and an index that says exactly what is in it.

Verification happens at stage time, not at apply time. Finding out that a
payload does not match its digest is a five minute problem in the office and an
afternoon on the line.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from v4t_setup.errors import StageError
from v4t_setup.model.manifest import Manifest
from v4t_setup.model.payload import PayloadItem
from v4t_setup.signing import Signature
from v4t_setup.stage.store import ObjectStore
from v4t_setup.util.digest import Digest, digest_tree
from v4t_setup.util.fsops import atomic_write_text, ensure_dir, safe_join

INDEX_NAME = "index.json"
MANIFEST_NAME = "manifest.json"
SIGNATURE_NAME = "manifest.json.sig"
TREES_DIR = "trees"

FILE_ENTRY = "file"
TREE_ENTRY = "tree"


@dataclass(frozen=True, slots=True)
class IndexEntry:
    """What the index records about one staged item."""

    item_id: str
    source: str
    kind: str
    digest: Digest
    size: int
    path: str

    def to_dict(self) -> dict[str, object]:
        return {
            "item_id": self.item_id,
            "source": self.source,
            "kind": self.kind,
            "digest": str(self.digest),
            "size": self.size,
            "path": self.path,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> IndexEntry:
        try:
            return cls(
                item_id=str(payload["item_id"]),
                source=str(payload["source"]),
                kind=str(payload["kind"]),
                digest=Digest.parse(str(payload["digest"])),
                size=int(payload["size"]),  # type: ignore[arg-type]
                path=str(payload["path"]),
            )
        except (KeyError, ValueError) as exc:
            raise StageError(
                "staging index entry is unreadable",
                context={"entry": payload, "detail": str(exc)},
            ) from exc


@dataclass
class StagedBundle:
    """A staged bundle on disk, readable by the apply layer."""

    root: Path
    manifest: Manifest
    entries: dict[str, IndexEntry] = field(default_factory=dict)

    @property
    def store(self) -> ObjectStore:
        return ObjectStore(self.root)

    @property
    def trees_root(self) -> Path:
        return self.root / TREES_DIR

    def _entry(self, relative: str) -> IndexEntry:
        normalised = relative.replace("\\", "/")
        entry = self.entries.get(normalised)
        if entry is None:
            raise StageError(
                "nothing staged under that source path",
                context={"source": relative, "staged": sorted(self.entries)},
            )
        return entry

    # PayloadSource

    def exists(self, relative: str) -> bool:
        return relative.replace("\\", "/") in self.entries

    def read(self, relative: str) -> bytes:
        """Read a staged file.

        :raises StageError: for an unknown path, or for a directory item, which
            has no single blob to return.
        """
        entry = self._entry(relative)
        if entry.kind != FILE_ENTRY:
            raise StageError(
                "this payload item is a directory, read its path instead",
                context={"source": relative},
            )
        return self.store.get(entry.digest)

    def local_path(self, relative: str) -> str:
        """Where a staged item is on disk."""
        entry = self._entry(relative)
        return str(self.root / entry.path)

    # Reporting

    def total_bytes(self) -> int:
        return sum(entry.size for entry in self.entries.values())

    def describe(self) -> str:
        megabytes = self.total_bytes() / 1024 / 1024
        return f"{self.root.name}: {len(self.entries)} items, {megabytes:.1f} MB"


@dataclass
class StagingReport:
    """What staging did."""

    bundle: StagedBundle
    staged: list[str] = field(default_factory=list)
    reused: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)

    @property
    def item_count(self) -> int:
        return len(self.staged) + len(self.reused)

    def lines(self) -> list[str]:
        result = [self.bundle.describe(), f"{len(self.staged)} staged, {len(self.reused)} reused"]
        result.extend(f"skipped {item}: {reason}" for item, reason in sorted(self.skipped.items()))
        return result


def index_path(root: Path) -> Path:
    return root / INDEX_NAME


def write_index(root: Path, entries: dict[str, IndexEntry]) -> None:
    """Write the index, sorted so two identical stagings match byte for byte."""
    payload = {
        "entries": [entries[key].to_dict() for key in sorted(entries)],
    }
    atomic_write_text(index_path(root), json.dumps(payload, sort_keys=True, indent=2) + "\n")


def read_index(root: Path) -> dict[str, IndexEntry]:
    """Read the index back.

    :raises StageError: when it is missing or unreadable.
    """
    path = index_path(root)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise StageError(
            "cannot read the staging index",
            context={"path": str(path), "detail": str(exc)},
        ) from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StageError(
            "staging index is not valid JSON",
            context={"path": str(path), "detail": str(exc)},
        ) from exc
    entries = [IndexEntry.from_dict(entry) for entry in payload.get("entries", [])]
    return {entry.source: entry for entry in entries}


class Stager:
    """Builds a staging tree from a manifest and a payload directory."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def bundle_root(self, manifest: Manifest) -> Path:
        """Where a bundle with this manifest is staged.

        Named after the manifest digest, so restaging the same bundle lands in
        the same place and restaging a different one cannot overwrite it.
        """
        return self.root / manifest.digest().short

    def stage(
        self,
        manifest: Manifest,
        source_root: Path,
        *,
        signature: Signature | None = None,
    ) -> StagingReport:
        """Stage every item in *manifest* from *source_root*.

        :raises StageError: when a source is missing, or its content does not
            match the digest the manifest declares.
        """
        manifest.validate()
        root = self.bundle_root(manifest)
        ensure_dir(root)
        store = ObjectStore(root)

        entries: dict[str, IndexEntry] = {}
        report = StagingReport(bundle=StagedBundle(root=root, manifest=manifest))

        for item in manifest.items:
            if item.source is None:
                report.skipped[item.item_id] = "item has no payload to stage"
                continue
            source = self._resolve_source(source_root, item)
            if source.is_dir():
                entry = self._stage_tree(root, item, source)
            else:
                entry = self._stage_file(store, item, source)
            if entry.kind == FILE_ENTRY and store.has(entry.digest) and item.digest is not None:
                report.reused.append(item.item_id)
            else:
                report.staged.append(item.item_id)
            entries[entry.source] = entry

        write_index(root, entries)
        manifest.write(root / MANIFEST_NAME)
        if signature is not None:
            signature.write(root / SIGNATURE_NAME)

        report.bundle.entries = entries
        return report

    def _resolve_source(self, source_root: Path, item: PayloadItem) -> Path:
        assert item.source is not None
        try:
            source = safe_join(source_root, item.source)
        except ValueError as exc:
            raise StageError(
                "payload source is not inside the source tree",
                context={"item": item.item_id, "source": str(item.source)},
            ) from exc
        if not source.exists():
            raise StageError(
                "payload source is missing",
                context={"item": item.item_id, "source": str(item.source)},
            )
        return source

    def _stage_file(self, store: ObjectStore, item: PayloadItem, source: Path) -> IndexEntry:
        digest = store.put_file(source)
        if item.digest is not None and item.digest != digest:
            store.remove(digest)
            raise StageError(
                "payload does not match the digest in the manifest",
                context={
                    "item": item.item_id,
                    "expected": str(item.digest),
                    "found": str(digest),
                },
            )
        assert item.source is not None
        return IndexEntry(
            item_id=item.item_id,
            source=str(item.source),
            kind=FILE_ENTRY,
            digest=digest,
            size=source.stat().st_size,
            path=str(store.path_for(digest).relative_to(store.root).as_posix()),
        )

    def _stage_tree(self, root: Path, item: PayloadItem, source: Path) -> IndexEntry:
        assert item.source is not None
        destination = root / TREES_DIR / item.item_id
        if destination.exists():
            shutil.rmtree(destination)
        ensure_dir(destination.parent)
        shutil.copytree(source, destination)
        digest = digest_tree(destination)
        if item.digest is not None and item.digest != digest:
            shutil.rmtree(destination)
            raise StageError(
                "payload tree does not match the digest in the manifest",
                context={
                    "item": item.item_id,
                    "expected": str(item.digest),
                    "found": str(digest),
                },
            )
        size = sum(p.stat().st_size for p in destination.rglob("*") if p.is_file())
        return IndexEntry(
            item_id=item.item_id,
            source=str(item.source),
            kind=TREE_ENTRY,
            digest=digest,
            size=size,
            path=str(PurePosixPath(TREES_DIR) / item.item_id),
        )


def open_bundle(root: Path) -> StagedBundle:
    """Reopen a staging tree that was built earlier.

    :raises StageError: when the tree has no manifest or no index.
    """
    from v4t_setup.model.manifest import read_manifest

    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        raise StageError(
            "staging tree has no manifest",
            context={"root": str(root)},
        )
    return StagedBundle(
        root=root,
        manifest=read_manifest(manifest_path),
        entries=read_index(root),
    )


__all__ = [
    "FILE_ENTRY",
    "INDEX_NAME",
    "MANIFEST_NAME",
    "SIGNATURE_NAME",
    "TREES_DIR",
    "TREE_ENTRY",
    "IndexEntry",
    "StagedBundle",
    "Stager",
    "StagingReport",
    "index_path",
    "open_bundle",
    "read_index",
    "write_index",
]
