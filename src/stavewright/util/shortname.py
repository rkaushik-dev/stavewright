"""MSI ``DefaultDir`` parsing and 8.3 short name generation.

Every row in the AIP directory table stores its name in the ``DefaultDir``
format, which packs up to four names into one string::

    target_short|target_long:source_short|source_long

Both halves are optional. ``Images`` is a bare name used for target and source
alike. ``DATAFI~1|Data Files`` gives a short and a long target name and no
source override. ``APPDIR:.`` gives a target of ``APPDIR`` and the source
sentinel ``.`` meaning "the directory the AIP lives in".

The short name half is not decoration. When a terminal image is built for an
older line PC the installer still writes 8.3 names into the file table, and a
mismatch between the name in the package and the name on disk is one of the
ways a repair install silently reinstalls nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SHORT_NAME_PATTERN = re.compile(
    r"^[^\\/:*?\"<>|+,;=\[\] .]{1,8}(\.[^\\/:*?\"<>|+,;=\[\] .]{1,3})?$"
)
"""What MSI accepts in the short half of a ``DefaultDir``.

The dot is excluded from both character classes on purpose. Without that the
pattern happily matches ``a.abcd``, because the stem class would swallow the
separator and the four character extension along with it.
"""

LONG_NAME_PATTERN = re.compile(r"^[^\\/:*?\"<>|]{1,255}$")
"""What MSI accepts in the long half. Wider, but still not anything."""

_ILLEGAL_IN_SHORT = set('\\/:*?"<>|+,;=[] ')

SOURCE_IS_TARGET = "."
"""The ``DefaultDir`` sentinel meaning the source directory is the AIP root."""


@dataclass(frozen=True, slots=True)
class DefaultDir:
    """The parsed form of one ``DefaultDir`` cell.

    ``target_long`` and ``source_long`` fall back to their short counterpart
    when the cell did not carry a long name, so callers can read
    :attr:`target_name` without worrying about which form was stored.
    """

    target_short: str
    target_long: str | None = None
    source_short: str | None = None
    source_long: str | None = None

    @property
    def target_name(self) -> str:
        """The name the directory gets on the terminal."""
        return self.target_long or self.target_short

    @property
    def source_name(self) -> str:
        """The name the directory has in the build tree."""
        if self.source_long:
            return self.source_long
        if self.source_short:
            return self.source_short
        return self.target_name

    @property
    def has_source_override(self) -> bool:
        """True when the build tree and the terminal disagree about the name."""
        return self.source_short is not None or self.source_long is not None

    @property
    def is_source_root(self) -> bool:
        """True for the ``:.`` form used by pseudo roots such as ``APPDIR``."""
        return self.source_short == SOURCE_IS_TARGET

    def __str__(self) -> str:
        return format_default_dir(self)


def _split_pair(text: str) -> tuple[str, str | None]:
    short, sep, long_name = text.partition("|")
    if not sep:
        return short, None
    if not long_name:
        raise ValueError(f"DefaultDir has an empty long name: {text!r}")
    return short, long_name


def parse_default_dir(text: str) -> DefaultDir:
    """Parse one ``DefaultDir`` cell.

    :raises ValueError: when the cell is empty, has more than one ``:`` or
        carries an empty half.
    """
    if not text:
        raise ValueError("DefaultDir is empty")
    if text.count(":") > 1:
        raise ValueError(f"DefaultDir has more than one source separator: {text!r}")
    target_text, sep, source_text = text.partition(":")
    if not target_text:
        raise ValueError(f"DefaultDir has no target name: {text!r}")
    target_short, target_long = _split_pair(target_text)
    if not sep:
        return DefaultDir(target_short=target_short, target_long=target_long)
    if not source_text:
        raise ValueError(f"DefaultDir has a separator but no source name: {text!r}")
    source_short, source_long = _split_pair(source_text)
    return DefaultDir(
        target_short=target_short,
        target_long=target_long,
        source_short=source_short,
        source_long=source_long,
    )


def format_default_dir(entry: DefaultDir) -> str:
    """Render a :class:`DefaultDir` back to its cell form.

    Round trips every cell in the shipped AIP.
    """
    target = entry.target_short
    if entry.target_long:
        target = f"{target}|{entry.target_long}"
    if entry.source_short is None and entry.source_long is None:
        return target
    source = entry.source_short or ""
    if entry.source_long is not None:
        source = f"{source}|{entry.source_long}"
    return f"{target}:{source}"


def is_valid_short_name(name: str) -> bool:
    """True when *name* fits the 8.3 charset MSI allows."""
    return bool(SHORT_NAME_PATTERN.match(name))


def is_valid_long_name(name: str) -> bool:
    """True when *name* is a usable long name."""
    return bool(LONG_NAME_PATTERN.match(name))


def _scrub(text: str) -> str:
    return "".join(ch for ch in text.upper() if ch not in _ILLEGAL_IN_SHORT)


def to_short_name(name: str, ordinal: int = 1) -> str:
    """Derive an 8.3 name from a long one, in the style Windows uses.

    Six characters of the stem, a tilde, then *ordinal*, then up to three
    characters of the extension. ``Data Files`` becomes ``DATAFI~1`` and
    ``System Test`` becomes ``SYSTEM~1``, which is what the AIP already stores.

    A name that is already a legal 8.3 name is returned upper cased and
    otherwise untouched, so ``Images`` stays ``IMAGES`` rather than becoming
    ``IMAGE~1``. The test is on the name as given, not on what is left after
    scrubbing. ``Job Files`` scrubs down to eight characters but Windows still
    mangles it to ``JOBFIL~1``, because the space made it illegal in the first
    place.

    :raises ValueError: when *name* has nothing usable left after scrubbing, or
        *ordinal* will not fit alongside six stem characters.
    """
    if ordinal < 1:
        raise ValueError("short name ordinal starts at 1")
    if ordinal == 1 and is_valid_short_name(name):
        return name.upper()
    stem_part, dot, extension_part = name.rpartition(".")
    if not dot:
        stem_part, extension_part = name, ""
    stem = _scrub(stem_part)
    extension = _scrub(extension_part)[:3]
    if not stem:
        raise ValueError(f"no usable short name characters in {name!r}")
    suffix = f"~{ordinal}"
    keep = 8 - len(suffix)
    if keep < 1:
        raise ValueError(f"ordinal {ordinal} leaves no room for a stem")
    candidate = f"{stem[:keep]}{suffix}"
    return f"{candidate}.{extension}" if extension else candidate


class ShortNameAllocator:
    """Hands out unique 8.3 names within one directory.

    MSI requires short names to be unique among siblings, not globally. One
    allocator per directory, and it remembers what it gave out so a second call
    for ``Data Files`` and ``Data Folder`` produces ``DATAFI~1`` and
    ``DATAFI~2`` rather than a collision.
    """

    def __init__(self) -> None:
        self._taken: set[str] = set()

    def reserve(self, short_name: str) -> None:
        """Mark a name as already used, for names lifted straight from an AIP."""
        self._taken.add(short_name.upper())

    def allocate(self, long_name: str) -> str:
        """Return a short name for *long_name* that no sibling holds yet.

        :raises ValueError: when the first 999 ordinals are all taken.
        """
        for ordinal in range(1, 1000):
            candidate = to_short_name(long_name, ordinal)
            if candidate.upper() not in self._taken:
                self._taken.add(candidate.upper())
                return candidate
        raise ValueError(f"exhausted short name ordinals for {long_name!r}")

    @property
    def taken(self) -> frozenset[str]:
        """Everything reserved or allocated so far, upper cased."""
        return frozenset(self._taken)


__all__ = [
    "LONG_NAME_PATTERN",
    "SHORT_NAME_PATTERN",
    "SOURCE_IS_TARGET",
    "DefaultDir",
    "ShortNameAllocator",
    "format_default_dir",
    "is_valid_long_name",
    "is_valid_short_name",
    "parse_default_dir",
    "to_short_name",
]
