"""Turning the MSI directory table into real paths on a real terminal.

The AIP stores directories as a parent chain, not as paths. ``FpgaImage_Dir``
has parent ``Quattro_Dir``, which has parent ``Matricode_Dir``, which has parent
``WindowsVolume``. Resolving that against a profile is what produces
``C:\\Sellenis\\S-jet600\\FpgaImage``.

Seven names never resolve through their parent because the installer supplies
them. Those are the pseudo roots, flagged ``IsPseudoRoot="1"`` in the project
file, and they come straight off the profile instead.

The one that bites is ``SystemFolder``. On a 64 bit terminal the file system
redirector sends a 32 bit component there to ``SysWOW64``, so the same row
resolves to two different directories depending on whether the component that
uses it is marked 64 bit. :meth:`DirectoryTable.resolve` takes *native* for
exactly that case.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PureWindowsPath

from v4t_setup.errors import DirectoryCycleError, DirectoryResolutionError
from v4t_setup.model.profile import TerminalProfile
from v4t_setup.util.shortname import DefaultDir, parse_default_dir

MAX_DEPTH = 32
"""Deepest parent chain we will walk before calling it a cycle."""

PSEUDO_ROOTS = frozenset(
    {
        "TARGETDIR",
        "APPDIR",
        "WindowsVolume",
        "SystemFolder",
        "System64Folder",
        "DesktopFolder",
        "SHORTCUTDIR",
    }
)
"""Names the installer supplies rather than deriving from a parent."""

REDIRECTED_ROOT = "SystemFolder"
"""The only pseudo root whose meaning depends on the component using it."""


@dataclass(frozen=True, slots=True)
class DirectoryRow:
    """One row of the MSI ``Directory`` table.

    :param directory: the row key, such as ``Quattro_Dir``.
    :param default_dir: the packed name cell.
    :param parent: the key of the parent row, or None for a root.
    :param is_pseudo_root: mirrors ``IsPseudoRoot`` in the project file.
    """

    directory: str
    default_dir: DefaultDir
    parent: str | None = None
    is_pseudo_root: bool = False

    @classmethod
    def from_cells(
        cls,
        directory: str,
        default_dir: str,
        parent: str | None = None,
        is_pseudo_root: bool = False,
    ) -> DirectoryRow:
        """Build a row from the raw strings an AIP row carries."""
        return cls(
            directory=directory,
            default_dir=parse_default_dir(default_dir),
            parent=parent or None,
            is_pseudo_root=is_pseudo_root,
        )

    @property
    def target_name(self) -> str:
        """The folder name this row contributes to a resolved path."""
        return self.default_dir.target_name

    @property
    def source_name(self) -> str:
        """The folder name this row has in the build tree."""
        return self.default_dir.source_name


@dataclass
class DirectoryTable:
    """The whole directory table, resolvable against a profile."""

    rows: dict[str, DirectoryRow] = field(default_factory=dict)

    @classmethod
    def from_rows(cls, rows: list[DirectoryRow]) -> DirectoryTable:
        """Build a table, refusing duplicate keys.

        :raises DirectoryResolutionError: on a repeated directory key.
        """
        table: dict[str, DirectoryRow] = {}
        for row in rows:
            if row.directory in table:
                raise DirectoryResolutionError(
                    "directory key appears twice",
                    context={"directory": row.directory},
                )
            table[row.directory] = row
        return cls(rows=table)

    def __contains__(self, directory: str) -> bool:
        return directory in self.rows

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, directory: str) -> DirectoryRow:
        try:
            return self.rows[directory]
        except KeyError:
            raise DirectoryResolutionError(
                "no such directory row",
                context={"directory": directory},
            ) from None

    def keys(self) -> list[str]:
        """Directory keys in sorted order, so reports are stable."""
        return sorted(self.rows)

    def _pseudo_root_path(
        self,
        directory: str,
        profile: TerminalProfile,
        native: bool,
    ) -> PureWindowsPath:
        if directory == "SystemFolder":
            return profile.system64_folder if native else profile.system_folder
        mapping = {
            "TARGETDIR": profile.windows_volume,
            "APPDIR": profile.install_root,
            "WindowsVolume": profile.windows_volume,
            "System64Folder": profile.system64_folder,
            "DesktopFolder": profile.desktop_folder,
            "SHORTCUTDIR": profile.shortcut_folder,
        }
        try:
            return mapping[directory]
        except KeyError:
            raise DirectoryResolutionError(
                "row is flagged as a pseudo root but the profile has no path for it",
                context={"directory": directory},
            ) from None

    def is_pseudo_root(self, directory: str) -> bool:
        """True when *directory* is supplied by the installer rather than derived."""
        row = self.rows.get(directory)
        if row is not None and row.is_pseudo_root:
            return True
        return directory in PSEUDO_ROOTS

    def resolve(
        self,
        directory: str,
        profile: TerminalProfile,
        *,
        native: bool = False,
    ) -> PureWindowsPath:
        """Resolve one directory key to an absolute path on the terminal.

        :param native: set for a component marked 64 bit, so ``SystemFolder``
            stops being redirected to ``SysWOW64``.
        :raises DirectoryResolutionError: for an unknown key or a missing parent.
        :raises DirectoryCycleError: when the parent chain loops.
        """
        chain: list[str] = []
        seen: set[str] = set()
        current: str | None = directory

        while current is not None:
            if current in seen:
                raise DirectoryCycleError(
                    "directory parent chain loops",
                    context={"directory": directory, "cycle": " -> ".join([*chain, current])},
                )
            seen.add(current)
            chain.append(current)
            if len(chain) > MAX_DEPTH:
                raise DirectoryCycleError(
                    "directory parent chain is deeper than the limit",
                    context={"directory": directory, "limit": MAX_DEPTH},
                )
            if self.is_pseudo_root(current):
                break
            row = self.rows.get(current)
            if row is None:
                raise DirectoryResolutionError(
                    "parent directory is not in the table",
                    context={"directory": directory, "missing": current},
                )
            current = row.parent
            if current is None:
                raise DirectoryResolutionError(
                    "directory chain ends without reaching a pseudo root",
                    context={"directory": directory, "ended_at": chain[-1]},
                )

        root = chain[-1]
        path = self._pseudo_root_path(root, profile, native)
        # Everything above the root, walked back down, contributes a segment.
        for key in reversed(chain[:-1]):
            path = path / self[key].target_name
        return path

    def resolve_all(
        self,
        profile: TerminalProfile,
        *,
        native: bool = False,
    ) -> dict[str, PureWindowsPath]:
        """Resolve every row. Useful for a plan dump or a drift report."""
        return {key: self.resolve(key, profile, native=native) for key in self.keys()}

    def source_path(self, directory: str, root: PureWindowsPath) -> PureWindowsPath:
        """Where *directory* lives in the build tree, under *root*.

        Stops at the first row whose cell carries the ``:.`` sentinel, because
        that is the AIP saying "this is the project directory".
        """
        segments: list[str] = []
        seen: set[str] = set()
        current: str | None = directory
        while current is not None:
            if current in seen:
                raise DirectoryCycleError(
                    "directory parent chain loops",
                    context={"directory": directory},
                )
            seen.add(current)
            row = self[current]
            if row.default_dir.is_source_root or self.is_pseudo_root(current):
                break
            segments.append(row.source_name)
            current = row.parent
        path = root
        for segment in reversed(segments):
            path = path / segment
        return path

    def children_of(self, directory: str) -> list[str]:
        """Immediate children of *directory*, sorted."""
        return sorted(key for key, row in self.rows.items() if row.parent == directory)

    def orphans(self) -> list[str]:
        """Rows whose parent key is not in the table and is not a pseudo root."""
        missing = []
        for key, row in self.rows.items():
            if row.parent is None or row.is_pseudo_root:
                continue
            if row.parent not in self.rows and row.parent not in PSEUDO_ROOTS:
                missing.append(key)
        return sorted(missing)


__all__ = [
    "MAX_DEPTH",
    "PSEUDO_ROOTS",
    "REDIRECTED_ROOT",
    "DirectoryRow",
    "DirectoryTable",
]
