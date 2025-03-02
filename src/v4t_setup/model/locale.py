"""The languages an S-Jet 600 can be commissioned in.

The package ships one resource DLL per language and English is compiled into
``V4T.exe`` itself, which is why there is no ``LangEnglish.dll`` in the AIP
component list. Installing a resource DLL for a language the operator will
never select costs nothing on disk but shows up in the front end's language
menu, and a menu with eight entries on a line that only ever runs in Polish is
a support call waiting to happen.

The code page is not decoration either. The print head renders text through a
single byte code page, so a job carrying Cyrillic on a terminal commissioned
for 1252 marks a line of question marks onto product and nobody notices until
the pallet is wrapped.
"""

from __future__ import annotations

from dataclasses import dataclass

BASE_LANGUAGE_ID = 1033
"""English, compiled into the executable. Always present, never a component."""


@dataclass(frozen=True, slots=True)
class Language:
    """One entry in the language table.

    :param lcid: the Windows language identifier the AIP and MSI use.
    :param tag: the BCP 47 tag, for reports and config files that prefer text.
    :param english_name: how the language appears in the front end menu.
    :param code_page: the single byte code page the print head renders through.
    :param component: the MSI component name, or None for the base language.
    """

    lcid: int
    tag: str
    english_name: str
    code_page: int
    component: str | None

    @property
    def is_base(self) -> bool:
        """True for the language built into the executable."""
        return self.component is None

    @property
    def resource_file(self) -> str | None:
        """The file name of the resource DLL, or None for the base language."""
        return f"{self.component}.dll" if self.component else None


_LANGUAGES: tuple[Language, ...] = (
    Language(1033, "en-US", "English", 1252, None),
    Language(2052, "zh-CN", "Chinese (Simplified)", 936, "LangChinese"),
    Language(1036, "fr-FR", "French", 1252, "LangFrench"),
    Language(1031, "de-DE", "German", 1252, "LangGerman"),
    Language(1045, "pl-PL", "Polish", 1250, "LangPolish"),
    Language(1046, "pt-BR", "Portuguese (Brazil)", 1252, "LangPortuguese"),
    Language(1049, "ru-RU", "Russian", 1251, "LangRussian"),
    Language(3082, "es-ES", "Spanish", 1252, "LangSpanish"),
    Language(1055, "tr-TR", "Turkish", 1254, "LangTurkish"),
)

BY_LCID: dict[int, Language] = {entry.lcid: entry for entry in _LANGUAGES}
BY_TAG: dict[str, Language] = {entry.tag.lower(): entry for entry in _LANGUAGES}
BY_COMPONENT: dict[str, Language] = {
    entry.component: entry for entry in _LANGUAGES if entry.component
}


class UnknownLanguageError(ValueError):
    """A language was requested that the product does not ship.

    Derived from ``ValueError`` rather than ``KeyError`` on purpose. The input
    is usually a string out of a profile file, so pydantic has to be able to
    fold it into a validation error, and ``KeyError`` also re-quotes the message
    when it is rendered which makes the report read badly.
    """


def all_languages() -> tuple[Language, ...]:
    """Every language the product ships, base first then alphabetical by tag."""
    return _LANGUAGES


def resolve(value: int | str) -> Language:
    """Look a language up by LCID, BCP 47 tag or component name.

    Accepts ``1049``, ``"1049"``, ``"ru-RU"``, ``"ru"`` and ``"LangRussian"``.
    A bare subtag resolves only when exactly one shipped language uses it, so
    ``"pt"`` finds Brazilian Portuguese but a future ``pt-PT`` would make it
    ambiguous and it would start raising instead of guessing.

    :raises UnknownLanguageError: when nothing matches or a subtag is ambiguous.
    """
    if isinstance(value, int):
        try:
            return BY_LCID[value]
        except KeyError:
            raise UnknownLanguageError(f"no shipped language has LCID {value}") from None

    text = value.strip()
    if text.isdigit():
        return resolve(int(text))
    if text in BY_COMPONENT:
        return BY_COMPONENT[text]
    lowered = text.lower()
    if lowered in BY_TAG:
        return BY_TAG[lowered]
    if "-" not in lowered:
        matches = [entry for entry in _LANGUAGES if entry.tag.lower().startswith(f"{lowered}-")]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            names = ", ".join(sorted(entry.tag for entry in matches))
            raise UnknownLanguageError(f"language subtag {text!r} is ambiguous between {names}")
    raise UnknownLanguageError(f"unknown language {text!r}")


def resolve_all(values: list[int | str]) -> tuple[Language, ...]:
    """Resolve a list, drop duplicates and return in the shipped table's order.

    Ordering by the table rather than by the caller's list keeps a plan stable
    when two config files list the same languages in a different order.
    """
    wanted = {resolve(value).lcid for value in values}
    return tuple(entry for entry in _LANGUAGES if entry.lcid in wanted)


def components_for(languages: tuple[Language, ...]) -> tuple[str, ...]:
    """The MSI component names needed for *languages*, base language excluded."""
    return tuple(entry.component for entry in languages if entry.component)


def code_pages_for(languages: tuple[Language, ...]) -> frozenset[int]:
    """Every code page the print head has to render for *languages*."""
    return frozenset(entry.code_page for entry in languages)


def needs_multiple_code_pages(languages: tuple[Language, ...]) -> bool:
    """True when the selection cannot be served by one print head code page.

    Firmware holds one code page at a time. A terminal commissioned for both
    Russian and Turkish will mark question marks for one of them, so the planner
    turns this into a preflight warning rather than letting it reach the line.
    """
    return len(code_pages_for(languages)) > 1


__all__ = [
    "BASE_LANGUAGE_ID",
    "BY_COMPONENT",
    "BY_LCID",
    "BY_TAG",
    "Language",
    "UnknownLanguageError",
    "all_languages",
    "code_pages_for",
    "components_for",
    "needs_multiple_code_pages",
    "resolve",
    "resolve_all",
]
