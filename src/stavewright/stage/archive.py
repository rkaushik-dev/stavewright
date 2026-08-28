"""Putting a staged bundle on a memory stick.

A bundle lives as a directory: a manifest, a signature, an index, an object
store keyed by digest and a few whole trees. That shape is right on a laptop
and wrong for carrying, because it is thousands of files and a plant's IT will
not let a laptop onto their network anyway.

So it goes into one zip. What comes out the other end has to be provably the
same bundle, and there are two ways to be sure and only one of them is worth
having:

Hash the archive. That tells you the file arrived intact and nothing about
whether it was the right file, because whoever made the archive made the hash.

Hash what is inside it, against the digests already in the index that the
manifest signature already covers. That is what unpacking does here, and it
means a tampered archive fails on the file that was tampered with rather than
on a number in a covering note.

The archive is deliberately not compressed. Nearly all of it is the payload,
which is DLLs and a cabinet, and zip's deflate gets a few percent on those
while costing a minute on a bench laptop. The one thing that would compress is
the index, which is measured in kilobytes.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from stavewright.errors import StageError
from stavewright.stage.stager import (
    FILE_ENTRY,
    INDEX_NAME,
    MANIFEST_NAME,
    SIGNATURE_NAME,
    StagedBundle,
    open_bundle,
    read_index,
)
from stavewright.stage.store import ObjectStore
from stavewright.util.digest import digest_bytes

ARCHIVE_SUFFIX = ".swbundle"
COMMENT = b"stavewright staged bundle"

MAX_MEMBER_BYTES = 2 * 1024 * 1024 * 1024
"""Two gigabytes. Nothing in a bundle is close, and a member that claims more
is either corrupt or trying to fill the disk of whatever unpacks it."""


@dataclass(frozen=True, slots=True)
class ArchiveReport:
    """What packing or unpacking did."""

    path: Path
    members: int = 0
    bytes_written: int = 0
    verified: int = 0
    skipped: list[str] = field(default_factory=list)

    def describe(self) -> str:
        return (
            f"{self.path.name}: {self.members} files, {self.bytes_written} bytes, "
            f"{self.verified} digests checked"
        )


def _members(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*") if path.is_file())


def pack(bundle: StagedBundle, destination: Path) -> ArchiveReport:
    """Write a staged bundle into one archive.

    :raises StageError: when the destination exists, or the bundle has no
        manifest beside it. Overwriting is not offered: an archive somebody
        has already carried somewhere is not this command's to replace.
    """
    if destination.exists():
        raise StageError(
            "refusing to overwrite an existing archive",
            context={"path": str(destination)},
        )
    if not (bundle.root / MANIFEST_NAME).is_file():
        raise StageError(
            "this bundle has no manifest, so an archive of it would not be one",
            context={"root": str(bundle.root)},
        )

    written = 0
    members = _members(bundle.root)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_STORED) as archive:
        archive.comment = COMMENT
        for path in members:
            relative = path.relative_to(bundle.root).as_posix()
            archive.write(path, relative)
            written += path.stat().st_size
    return ArchiveReport(path=destination, members=len(members), bytes_written=written)


def _safe_name(name: str, destination: Path) -> Path:
    """Where a member lands, refusing anything that leaves the directory.

    A zip may name ``../../etc/passwd`` and Python will happily write it. This
    is the check that means unpacking somebody else's archive is not a way to
    write anywhere on the bench laptop.
    """
    if name.startswith("/") or ".." in Path(name).parts or ":" in name:
        raise StageError(
            "this archive names a member outside the directory it unpacks to",
            context={"member": name},
        )
    return destination / Path(name)


def unpack(archive_path: Path, destination: Path, *, verify: bool = True) -> ArchiveReport:
    """Read an archive back into a staged bundle.

    :param verify: re-hash every object against the index. On by default,
        because an archive that arrived on a memory stick is the least trusted
        thing in this toolkit.
    :raises StageError: when the archive is not one, names a member outside the
        destination, or holds an object whose bytes do not match the index.
    """
    if destination.exists() and any(destination.iterdir()):
        raise StageError(
            "refusing to unpack into a directory that is not empty",
            context={"path": str(destination)},
        )
    try:
        with zipfile.ZipFile(archive_path) as archive:
            names = archive.namelist()
            if MANIFEST_NAME not in names or INDEX_NAME not in names:
                raise StageError(
                    "this archive is not a staged bundle",
                    context={
                        "path": str(archive_path),
                        "missing": sorted({MANIFEST_NAME, INDEX_NAME} - set(names)),
                    },
                )
            for info in archive.infolist():
                if info.is_dir():
                    continue
                if info.file_size > MAX_MEMBER_BYTES:
                    raise StageError(
                        "a member of this archive is implausibly large",
                        context={"member": info.filename, "bytes": info.file_size},
                    )
                target = _safe_name(info.filename, destination)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(info))
    except zipfile.BadZipFile as exc:
        raise StageError(
            "this file is not a zip archive",
            context={"path": str(archive_path), "detail": str(exc)},
        ) from exc

    checked = 0
    if verify:
        checked = verify_unpacked(destination)
    return ArchiveReport(
        path=destination,
        members=len(_members(destination)),
        bytes_written=sum(path.stat().st_size for path in _members(destination)),
        verified=checked,
    )


def verify_unpacked(root: Path) -> int:
    """Re-hash every file object against the index. Returns how many.

    Trees are not hashed here. The index carries one digest for a whole tree
    and rebuilding it means walking the tree in the same order the stager did,
    which :func:`~stavewright.stage.stager.open_bundle` already does when the
    bundle is opened.

    :raises StageError: on the first object whose bytes do not match.
    """
    index = read_index(root)
    store = ObjectStore(root)
    checked = 0
    for entry in index.values():
        if entry.kind != FILE_ENTRY:
            continue
        path = store.path_for(entry.digest)
        if not path.is_file():
            raise StageError(
                "the archive is missing an object its index names",
                context={"item": entry.item_id, "digest": str(entry.digest)},
            )
        found = digest_bytes(path.read_bytes())
        if found != entry.digest:
            raise StageError(
                "an object in this archive does not match the digest in its index",
                context={
                    "item": entry.item_id,
                    "expected": str(entry.digest),
                    "found": str(found),
                },
            )
        checked += 1
    return checked


def open_archive(archive_path: Path, destination: Path) -> StagedBundle:
    """Unpack and open in one step, which is what a bench actually does."""
    unpack(archive_path, destination)
    return open_bundle(destination)


def archive_manifest_digest(archive_path: Path) -> str:
    """The digest of the manifest inside an archive, without unpacking it.

    Enough to tell two archives apart on a memory stick with six on it.

    :raises StageError: when the archive cannot be read or holds no manifest.
    """
    try:
        with zipfile.ZipFile(archive_path) as archive:
            return str(digest_bytes(archive.read(MANIFEST_NAME)))
    except KeyError as exc:
        raise StageError(
            "this archive holds no manifest",
            context={"path": str(archive_path)},
        ) from exc
    except (OSError, zipfile.BadZipFile) as exc:
        raise StageError(
            "cannot read archive",
            context={"path": str(archive_path), "detail": str(exc)},
        ) from exc


def is_signed(archive_path: Path) -> bool:
    """Whether the archive carries the manifest's detached signature."""
    try:
        with zipfile.ZipFile(archive_path) as archive:
            return SIGNATURE_NAME in archive.namelist()
    except (OSError, zipfile.BadZipFile):
        return False


__all__ = [
    "ARCHIVE_SUFFIX",
    "COMMENT",
    "MAX_MEMBER_BYTES",
    "ArchiveReport",
    "archive_manifest_digest",
    "is_signed",
    "open_archive",
    "pack",
    "unpack",
    "verify_unpacked",
]
