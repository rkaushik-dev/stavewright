"""A content addressed store for payload bytes.

Objects are filed under their own digest, two hex characters of directory then
the rest as the name, which is the layout git uses and for the same reason: a
flat directory of forty thousand files is slow to list on NTFS.

Deduplication is not an optimisation here, it is a correctness property. The
package installs ``C1Sizer.ocx`` twice, once into ``SysWOW64`` and once into
``System32``, and the two rows point at different source paths. If those two
paths hold the same bytes then the store holds one object and the two steps
verify against the same digest. If they hold different bytes the store holds
two objects and that difference is visible rather than depending on which row
was copied last.

Writes go through a temporary name and a rename, so a store never contains a
half written object even if the laptop is unplugged mid copy.
"""

from __future__ import annotations

import itertools
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from stavewright.errors import StageError
from stavewright.util.digest import DEFAULT_ALGORITHM, Digest, digest_bytes, digest_file
from stavewright.util.fsops import ensure_dir, remove_path

PREFIX_LENGTH = 2
OBJECTS_DIR = "objects"
INCOMING_DIR = "incoming"
"""Where a half written object lives until it is renamed into place.

Not beside its final name. A sha512 object name is 126 characters, and adding a
temporary suffix to that pushed the whole path over the 260 character limit on
a bench laptop with a deep staging directory. The write failed with a file not
found that took an embarrassingly long time to read correctly.

Incoming names are short counters instead, and the rename is still within one
volume so it stays atomic.
"""

_counter = itertools.count()


@dataclass(frozen=True, slots=True)
class StoreStats:
    """What a store holds."""

    objects: int
    bytes: int

    def describe(self) -> str:
        return f"{self.objects} objects, {self.bytes / 1024 / 1024:.1f} MB"


class ObjectStore:
    """Payload objects, filed under their digest."""

    def __init__(self, root: Path, *, algorithm: str = DEFAULT_ALGORITHM) -> None:
        self.root = root
        self.algorithm = algorithm

    @property
    def objects_root(self) -> Path:
        return self.root / OBJECTS_DIR

    @property
    def incoming_root(self) -> Path:
        return self.root / INCOMING_DIR

    def path_for(self, digest: Digest) -> Path:
        """Where an object with *digest* lives.

        :raises StageError: when the digest uses a different algorithm to the
            one this store was opened with, since the two would collide in the
            same directory tree.
        """
        if digest.algorithm != self.algorithm:
            raise StageError(
                "digest algorithm does not match the store",
                context={"store": self.algorithm, "digest": digest.algorithm},
            )
        prefix = digest.hexdigest[:PREFIX_LENGTH]
        return self.objects_root / prefix / digest.hexdigest[PREFIX_LENGTH:]

    def has(self, digest: Digest) -> bool:
        """True when the object is already stored."""
        return self.path_for(digest).is_file()

    def put_bytes(self, data: bytes) -> Digest:
        """Store *data* and return its digest. Storing twice is free."""
        digest = digest_bytes(data, self.algorithm)
        destination = self.path_for(digest)
        if destination.is_file():
            return digest
        ensure_dir(destination.parent)
        ensure_dir(self.incoming_root)
        temporary = self.incoming_root / f"{os.getpid()}-{next(_counter)}"
        try:
            with temporary.open("wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(destination)
        finally:
            if temporary.exists():
                temporary.unlink(missing_ok=True)
        return digest

    def put_file(self, path: Path) -> Digest:
        """Store the contents of *path*.

        :raises StageError: when the file cannot be read.
        """
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise StageError(
                "cannot read payload file",
                context={"path": str(path), "detail": str(exc)},
            ) from exc
        return self.put_bytes(data)

    def get(self, digest: Digest) -> bytes:
        """Read an object back.

        :raises StageError: when it is not in the store.
        """
        path = self.path_for(digest)
        try:
            return path.read_bytes()
        except OSError as exc:
            raise StageError(
                "object is not in the store",
                context={"digest": str(digest), "detail": str(exc)},
            ) from exc

    def verify(self, digest: Digest) -> bool:
        """Re-hash a stored object and check it still matches its name.

        Catches bit rot on a laptop that has been in a van for six months, and
        catches somebody editing an object in place.
        """
        path = self.path_for(digest)
        if not path.is_file():
            return False
        return digest_file(path, self.algorithm) == digest

    def remove(self, digest: Digest) -> bool:
        """Delete an object. Returns whether anything was removed."""
        path = self.path_for(digest)
        if not path.is_file():
            return False
        return remove_path(path)

    def iter_digests(self) -> Iterator[Digest]:
        """Every object in the store, in sorted order."""
        if not self.objects_root.is_dir():
            return
        for prefix in sorted(p for p in self.objects_root.iterdir() if p.is_dir()):
            for entry in sorted(p for p in prefix.iterdir() if p.is_file()):
                if entry.name.count(".tmp"):
                    continue
                yield Digest(self.algorithm, prefix.name + entry.name)

    def stats(self) -> StoreStats:
        """How many objects and how many bytes."""
        count = 0
        total = 0
        for digest in self.iter_digests():
            count += 1
            total += self.path_for(digest).stat().st_size
        return StoreStats(objects=count, bytes=total)

    def verify_all(self) -> list[Digest]:
        """Every object whose content no longer matches its name."""
        return [digest for digest in self.iter_digests() if not self.verify(digest)]

    def prune(self, keep: set[Digest]) -> list[Digest]:
        """Remove objects not in *keep*. Returns what was removed."""
        removed: list[Digest] = []
        for digest in list(self.iter_digests()):
            if digest not in keep:
                self.remove(digest)
                removed.append(digest)
        return removed


__all__ = ["OBJECTS_DIR", "PREFIX_LENGTH", "ObjectStore", "StoreStats"]
