"""The summary information stream, which is where an MSI says what it is.

``\\x05SummaryInformation`` is an OLE property set, the same thing a Word
document from 1997 carries, and the installer borrows it rather than inventing
a header. It is the only part of an MSI that Explorer can read, which is why
the fields have names like Subject and Keywords holding things that are not
subjects or keywords.

What each field actually means to an installer:

Revision Number
    the package code. A GUID that must change on every rebuild, and it is the
    one identifier that says two files are the same bytes rather than the same
    product. Two installers with the same package code and different content
    are a supported way to make Windows do the wrong thing.
Template
    platform and languages, semicolon separated: ``Intel;1033``. ``x64`` there
    instead of ``Intel`` means a 64 bit package, which a 32 bit line PC will
    refuse with a message nobody reads.
Page Count
    the schema version, not a page count. 200 is Windows Installer 2.0.
Word Count
    a bit field about the source, not a word count. Bit 0 says the source is
    uncompressed, bit 1 that it is an administrative image, bit 2 that the
    install is elevated, bit 3 that it is a patch.
Subject and Author
    the product name and the manufacturer, duplicated from the Property table.
    They can disagree with it, and when they do it is the Property table that
    the installer believes.

The layout is a header, a list of section GUIDs and offsets, and then one
section: a byte count, a property count, and pairs of property id and offset.
Every offset in the section is relative to the section, not to the stream, and
getting that wrong reads plausible garbage rather than failing.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from v4t_setup.msi.cfb import CompoundFile
from v4t_setup.msi.tables import MsiError

STREAM_NAME = "\x05SummaryInformation"

BYTE_ORDER_MARK = 0xFFFE
HEADER_SIZE = 28
SECTION_LIST_ENTRY = 20

VT_EMPTY = 0
VT_NULL = 1
VT_I2 = 2
VT_I4 = 3
VT_LPSTR = 30
VT_FILETIME = 64

FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=UTC)
FILETIME_TICKS_PER_MICROSECOND = 10
"""A FILETIME counts 100 nanosecond ticks, and timedelta stops at
microseconds. Dividing in the other order, as ``timedelta(microseconds=1) /
10``, produces a zero timedelta and every timestamp in the file comes back as
1601-01-01, which reads exactly like an unset one."""

PID_CODEPAGE = 1
PID_TITLE = 2
PID_SUBJECT = 3
PID_AUTHOR = 4
PID_KEYWORDS = 5
PID_COMMENTS = 6
PID_TEMPLATE = 7
PID_LAST_SAVED_BY = 8
PID_REVISION_NUMBER = 9
PID_LAST_PRINTED = 11
PID_CREATED = 12
PID_LAST_SAVED = 13
PID_PAGE_COUNT = 14
PID_WORD_COUNT = 15
PID_CREATING_APPLICATION = 18
PID_SECURITY = 19

SOURCE_UNCOMPRESSED = 0x0001
SOURCE_ADMIN_IMAGE = 0x0002
SOURCE_ELEVATED = 0x0004
SOURCE_PATCH = 0x0008

PropertyValue = str | int | datetime | None


@dataclass(frozen=True, slots=True)
class SummaryInformation:
    """What the summary stream says, with the fields under useful names."""

    properties: dict[int, PropertyValue]

    def __len__(self) -> int:
        return len(self.properties)

    def __contains__(self, pid: int) -> bool:
        return pid in self.properties

    def _text(self, pid: int) -> str | None:
        value = self.properties.get(pid)
        return value if isinstance(value, str) else None

    def _number(self, pid: int) -> int | None:
        value = self.properties.get(pid)
        return value if isinstance(value, int) else None

    def _moment(self, pid: int) -> datetime | None:
        value = self.properties.get(pid)
        return value if isinstance(value, datetime) else None

    @property
    def codepage(self) -> int | None:
        return self._number(PID_CODEPAGE)

    @property
    def title(self) -> str | None:
        return self._text(PID_TITLE)

    @property
    def subject(self) -> str | None:
        return self._text(PID_SUBJECT)

    @property
    def product_name(self) -> str | None:
        """The product name, wherever this build put it.

        The specification says Subject. The installer in this repository leaves
        Subject empty and puts the name in Title, and it is not the only build
        tool that does, so both are checked. An empty string counts as absent:
        every string field in a summary stream is present and empty rather than
        missing, so ``is None`` on its own answers nothing.
        """
        return self.subject or self.title or None

    @property
    def manufacturer(self) -> str | None:
        """Author, likewise."""
        return self._text(PID_AUTHOR)

    @property
    def keywords(self) -> str | None:
        return self._text(PID_KEYWORDS)

    @property
    def comments(self) -> str | None:
        return self._text(PID_COMMENTS)

    @property
    def template(self) -> str | None:
        return self._text(PID_TEMPLATE)

    @property
    def platform(self) -> str | None:
        """The half of Template before the semicolon."""
        template = self.template
        if template is None:
            return None
        return template.split(";", 1)[0] or None

    @property
    def languages(self) -> tuple[int, ...]:
        """The LCIDs after the semicolon, in order. Empty when neutral."""
        template = self.template
        if template is None or ";" not in template:
            return ()
        tail = template.split(";", 1)[1]
        return tuple(int(part) for part in tail.split(",") if part.strip().isdigit())

    @property
    def is_x64(self) -> bool:
        return (self.platform or "").lower() in ("x64", "amd64", "intel64")

    @property
    def package_code(self) -> str | None:
        """Revision Number. Changes on every rebuild, or should."""
        return self._text(PID_REVISION_NUMBER)

    @property
    def schema_version(self) -> int | None:
        """Page Count. 200 is Windows Installer 2.0."""
        return self._number(PID_PAGE_COUNT)

    @property
    def source_flags(self) -> int:
        return self._number(PID_WORD_COUNT) or 0

    @property
    def is_compressed(self) -> bool:
        """True when the payload is in a cabinet rather than loose beside it."""
        return not self.source_flags & SOURCE_UNCOMPRESSED

    @property
    def is_admin_image(self) -> bool:
        return bool(self.source_flags & SOURCE_ADMIN_IMAGE)

    @property
    def is_patch(self) -> bool:
        return bool(self.source_flags & SOURCE_PATCH)

    @property
    def created_at(self) -> datetime | None:
        return self._moment(PID_CREATED)

    @property
    def saved_at(self) -> datetime | None:
        return self._moment(PID_LAST_SAVED)

    @property
    def creating_application(self) -> str | None:
        return self._text(PID_CREATING_APPLICATION)

    @property
    def security(self) -> int | None:
        return self._number(PID_SECURITY)

    def describe(self) -> str:
        parts = [self.product_name or "(no product)", self.package_code or "(no package code)"]
        if self.platform:
            parts.append(self.platform)
        if self.creating_application:
            parts.append(f"built by {self.creating_application}")
        return ", ".join(parts)


def _filetime(raw: int) -> datetime | None:
    if raw == 0:
        return None
    return FILETIME_EPOCH + timedelta(microseconds=raw // FILETIME_TICKS_PER_MICROSECOND)


def _read_value(raw: bytes, offset: int, encoding: str) -> PropertyValue:
    kind = int(struct.unpack_from("<I", raw, offset)[0])
    body = offset + 4
    if kind in (VT_EMPTY, VT_NULL):
        return None
    if kind == VT_I2:
        return int(struct.unpack_from("<h", raw, body)[0])
    if kind == VT_I4:
        return int(struct.unpack_from("<i", raw, body)[0])
    if kind == VT_LPSTR:
        length = int(struct.unpack_from("<I", raw, body)[0])
        text = raw[body + 4 : body + 4 + length]
        return text.split(b"\x00", 1)[0].decode(encoding, "replace")
    if kind == VT_FILETIME:
        return _filetime(int(struct.unpack_from("<Q", raw, body)[0]))
    raise MsiError(
        "the summary stream holds a property type this reader does not know",
        context={"type": kind, "offset": offset},
    )


def parse_summary(raw: bytes) -> SummaryInformation:
    """Read a property set stream.

    :raises MsiError: when the stream is too short, does not start with the
        byte order mark, declares no sections, or holds a property type this
        reader does not know.
    """
    if len(raw) < HEADER_SIZE + SECTION_LIST_ENTRY:
        raise MsiError(
            "the summary stream is too short to hold a property set",
            context={"bytes": len(raw)},
        )
    if int(struct.unpack_from("<H", raw, 0)[0]) != BYTE_ORDER_MARK:
        raise MsiError(
            "the summary stream does not start with a byte order mark",
            context={"found": raw[:2].hex()},
        )
    sections = int(struct.unpack_from("<I", raw, 24)[0])
    if sections < 1:
        raise MsiError("the summary stream declares no sections")

    # Only the first section is the summary information. An MSI never has a
    # second one, and reading it would mean guessing at its property ids.
    section = int(struct.unpack_from("<I", raw, HEADER_SIZE + 16)[0])
    count = int(struct.unpack_from("<I", raw, section + 4)[0])

    # The codepage has to be read first, because every string in the section is
    # in it, and it is itself an entry in the same list.
    offsets: dict[int, int] = {}
    for index in range(count):
        base = section + 8 + index * 8
        pid = int(struct.unpack_from("<I", raw, base)[0])
        offsets[pid] = section + int(struct.unpack_from("<I", raw, base + 4)[0])

    encoding = "cp1252"
    if PID_CODEPAGE in offsets:
        codepage = _read_value(raw, offsets[PID_CODEPAGE], encoding)
        if isinstance(codepage, int) and codepage > 0:
            encoding = "utf-8" if codepage == 65001 else f"cp{codepage & 0xFFFF}"

    properties: dict[int, PropertyValue] = {}
    for pid, offset in offsets.items():
        properties[pid] = _read_value(raw, offset, encoding)
    return SummaryInformation(properties=properties)


def read_summary(source: CompoundFile) -> SummaryInformation:
    """Read the summary stream out of a compound file.

    :raises MsiError: when there is no such stream, which means the file is a
        compound file but not something Windows would install.
    """
    if STREAM_NAME not in source:
        raise MsiError(
            "this compound file has no summary information stream",
            context={"streams": len(source)},
        )
    return parse_summary(source.read(STREAM_NAME))


__all__ = [
    "PID_CREATED",
    "PID_PAGE_COUNT",
    "PID_REVISION_NUMBER",
    "PID_SUBJECT",
    "PID_TEMPLATE",
    "PID_WORD_COUNT",
    "SOURCE_ADMIN_IMAGE",
    "SOURCE_ELEVATED",
    "SOURCE_PATCH",
    "SOURCE_UNCOMPRESSED",
    "STREAM_NAME",
    "PropertyValue",
    "SummaryInformation",
    "parse_summary",
    "read_summary",
]
