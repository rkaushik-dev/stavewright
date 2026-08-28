"""Filesystem primitives that behave the same on a bench PC and in a test.

Two things drive the design. Terminals run Windows, where a file held open by
the marking front end cannot be replaced and a delete can fail for several
seconds after the handle closes. And a manifest is data that arrives from
outside, so any relative path in it is a path traversal until proven otherwise.
"""

from __future__ import annotations

import os
import shutil
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath, PureWindowsPath

from stavewright.util.digest import DEFAULT_ALGORITHM, Digest, digest_file

REMOVE_ATTEMPTS = 5
"""How many times to retry a delete before giving up."""

REMOVE_BACKOFF_SECONDS = 0.2
"""Base delay between delete attempts. Grows linearly."""

_RESERVED_WINDOWS_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)


class PathEscapeError(ValueError):
    """A relative path resolved outside the root it was joined to."""


def is_reserved_name(name: str) -> bool:
    """True when *name* is a device name Windows will not let you create.

    The check ignores an extension, because ``NUL.txt`` is just as unusable as
    ``NUL``. This matters when a customer template set names a field file after
    a printer port.
    """
    stem = name.split(".", 1)[0].upper().rstrip(" ")
    return stem in _RESERVED_WINDOWS_NAMES


def safe_join(root: Path, relative: str | PurePosixPath) -> Path:
    """Join *relative* under *root*, refusing anything that climbs out.

    Absolute paths, drive letters, UNC prefixes and ``..`` segments are all
    rejected rather than normalised away, so a bad manifest fails loudly instead
    of quietly writing to the wrong place.

    :raises PathEscapeError: when the result would not be under *root*.
    """
    text = str(relative)
    if not text or text in {".", "./"}:
        raise PathEscapeError("empty relative path")
    windows_view = PureWindowsPath(text)
    if windows_view.is_absolute() or windows_view.drive or text.startswith(("\\\\", "//")):
        raise PathEscapeError(f"path is not relative: {text!r}")
    parts = PurePosixPath(text.replace("\\", "/")).parts
    if any(part == ".." for part in parts):
        raise PathEscapeError(f"path climbs above its root: {text!r}")
    for part in parts:
        if is_reserved_name(part):
            raise PathEscapeError(f"path uses a reserved device name: {part!r}")
    candidate = root.joinpath(*parts)
    root_resolved = root.resolve()
    try:
        candidate.resolve().relative_to(root_resolved)
    except ValueError as exc:
        raise PathEscapeError(f"path escapes {root_resolved}: {text!r}") from exc
    return candidate


def ensure_dir(path: Path) -> Path:
    """Create *path* and its parents, returning it. Idempotent."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write *data* to *path* so a reader never sees a half written file.

    A temporary sibling is written and flushed, then renamed over the target.
    ``Path.replace`` is atomic on both NTFS and POSIX when source and
    destination share a volume, which the sibling guarantees.
    """
    ensure_dir(path.parent)
    temp = path.with_name(f".{path.name}.swtmp")
    try:
        with temp.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temp.replace(path)
    finally:
        if temp.exists():
            temp.unlink(missing_ok=True)


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Text flavour of :func:`atomic_write_bytes`. Always writes LF endings."""
    atomic_write_bytes(path, text.replace("\r\n", "\n").encode(encoding))


def remove_path(path: Path) -> bool:
    """Delete a file or a tree, retrying while Windows still holds a handle.

    :returns: True when something was removed, False when the path was absent.
    :raises OSError: when every attempt failed.
    """
    if not path.exists() and not path.is_symlink():
        return False
    last: OSError | None = None
    for attempt in range(REMOVE_ATTEMPTS):
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
        except OSError as exc:
            last = exc
            time.sleep(REMOVE_BACKOFF_SECONDS * (attempt + 1))
        else:
            return True
    assert last is not None
    raise last


def copy_with_digest(
    source: Path,
    destination: Path,
    *,
    algorithm: str = DEFAULT_ALGORITHM,
    expected: Digest | None = None,
) -> Digest:
    """Copy *source* to *destination* and return the digest of what landed.

    The digest is taken from the destination, not the source, so a truncated
    copy onto a full disk is caught here rather than at reconcile time.

    :raises ValueError: when *expected* is given and does not match.
    """
    ensure_dir(destination.parent)
    temp = destination.with_name(f".{destination.name}.swtmp")
    try:
        shutil.copyfile(source, temp)
        actual = digest_file(temp, algorithm)
        if expected is not None and actual != expected:
            raise ValueError(f"copy of {source} digests to {actual}, expected {expected}")
        temp.replace(destination)
    finally:
        if temp.exists():
            temp.unlink(missing_ok=True)
    return actual


@contextmanager
def scoped_directory(path: Path) -> Iterator[Path]:
    """Create *path*, yield it, and remove it again even if the body raised."""
    ensure_dir(path)
    try:
        yield path
    finally:
        remove_path(path)


__all__ = [
    "REMOVE_ATTEMPTS",
    "REMOVE_BACKOFF_SECONDS",
    "PathEscapeError",
    "atomic_write_bytes",
    "atomic_write_text",
    "copy_with_digest",
    "ensure_dir",
    "is_reserved_name",
    "remove_path",
    "safe_join",
    "scoped_directory",
]
