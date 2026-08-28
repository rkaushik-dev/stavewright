"""The encoding MSI uses for the names of its streams.

A compound file directory entry has 31 usable characters and MSI table names
are longer than that, so the installer packs them. Three base 64 digits go into
two UTF-16 code units, in a private range chosen so the packed name is still a
legal directory entry name.

The alphabet is ``0-9 A-Z a-z . _`` in that order, sixty four characters, and
the ranges are:

``U+3800`` to ``U+47FF``
    two digits, low six bits first.
``U+4800`` to ``U+483F``
    one digit.
``U+4840``
    not a digit at all. It is the marker that says this stream is a table, and
    it only ever appears first. Everything that talks about MSI writes it as
    ``!``, so this module does too.

Anything else is itself. That is how ``\\x05SummaryInformation`` survives: the
leading ``\\x05`` and the ASCII are all outside the packed ranges.

Getting this wrong is quiet rather than loud. A wrong alphabet still decodes to
something printable, and the stream that ought to be ``!Directory`` comes out as
``!Direcupsy`` and is simply reported as missing.

One asymmetry, and it is the format's rather than this module's. Decoding
always undoes encoding, so ``decode(encode(name)) == name`` for any name. The
other way round does not hold everywhere, because the installer does not pack
every stream it writes. ``\\x05SummaryInformation`` is a plain OLE property set
stored under its plain name, and encoding that name produces the packed form,
which is a real name the file does not have. So decode what the file gives you,
and only encode a name you are about to look up as a table.
"""

from __future__ import annotations

ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz._"

TWO_DIGIT_BASE = 0x3800
ONE_DIGIT_BASE = 0x4800
TABLE_MARKER = "䡀"
TABLE_PREFIX = "!"

_VALUES = {character: index for index, character in enumerate(ALPHABET)}


def decode_stream_name(name: str) -> str:
    """Unpack one directory entry name.

    A table stream comes back with a leading ``!``. Characters outside the
    packed ranges are passed through, so an ordinary name is returned as it is.
    """
    out: list[str] = []
    for character in name:
        code = ord(character)
        if code == ord(TABLE_MARKER):
            out.append(TABLE_PREFIX)
        elif ONE_DIGIT_BASE <= code < ord(TABLE_MARKER):
            out.append(ALPHABET[code - ONE_DIGIT_BASE])
        elif TWO_DIGIT_BASE <= code < ONE_DIGIT_BASE:
            packed = code - TWO_DIGIT_BASE
            out.append(ALPHABET[packed & 0x3F])
            out.append(ALPHABET[(packed >> 6) & 0x3F])
        else:
            out.append(character)
    return "".join(out)


def encode_stream_name(name: str) -> str:
    """Pack a name back the way the installer wrote it.

    A leading ``!`` becomes the table marker. Characters outside the alphabet
    are passed through, which is what makes the summary information stream
    round trip.
    """
    out: list[str] = []
    rest = name
    if rest.startswith(TABLE_PREFIX):
        out.append(TABLE_MARKER)
        rest = rest[1:]

    position = 0
    while position < len(rest):
        first = _VALUES.get(rest[position])
        if first is None:
            out.append(rest[position])
            position += 1
            continue
        second = _VALUES.get(rest[position + 1]) if position + 1 < len(rest) else None
        if second is None:
            out.append(chr(ONE_DIGIT_BASE + first))
            position += 1
        else:
            out.append(chr(TWO_DIGIT_BASE + first + (second << 6)))
            position += 2
    return "".join(out)


def is_table_stream(name: str) -> bool:
    """Whether an encoded name is a table stream."""
    return name.startswith(TABLE_MARKER)


def table_name(name: str) -> str | None:
    """The table an encoded stream name holds, or None if it is not a table."""
    if not is_table_stream(name):
        return None
    return decode_stream_name(name)[1:]


__all__ = [
    "ALPHABET",
    "ONE_DIGIT_BASE",
    "TABLE_MARKER",
    "TABLE_PREFIX",
    "TWO_DIGIT_BASE",
    "decode_stream_name",
    "encode_stream_name",
    "is_table_stream",
    "table_name",
]
