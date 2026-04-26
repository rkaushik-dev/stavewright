"""How Windows compares the names of things.

Three pieces of the toolkit keep a dictionary keyed on something Windows would
consider the same under two spellings, and each had grown its own folding
function. The in memory target and the snapshot had byte identical copies, and
a fourth would have appeared the next time somebody wrote a target.

Being wrong here is quiet. Two entries for one file where the code expects one
is a reconcile that reports drift against a machine that has not drifted.

What Windows actually does:

Paths compare case insensitively and treat ``\\`` and ``/`` as the same
separator, so both fold to lowercase with forward slashes. A trailing separator
is not part of the name, except on a bare root.

Registry keys compare case insensitively too, but the separator is a backslash
and there is no drive letter, so they fold the other way.

Value names compare case insensitively, and the unnamed value is written
``(Default)`` everywhere a person will read it.

None of this touches the strings the toolkit writes out. A folded name is a
dictionary key, and what goes into a report is what the caller supplied.
"""

from __future__ import annotations

from v4t_setup.model.registry import RegistryRoot
from v4t_setup.target.base import ShortcutSpec

DEFAULT_VALUE_NAME = "(Default)"


def fold_path(path: str) -> str:
    """Fold a file path the way the filesystem compares one.

    A drive letter keeps its colon. An empty path folds to ``/`` rather than to
    the empty string, so that a key is never blank.
    """
    folded = path.replace("\\", "/").rstrip("/").lower()
    return folded or "/"


def fold_key(key: str) -> str:
    """Fold a registry key path. Backslashes, no drive letter, no edges."""
    return key.replace("/", "\\").strip("\\").lower()


def fold_value_name(name: str | None) -> str:
    """Fold a registry value name. ``None`` is the unnamed value."""
    return (name or "").lower()


def shortcut_key(spec: ShortcutSpec) -> str:
    """One key for a shortcut, which is its directory and its name."""
    return f"{fold_path(spec.directory)}/{spec.name.lower()}"


def registry_path(root: RegistryRoot, key: str, name: str | None) -> str:
    """A registry value written the way a person writes one.

    Not folded. This one is for reading, and a snapshot that lowercases every
    key is a snapshot nobody can search.
    """
    return f"{root.hive_name}\\{key}\\{name or DEFAULT_VALUE_NAME}"


__all__ = [
    "DEFAULT_VALUE_NAME",
    "fold_key",
    "fold_path",
    "fold_value_name",
    "registry_path",
    "shortcut_key",
]
