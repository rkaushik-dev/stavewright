"""Content digests for payload items.

A commissioning bundle is only trustworthy if the bytes that reach a terminal
are the bytes the release engineer signed off. Every payload item therefore
carries a digest, and the same digest doubles as the key in the staging content
store so two components that ship the same DLL only occupy one copy.

The FTDI driver package is the reason :func:`digest_tree` exists. It is a
directory of ``.inf``, ``.cat`` and ``.sys`` files that has to be treated as one
unit, because installing half of it leaves a device node that never binds.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

DEFAULT_ALGORITHM = "sha256"
"""What new manifests are written with."""

SUPPORTED_ALGORITHMS = frozenset({"sha256", "sha384", "sha512"})
"""SHA-1 and MD5 are refused. Some 2016 era payload lists still carry them."""

_CHUNK = 1 << 20


@dataclass(frozen=True, slots=True, order=True)
class Digest:
    """An algorithm tagged content hash.

    Rendered as ``sha256:9f86d0...`` wherever it appears in a manifest, a
    journal record or a drift report.
    """

    algorithm: str
    hexdigest: str

    def __post_init__(self) -> None:
        if self.algorithm not in SUPPORTED_ALGORITHMS:
            raise ValueError(f"unsupported digest algorithm: {self.algorithm!r}")
        expected = hashlib.new(self.algorithm).digest_size * 2
        if len(self.hexdigest) != expected:
            raise ValueError(
                f"{self.algorithm} digest must be {expected} hex characters, "
                f"got {len(self.hexdigest)}"
            )
        if self.hexdigest.lower() != self.hexdigest:
            raise ValueError("digest hex must be lower case")
        if any(c not in "0123456789abcdef" for c in self.hexdigest):
            raise ValueError(f"digest is not hexadecimal: {self.hexdigest!r}")

    def __str__(self) -> str:
        return f"{self.algorithm}:{self.hexdigest}"

    @property
    def short(self) -> str:
        """First twelve hex characters, for log lines and staging directory names."""
        return self.hexdigest[:12]

    @classmethod
    def parse(cls, text: str) -> Digest:
        """Inverse of :meth:`__str__`.

        :raises ValueError: when the text has no algorithm tag or fails validation.
        """
        algorithm, sep, hexdigest = text.partition(":")
        if not sep:
            raise ValueError(f"digest is missing its algorithm tag: {text!r}")
        return cls(algorithm=algorithm, hexdigest=hexdigest)


def digest_bytes(data: bytes, algorithm: str = DEFAULT_ALGORITHM) -> Digest:
    """Hash an in memory blob."""
    if algorithm not in SUPPORTED_ALGORITHMS:
        raise ValueError(f"unsupported digest algorithm: {algorithm!r}")
    return Digest(algorithm, hashlib.new(algorithm, data).hexdigest())


def digest_file(path: Path, algorithm: str = DEFAULT_ALGORITHM) -> Digest:
    """Hash a file without holding it in memory.

    The 19 MB ``msi-Jet600.msi`` and the FPGA bitstream both go through here on
    every plan, so this stays streaming.
    """
    if algorithm not in SUPPORTED_ALGORITHMS:
        raise ValueError(f"unsupported digest algorithm: {algorithm!r}")
    hasher = hashlib.new(algorithm)
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            hasher.update(chunk)
    return Digest(algorithm, hasher.hexdigest())


def iter_tree(root: Path) -> Iterator[tuple[PurePosixPath, Path]]:
    """Walk *root* yielding relative posix paths paired with absolute ones.

    Sorted so two machines walking the same tree agree. Symlinks are not
    followed, because the driver package on a bench image sometimes contains a
    junction back to the staging root and following it never terminates.
    """
    root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        here = Path(dirpath)
        for name in sorted(filenames):
            absolute = here / name
            if absolute.is_symlink():
                continue
            yield PurePosixPath(absolute.relative_to(root).as_posix()), absolute


def digest_tree(root: Path, algorithm: str = DEFAULT_ALGORITHM) -> Digest:
    """Hash a whole directory as one value.

    The relative path is folded into the hash alongside the content, so moving a
    file inside the tree changes the result. Renaming ``ftdibus.sys`` would
    otherwise go unnoticed.
    """
    hasher = hashlib.new(algorithm)
    for relative, absolute in iter_tree(root):
        hasher.update(str(relative).encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(digest_file(absolute, algorithm).hexdigest.encode("ascii"))
        hasher.update(b"\n")
    return Digest(algorithm, hasher.hexdigest())


def digest_many(paths: Iterable[Path], algorithm: str = DEFAULT_ALGORITHM) -> dict[Path, Digest]:
    """Digest a batch of files, preserving the caller's order in the result."""
    return {path: digest_file(path, algorithm) for path in paths}


__all__ = [
    "DEFAULT_ALGORITHM",
    "SUPPORTED_ALGORITHMS",
    "Digest",
    "digest_bytes",
    "digest_file",
    "digest_many",
    "digest_tree",
    "iter_tree",
]
