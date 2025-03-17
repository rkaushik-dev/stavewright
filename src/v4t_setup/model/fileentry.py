"""Rows of the MSI ``File`` table.

The ``FileName`` cell uses the same packed short and long form the directory
table uses, so ``LANGCH~1.DLL|LangChinese.dll`` is one cell and not two columns.

``SelfReg`` is the interesting one. Advanced Installer writes ``false`` against
both OCX rows even though the controls do need registering, because the project
registers them out of band rather than through the MSI ``SelfReg`` table. That
is the reason ``ocx/reg.bat`` exists in this repository at all, and the reason
the planner cannot work out COM registration from the file rows alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntFlag
from pathlib import PurePosixPath, PureWindowsPath

from v4t_setup.errors import ManifestError
from v4t_setup.util.shortname import DefaultDir, parse_default_dir


class FileAttributes(IntFlag):
    """Bit values from the MSI ``File`` table ``Attributes`` column."""

    NONE = 0
    READ_ONLY = 1
    HIDDEN = 2
    SYSTEM = 4
    VITAL = 512
    CHECKSUM = 1024
    PATCH_ADDED = 4096
    NON_COMPRESSED = 8192
    COMPRESSED = 16384


@dataclass(frozen=True, slots=True)
class FileEntry:
    """One row of the MSI ``File`` table.

    :param file: the row key, which other tables reference. Note that this is
        not the file name and often differs from it.
    :param component: the component this file belongs to.
    :param file_name: the packed short and long name cell.
    :param source_path: where the file comes from in the build tree, relative
        to the project root.
    """

    file: str
    component: str
    file_name: DefaultDir
    source_path: PureWindowsPath | None = None
    attributes: FileAttributes = FileAttributes.NONE
    self_reg: bool = False
    version: str | None = None
    language: str | None = None
    sequence: int | None = None

    def __post_init__(self) -> None:
        if not self.file:
            raise ManifestError("file row has no key")
        if not self.component:
            raise ManifestError("file row names no component", context={"file": self.file})

    @classmethod
    def from_cells(
        cls,
        file: str,
        component: str,
        file_name: str,
        source_path: str | None = None,
        attributes: int = 0,
        self_reg: bool = False,
        version: str | None = None,
        language: str | None = None,
        sequence: int | None = None,
    ) -> FileEntry:
        """Build a row from the raw strings an AIP row carries."""
        return cls(
            file=file,
            component=component,
            file_name=parse_default_dir(file_name),
            source_path=PureWindowsPath(source_path) if source_path else None,
            attributes=FileAttributes(attributes),
            self_reg=self_reg,
            version=version or None,
            language=language or None,
            sequence=sequence,
        )

    @property
    def target_name(self) -> str:
        """The name the file gets on the terminal."""
        return self.file_name.target_name

    @property
    def short_name(self) -> str:
        """The 8.3 name written into the package."""
        return self.file_name.target_short

    @property
    def has_long_name(self) -> bool:
        """True when the cell carried both forms."""
        return self.file_name.target_long is not None

    @property
    def extension(self) -> str:
        """Lower cased extension of the target name, without the dot."""
        _, _, suffix = self.target_name.rpartition(".")
        return suffix.lower() if "." in self.target_name else ""

    @property
    def is_vital(self) -> bool:
        """True when a failure to install this file fails the whole install."""
        return bool(self.attributes & FileAttributes.VITAL)

    @property
    def is_versioned(self) -> bool:
        """True when the file carries a version resource the installer compares."""
        return self.version is not None

    @property
    def looks_like_a_com_server(self) -> bool:
        """True for the extensions that normally need registering.

        Only a hint. ``SelfReg`` in this project is false on both OCX rows, so
        the planner takes COM registration from the manifest rather than
        guessing from here.
        """
        return self.extension in {"ocx", "dll", "exe"}

    def source_relative(self) -> PurePosixPath | None:
        """The source path as posix, for a manifest that has to be portable."""
        if self.source_path is None:
            return None
        return PurePosixPath(self.source_path.as_posix())

    def describe(self) -> str:
        """One line for a plan or drift report."""
        source = self.source_path.as_posix() if self.source_path else "(no source)"
        return f"{self.file} -> {self.target_name} from {source}"


def index_by_component(entries: list[FileEntry]) -> dict[str, list[FileEntry]]:
    """Group file rows by the component that owns them, each group sorted by key."""
    grouped: dict[str, list[FileEntry]] = {}
    for entry in entries:
        grouped.setdefault(entry.component, []).append(entry)
    for group in grouped.values():
        group.sort(key=lambda entry: entry.file)
    return grouped


def duplicate_target_names(entries: list[FileEntry]) -> dict[str, list[str]]:
    """File keys that write the same target name, grouped by that name.

    Two rows sharing a name is legitimate when they go to different directories,
    which is exactly what the OCX pair does. It is a bug when they do not, so
    the caller resolves the directory before deciding.
    """
    by_name: dict[str, list[str]] = {}
    for entry in entries:
        by_name.setdefault(entry.target_name, []).append(entry.file)
    return {name: sorted(keys) for name, keys in by_name.items() if len(keys) > 1}


__all__ = [
    "FileAttributes",
    "FileEntry",
    "duplicate_target_names",
    "index_by_component",
]
