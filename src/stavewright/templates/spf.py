"""Reading the ``.spf`` print template format.

The format is not published and we do not have the writer's source, so this
reader is deliberately structural rather than exact. What can be established
from the shipped ``T1.spf`` and held to is:

* The file opens with ``FILE_TEMPLATE_VERSION_02`` as UTF-16LE, zero padded.
* A fourteen character timestamp follows at offset 64, in ``DDMMYYYYHHMMSS``.
* The body is a sequence of blocks, each introduced by the UTF-16LE literal
  ``DATA_BLOCK`` immediately followed by a zero terminated block name.

Everything between one marker and the next is the block's payload, and the
reader keeps it as bytes rather than pretending to understand the struct. That
is enough to answer the questions a commissioning run actually asks: which
blocks are in this template, what fonts do they name, and does the barcode
block reference a field the data file supplies.

Guessing at field widths was tried first and it worked on ``T1.spf`` and on
nothing else. Scanning for the marker is uglier and it survives a template
saved by a different build of the front end.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from stavewright.errors import TemplateError

MAGIC = "FILE_TEMPLATE_VERSION_02"
BLOCK_MARKER = "DATA_BLOCK"
TIMESTAMP_OFFSET = 64
TIMESTAMP_LENGTH = 14
TIMESTAMP_FORMAT = "%d%m%Y%H%M%S"

_MAGIC_BYTES = MAGIC.encode("utf-16-le")
_MARKER_BYTES = BLOCK_MARKER.encode("utf-16-le")

# A run of at least three printable characters stored as UTF-16LE.
_STRING_RUN = re.compile(rb"(?:[\x20-\x7e]\x00){3,}")

# Trailing `_3` on a block name is an instance number, not part of the kind.
_INDEXED_NAME = re.compile(r"^(?P<kind>[A-Z]+)_(?P<index>\d+)$")


def read_utf16_string(raw: bytes, offset: int, limit: int = 256) -> str:
    """Read a zero terminated UTF-16LE string at *offset*.

    :param limit: how many characters to read before giving up, so a corrupt
        file cannot make this walk the whole buffer.
    """
    characters: list[str] = []
    position = offset
    end = min(len(raw), offset + limit * 2)
    while position + 1 < end:
        unit = raw[position : position + 2]
        if unit == b"\x00\x00":
            break
        characters.append(unit.decode("utf-16-le", errors="replace"))
        position += 2
    return "".join(characters)


@dataclass(frozen=True, slots=True)
class SpfBlock:
    """One ``DATA_BLOCK`` and the bytes that follow it."""

    name: str
    offset: int
    payload: bytes = b""

    @property
    def kind(self) -> str:
        """The block name with any instance number removed."""
        match = _INDEXED_NAME.match(self.name)
        return match.group("kind") if match else self.name

    @property
    def index(self) -> int | None:
        """The instance number, when the name carried one."""
        match = _INDEXED_NAME.match(self.name)
        return int(match.group("index")) if match else None

    @property
    def size(self) -> int:
        return len(self.payload)

    def strings(self, minimum: int = 3) -> list[str]:
        """Printable UTF-16LE runs inside the payload, in order.

        This is where a font specification or a barcode symbology name turns
        up. Runs shorter than *minimum* characters are almost always struct
        fields that happen to look like text, so they are dropped.
        """
        found = [
            match.group().decode("utf-16-le") for match in _STRING_RUN.finditer(self.payload)
        ]
        return [text for text in found if len(text) >= minimum]

    def describe(self) -> str:
        suffix = f" #{self.index}" if self.index is not None else ""
        return f"{self.kind}{suffix} at {self.offset} ({self.size} bytes)"


@dataclass
class SpfTemplate:
    """A whole parsed template."""

    magic: str = MAGIC
    saved_at: datetime | None = None
    blocks: list[SpfBlock] = field(default_factory=list)
    size: int = 0
    source: Path | None = None

    def __len__(self) -> int:
        return len(self.blocks)

    def __iter__(self) -> Iterator[SpfBlock]:
        return iter(self.blocks)

    @property
    def kinds(self) -> tuple[str, ...]:
        """The block kinds present, in file order, with duplicates kept."""
        return tuple(block.kind for block in self.blocks)

    def of_kind(self, kind: str) -> list[SpfBlock]:
        """Every block of *kind*, in file order."""
        return [block for block in self.blocks if block.kind == kind]

    def first(self, kind: str) -> SpfBlock | None:
        """The first block of *kind*, or None."""
        blocks = self.of_kind(kind)
        return blocks[0] if blocks else None

    def all_strings(self) -> list[str]:
        """Every printable run across every block."""
        return [text for block in self.blocks for text in block.strings()]

    def describe(self) -> str:
        where = self.source.name if self.source else "(in memory)"
        when = self.saved_at.isoformat(sep=" ") if self.saved_at else "unknown"
        return f"{where}: {len(self.blocks)} blocks, saved {when}"


def _parse_timestamp(raw: bytes) -> datetime | None:
    text = read_utf16_string(raw, TIMESTAMP_OFFSET, limit=TIMESTAMP_LENGTH)
    if len(text) != TIMESTAMP_LENGTH or not text.isdigit():
        return None
    try:
        return datetime.strptime(text, TIMESTAMP_FORMAT)
    except ValueError:
        return None


def parse_spf(raw: bytes, *, source: Path | None = None) -> SpfTemplate:
    """Parse a template from bytes.

    :raises TemplateError: when the magic is absent, which means this is not a
        template rather than a template we cannot read.
    """
    if len(raw) < len(_MAGIC_BYTES):
        raise TemplateError(
            "file is too short to be a template",
            context={"size": len(raw), "source": str(source) if source else None},
        )
    if not raw.startswith(_MAGIC_BYTES):
        raise TemplateError(
            "file does not start with the template magic",
            context={
                "expected": MAGIC,
                "found": read_utf16_string(raw, 0, limit=len(MAGIC)),
                "source": str(source) if source else None,
            },
        )

    starts = [match.start() for match in re.finditer(re.escape(_MARKER_BYTES), raw)]
    blocks: list[SpfBlock] = []
    for position, start in enumerate(starts):
        name_at = start + len(_MARKER_BYTES)
        name = read_utf16_string(raw, name_at)
        payload_at = name_at + len(name.encode("utf-16-le"))
        # Step over the name's own terminator so the payload does not open with
        # two stray null bytes. A name that ran to the end of the buffer has no
        # terminator to skip.
        if raw[payload_at : payload_at + 2] == b"\x00\x00":
            payload_at += 2
        payload_end = starts[position + 1] if position + 1 < len(starts) else len(raw)
        blocks.append(
            SpfBlock(name=name, offset=start, payload=raw[payload_at:payload_end])
        )

    return SpfTemplate(
        magic=MAGIC,
        saved_at=_parse_timestamp(raw),
        blocks=blocks,
        size=len(raw),
        source=source,
    )


def read_spf(path: Path) -> SpfTemplate:
    """Read and parse a template from disk.

    :raises TemplateError: when the file cannot be read or is not a template.
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise TemplateError(
            "cannot read template",
            context={"path": str(path), "detail": str(exc)},
        ) from exc
    return parse_spf(raw, source=path)


def is_template(path: Path) -> bool:
    """True when *path* starts with the template magic. Reads 48 bytes."""
    try:
        with path.open("rb") as handle:
            return handle.read(len(_MAGIC_BYTES)) == _MAGIC_BYTES
    except OSError:
        return False


__all__ = [
    "BLOCK_MARKER",
    "MAGIC",
    "TIMESTAMP_FORMAT",
    "TIMESTAMP_LENGTH",
    "TIMESTAMP_OFFSET",
    "SpfBlock",
    "SpfTemplate",
    "is_template",
    "parse_spf",
    "read_spf",
    "read_utf16_string",
]
