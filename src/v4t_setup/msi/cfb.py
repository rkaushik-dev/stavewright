"""The compound file an MSI actually is.

An MSI is not a format of its own. It is a Microsoft Compound File, the same
container as a ``.doc`` from the same era, with the installer database stored in
it as a set of streams. Getting at the tables means getting through this first.

The layout, in the order this module walks it:

A 512 byte header, starting with the eight byte signature ``D0 CF 11 E0 A1 B1
1A E1``. It gives the sector size as a power of two, the sector where the
directory starts, and the first 109 entries of the DIFAT.

The DIFAT, which says which sectors hold the FAT. 109 entries fit in the header
and the rest live in a chain of their own, which is why a small file needs no
DIFAT sectors at all and this reader still has to handle them.

The FAT, one 32 bit entry per sector, each holding the next sector in that
chain or one of four sentinels. Following it is how a stream longer than one
sector is read.

The directory, a tree of 128 byte entries. The root entry is special: its
"stream" is the mini stream, where everything under 4096 bytes lives, packed
into 64 byte mini sectors with a FAT of their own.

There is no dependency for this on purpose. The alternatives are a C extension
or a package that has not been touched since 2018, and the format is frozen.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path

from v4t_setup.errors import V4TError

SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")

HEADER_SIZE = 512
DIRECTORY_ENTRY_SIZE = 128
DIFAT_IN_HEADER = 109
DIFAT_HEADER_OFFSET = 76

MAXREGSECT = 0xFFFFFFFA
DIFSECT = 0xFFFFFFFC
FATSECT = 0xFFFFFFFD
ENDOFCHAIN = 0xFFFFFFFE
FREESECT = 0xFFFFFFFF
NOSTREAM = 0xFFFFFFFF

MAX_SECTOR_SHIFT = 16
"""A sector of 64KB. Nothing legitimate goes near it, and a corrupt shift of
31 would otherwise ask for a two gigabyte read."""


class CompoundFileError(V4TError):
    """The file is not a compound file, or is one that has been damaged."""

    code = "CFB0001"


class EntryType(IntEnum):
    """What a directory entry describes."""

    EMPTY = 0
    STORAGE = 1
    STREAM = 2
    ROOT = 5


@dataclass(frozen=True, slots=True)
class DirectoryEntry:
    """One entry in the directory tree."""

    index: int
    name: str
    kind: EntryType
    start_sector: int
    size: int
    left: int
    right: int
    child: int

    @property
    def is_stream(self) -> bool:
        return self.kind is EntryType.STREAM

    @property
    def is_storage(self) -> bool:
        return self.kind in (EntryType.STORAGE, EntryType.ROOT)


def _uint32(raw: bytes, offset: int) -> int:
    return int(struct.unpack_from("<I", raw, offset)[0])


class CompoundFile:
    """A compound file opened from bytes in memory.

    An MSI is a few megabytes, so it is read whole rather than seeked around.
    That removes a whole class of half read stream from the picture and makes
    every failure here a failure about the bytes rather than about the file
    handle.
    """

    def __init__(self, raw: bytes) -> None:
        if len(raw) < HEADER_SIZE:
            raise CompoundFileError(
                "file is too short to be a compound file",
                context={"bytes": len(raw)},
            )
        if raw[:8] != SIGNATURE:
            raise CompoundFileError(
                "file does not start with the compound file signature",
                context={"found": raw[:8].hex()},
            )
        self.raw = raw

        shift = int(struct.unpack_from("<H", raw, 30)[0])
        mini_shift = int(struct.unpack_from("<H", raw, 32)[0])
        if not 6 <= shift <= MAX_SECTOR_SHIFT or not 2 <= mini_shift < shift:
            raise CompoundFileError(
                "the sector sizes in the header are not usable",
                context={"sector_shift": shift, "mini_sector_shift": mini_shift},
            )
        self.sector_size = 1 << shift
        self.mini_sector_size = 1 << mini_shift
        self.mini_cutoff = _uint32(raw, 56)

        self._first_directory = _uint32(raw, 48)
        self._first_minifat = _uint32(raw, 60)
        self._minifat_count = _uint32(raw, 64)
        self._first_difat = _uint32(raw, 68)
        self._difat_count = _uint32(raw, 72)

        self._fat = self._read_fat()
        self._entries = self._read_directory()
        self._minifat = self._read_minifat()
        self._mini_stream = self._read_mini_stream()

    @property
    def sector_count(self) -> int:
        """How many sectors the file has after the header."""
        return max(0, (len(self.raw) - HEADER_SIZE) // self.sector_size)

    def _sector(self, number: int) -> bytes:
        if number > MAXREGSECT:
            raise CompoundFileError(
                "a chain asked for a reserved sector number",
                context={"sector": number},
            )
        start = (number + 1) * self.sector_size
        end = start + self.sector_size
        if end > len(self.raw):
            raise CompoundFileError(
                "a chain runs off the end of the file",
                context={"sector": number, "wanted": end, "bytes": len(self.raw)},
            )
        return self.raw[start:end]

    def _difat(self) -> list[int]:
        entries = [
            _uint32(self.raw, DIFAT_HEADER_OFFSET + 4 * index)
            for index in range(DIFAT_IN_HEADER)
        ]
        sector = self._first_difat
        per_sector = self.sector_size // 4
        seen = 0
        while sector != ENDOFCHAIN and sector != FREESECT:
            if seen > self._difat_count + 1:
                raise CompoundFileError(
                    "the DIFAT chain is longer than the header says",
                    context={"declared": self._difat_count},
                )
            block = self._sector(sector)
            entries.extend(_uint32(block, 4 * index) for index in range(per_sector - 1))
            sector = _uint32(block, 4 * (per_sector - 1))
            seen += 1
        return [entry for entry in entries if entry <= MAXREGSECT]

    def _read_fat(self) -> list[int]:
        fat: list[int] = []
        per_sector = self.sector_size // 4
        for sector in self._difat():
            block = self._sector(sector)
            fat.extend(_uint32(block, 4 * index) for index in range(per_sector))
        return fat

    def _chain(self, start: int) -> Iterator[int]:
        """The sectors of one stream, in order.

        :raises CompoundFileError: when the chain loops, which is what a
            damaged FAT looks like from here and would otherwise hang.
        """
        seen: set[int] = set()
        sector = start
        while sector != ENDOFCHAIN and sector != FREESECT:
            if sector in seen:
                raise CompoundFileError(
                    "a sector chain loops back on itself",
                    context={"sector": sector, "start": start},
                )
            if sector >= len(self._fat):
                raise CompoundFileError(
                    "a chain leaves the allocation table",
                    context={"sector": sector, "fat_entries": len(self._fat)},
                )
            seen.add(sector)
            yield sector
            sector = self._fat[sector]

    def _read_chain(self, start: int, size: int | None = None) -> bytes:
        data = b"".join(self._sector(number) for number in self._chain(start))
        return data if size is None else data[:size]

    def _read_directory(self) -> list[DirectoryEntry]:
        raw = self._read_chain(self._first_directory)
        entries: list[DirectoryEntry] = []
        for index in range(len(raw) // DIRECTORY_ENTRY_SIZE):
            entry = self._parse_entry(raw, index)
            if entry is not None:
                entries.append(entry)
        if not entries or entries[0].kind is not EntryType.ROOT:
            raise CompoundFileError("the directory has no root entry")
        return entries

    def _parse_entry(self, raw: bytes, index: int) -> DirectoryEntry | None:
        base = index * DIRECTORY_ENTRY_SIZE
        name_length = int(struct.unpack_from("<H", raw, base + 64)[0])
        kind_value = raw[base + 66]
        if kind_value == EntryType.EMPTY:
            return None
        try:
            kind = EntryType(kind_value)
        except ValueError:
            raise CompoundFileError(
                "a directory entry has a type this reader does not know",
                context={"index": index, "type": kind_value},
            ) from None
        # The length counts bytes and includes the terminating null.
        name = raw[base : base + max(0, name_length - 2)].decode("utf-16-le", "replace")
        return DirectoryEntry(
            index=index,
            name=name,
            kind=kind,
            start_sector=_uint32(raw, base + 116),
            size=int(struct.unpack_from("<Q", raw, base + 120)[0]),
            left=_uint32(raw, base + 68),
            right=_uint32(raw, base + 72),
            child=_uint32(raw, base + 76),
        )

    def _read_minifat(self) -> list[int]:
        if self._first_minifat in (ENDOFCHAIN, FREESECT):
            return []
        raw = self._read_chain(self._first_minifat)
        return [_uint32(raw, 4 * index) for index in range(len(raw) // 4)]

    def _read_mini_stream(self) -> bytes:
        root = self._entries[0]
        if root.start_sector in (ENDOFCHAIN, FREESECT) or root.size == 0:
            return b""
        return self._read_chain(root.start_sector, root.size)

    def _read_mini_chain(self, start: int, size: int) -> bytes:
        pieces: list[bytes] = []
        seen: set[int] = set()
        sector = start
        while sector != ENDOFCHAIN and sector != FREESECT and len(b"".join(pieces)) < size:
            if sector in seen:
                raise CompoundFileError(
                    "a mini sector chain loops back on itself",
                    context={"sector": sector, "start": start},
                )
            if sector >= len(self._minifat):
                raise CompoundFileError(
                    "a mini chain leaves the mini allocation table",
                    context={"sector": sector, "entries": len(self._minifat)},
                )
            seen.add(sector)
            offset = sector * self.mini_sector_size
            pieces.append(self._mini_stream[offset : offset + self.mini_sector_size])
            sector = self._minifat[sector]
        return b"".join(pieces)[:size]

    @property
    def entries(self) -> list[DirectoryEntry]:
        """Every directory entry, in file order. The root is first."""
        return list(self._entries)

    @property
    def names(self) -> list[str]:
        """Every stream name, sorted. The root storage is not one."""
        return sorted(entry.name for entry in self._entries if entry.is_stream)

    def find(self, name: str) -> DirectoryEntry | None:
        for entry in self._entries:
            if entry.name == name and entry.is_stream:
                return entry
        return None

    def __contains__(self, name: str) -> bool:
        return self.find(name) is not None

    def __len__(self) -> int:
        return sum(1 for entry in self._entries if entry.is_stream)

    def read(self, name: str) -> bytes:
        """One stream, whole.

        :raises CompoundFileError: when there is no such stream.
        """
        entry = self.find(name)
        if entry is None:
            raise CompoundFileError(
                "no such stream in this compound file",
                context={"name": name, "streams": len(self)},
            )
        return self.read_entry(entry)

    def read_entry(self, entry: DirectoryEntry) -> bytes:
        """One stream, by its directory entry."""
        if entry.size == 0:
            return b""
        if entry.size < self.mini_cutoff:
            return self._read_mini_chain(entry.start_sector, entry.size)
        return self._read_chain(entry.start_sector, entry.size)


def read_compound_file(path: Path) -> CompoundFile:
    """Open a compound file.

    :raises CompoundFileError: when the file cannot be read, or is not one.
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CompoundFileError(
            "cannot read compound file",
            context={"path": str(path), "detail": str(exc)},
        ) from exc
    return CompoundFile(raw)


def is_compound_file(path: Path) -> bool:
    """Whether *path* starts with the compound file signature.

    Cheap enough to call on every file in a directory. Reads eight bytes.
    """
    try:
        with path.open("rb") as handle:
            return handle.read(8) == SIGNATURE
    except OSError:
        return False


__all__ = [
    "DIFAT_IN_HEADER",
    "ENDOFCHAIN",
    "FREESECT",
    "HEADER_SIZE",
    "SIGNATURE",
    "CompoundFile",
    "CompoundFileError",
    "DirectoryEntry",
    "EntryType",
    "is_compound_file",
    "read_compound_file",
]
