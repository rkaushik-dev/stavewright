"""Expansion of MSI formatted fields.

Half the strings in the AIP are formatted fields rather than literals. The
``ARPCOMMENTS`` row is a good example::

    This installer database contains the logic and data required to install
    [|ProductName].

Advanced Installer writes ``[|Name]`` where plain MSI writes ``[Name]``. The
leading pipe means the value is substituted when the package is built rather
than when it is installed. For our purposes both resolve out of the same
property table, so both forms are handled and the difference is only reported
when a caller asks.

The rules implemented here are the ones that actually turn up in this project:

* ``[Property]`` and ``[|Property]`` substitute a property, empty if undefined.
* ``[%NAME]`` substitutes an environment variable.
* ``[\\c]`` is a literal ``c``, which is how a bracket gets into a value.
* ``[~]`` is a null character, used as a separator inside ``REG_MULTI_SZ``.
* Substituted text is expanded again, so a property may reference another.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

MAX_EXPANSION_DEPTH = 8
"""How many times a substitution may itself contain a token."""

NULL_TOKEN = "[~]"
"""Renders as a single NUL. Registry multi strings are built out of these."""

_TOKEN = re.compile(r"\[(?P<body>\\.|~|[%|]?[^\[\]]*)\]", re.DOTALL)
_PROPERTY_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")

# Escapes and the null token have to survive the expansion loop untouched.
# Without this, `[\[]ProductName[\]]` resolves to `[ProductName]` on the first
# pass and then gets expanded again on the second, which is exactly what the
# escape was written to prevent. They are swapped for markers in a private use
# range no MSI string will contain, then put back at the end.
_ESCAPE = re.compile(r"\[(?P<body>\\.|~)\]", re.DOTALL)
_MARK_OPEN = "\ue000"
_MARK_CLOSE = "\ue001"
_MARK = re.compile(f"{_MARK_OPEN}(\\d+){_MARK_CLOSE}")


class TokenRecursionError(ValueError):
    """A formatted field expanded into itself."""


class UnknownPropertyError(KeyError):
    """A property was referenced in strict mode and is not defined."""


def find_tokens(text: str) -> set[str]:
    """Return the property names *text* references.

    Environment variables, escapes and the null token are not property
    references and are left out. Used by the manifest builder to check that
    every property a formatted field needs actually exists before shipping.
    """
    names: set[str] = set()
    for match in _TOKEN.finditer(text):
        body = match.group("body")
        if not body or body.startswith(("\\", "%", "~")):
            continue
        name = body[1:] if body.startswith("|") else body
        if _PROPERTY_NAME.match(name):
            names.add(name)
    return names


def is_build_time(token_body: str) -> bool:
    """True for the ``[|Name]`` form Advanced Installer uses."""
    return token_body.startswith("|")


def _protect(text: str, literals: list[str]) -> str:
    """Swap escapes and null tokens for markers, recording what they stood for."""

    def replace(match: re.Match[str]) -> str:
        body = match.group("body")
        literals.append("\0" if body == "~" else body[1:])
        return f"{_MARK_OPEN}{len(literals) - 1}{_MARK_CLOSE}"

    return _ESCAPE.sub(replace, text)


def _restore(text: str, literals: list[str]) -> str:
    """Put the protected literals back."""
    return _MARK.sub(lambda match: literals[int(match.group(1))], text)


def _substitute_once(
    text: str,
    properties: Mapping[str, str],
    environment: Mapping[str, str] | None,
    strict: bool,
) -> tuple[str, bool]:
    changed = False

    def replace(match: re.Match[str]) -> str:
        nonlocal changed
        body = match.group("body")
        changed = True
        if body.startswith("%"):
            source = environment or {}
            return source.get(body[1:], "")
        name = body[1:] if is_build_time(body) else body
        if not _PROPERTY_NAME.match(name):
            # Not a property reference at all. Leave the brackets alone.
            changed = False
            return match.group(0)
        if name in properties:
            return properties[name]
        if strict:
            raise UnknownPropertyError(name)
        return ""

    return _TOKEN.sub(replace, text), changed


def expand(
    text: str,
    properties: Mapping[str, str],
    *,
    environment: Mapping[str, str] | None = None,
    strict: bool = False,
    max_depth: int = MAX_EXPANSION_DEPTH,
) -> str:
    """Expand every token in *text*.

    :param properties: the MSI property table.
    :param environment: what ``[%NAME]`` reads from. Defaults to nothing, so a
        plan is reproducible unless the caller opts in to the ambient
        environment.
    :param strict: raise on an undefined property instead of substituting empty.
    :param max_depth: how many expansion passes to allow.
    :raises TokenRecursionError: when the text still contains tokens after
        *max_depth* passes.
    :raises UnknownPropertyError: in strict mode, for an undefined property.
    """
    literals: list[str] = []
    current = _protect(text, literals)
    for _ in range(max_depth):
        expanded, changed = _substitute_once(current, properties, environment, strict)
        if not changed:
            return _restore(expanded, literals)
        # A property may itself have carried an escape, so re-protect each pass.
        current = _protect(expanded, literals)
    raise TokenRecursionError(
        f"formatted field did not settle after {max_depth} passes: {text!r}"
    )


def expand_all(
    values: Mapping[str, str],
    properties: Mapping[str, str],
    **kwargs: object,
) -> dict[str, str]:
    """Expand every value in a mapping, keeping the keys as they are."""
    return {key: expand(value, properties, **kwargs) for key, value in values.items()}  # type: ignore[arg-type]


def to_multi_sz(parts: list[str]) -> str:
    """Join *parts* the way a ``REG_MULTI_SZ`` formatted field does."""
    return NULL_TOKEN.join(parts)


def from_multi_sz(text: str) -> list[str]:
    """Split an expanded ``REG_MULTI_SZ`` value back into its parts."""
    if not text:
        return []
    return text.split("\0")


__all__ = [
    "MAX_EXPANSION_DEPTH",
    "NULL_TOKEN",
    "TokenRecursionError",
    "UnknownPropertyError",
    "expand",
    "expand_all",
    "find_tokens",
    "from_multi_sz",
    "is_build_time",
    "to_multi_sz",
]
