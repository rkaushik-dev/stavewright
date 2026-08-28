"""Registry rows, and the prefix language MSI encodes their types in.

The ``Registry`` table stores a type by decorating the value rather than by
carrying a type column. ``#0`` is the DWORD zero, ``#x1F`` is two bytes of
binary, ``#%PATH%`` is an expandable string and ``##0`` is the literal text
``#0``. A value split by null characters is a multi string. Getting that wrong
means writing ``"#0"`` as text into ``EnableLUA``, which does not disable UAC
and does not error either.

The ``Name`` column has its own small language. ``+`` means create the key and
write nothing, ``-`` means delete the key on uninstall, ``*`` means create the
key and also write the default value.

Root ``-1`` is the one that matters on a terminal. It means HKCU or HKLM
depending on ``ALLUSERS``, and the S-Jet 600 package sets ``ALLUSERS`` to 1, so
the product information block lands in HKLM. A bench that reconciles against
HKCU finds nothing and reports the terminal as uncommissioned.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, StrEnum

from stavewright.errors import ManifestError
from stavewright.util.tokens import from_multi_sz


class RegistryRoot(IntEnum):
    """Values of the ``Root`` column."""

    USER_OR_MACHINE = -1
    CLASSES_ROOT = 0
    CURRENT_USER = 1
    LOCAL_MACHINE = 2
    USERS = 3

    @property
    def hive_name(self) -> str:
        """The short hive name used in reports and drift records."""
        return {
            RegistryRoot.USER_OR_MACHINE: "HKMU",
            RegistryRoot.CLASSES_ROOT: "HKCR",
            RegistryRoot.CURRENT_USER: "HKCU",
            RegistryRoot.LOCAL_MACHINE: "HKLM",
            RegistryRoot.USERS: "HKU",
        }[self]

    def resolve(self, all_users: bool) -> RegistryRoot:
        """Turn ``HKMU`` into a real hive. Other roots return themselves."""
        if self is RegistryRoot.USER_OR_MACHINE:
            return RegistryRoot.LOCAL_MACHINE if all_users else RegistryRoot.CURRENT_USER
        return self


class RegistryValueType(StrEnum):
    """What a decoded value should be written as."""

    STRING = "REG_SZ"
    EXPAND_STRING = "REG_EXPAND_SZ"
    DWORD = "REG_DWORD"
    BINARY = "REG_BINARY"
    MULTI_STRING = "REG_MULTI_SZ"


class KeyOperation(StrEnum):
    """What the ``Name`` column asks for beyond writing a value."""

    WRITE_VALUE = "write-value"
    CREATE_KEY = "create-key"
    REMOVE_KEY_ON_UNINSTALL = "remove-key-on-uninstall"
    CREATE_KEY_AND_DEFAULT = "create-key-and-default"


@dataclass(frozen=True, slots=True)
class DecodedValue:
    """A registry value with its MSI decoration removed."""

    type: RegistryValueType
    data: str | int | bytes | tuple[str, ...]

    def __str__(self) -> str:
        if self.type is RegistryValueType.BINARY:
            assert isinstance(self.data, bytes)
            return self.data.hex().upper()
        if self.type is RegistryValueType.MULTI_STRING:
            assert isinstance(self.data, tuple)
            return " | ".join(self.data)
        return str(self.data)


def decode_value(raw: str | None) -> DecodedValue:
    """Strip the MSI decoration off a value cell.

    ==============  ================================================
    cell            result
    ==============  ================================================
    ``S-Jet 600``   ``REG_SZ`` with that text
    ``#0``          ``REG_DWORD`` zero
    ``#1234``       ``REG_DWORD`` 1234
    ``#x1F2A``      ``REG_BINARY`` of two bytes
    ``#%PATH%``     ``REG_EXPAND_SZ``
    ``##0``         ``REG_SZ`` holding the literal ``#0``
    ``a\\0b``        ``REG_MULTI_SZ`` of two entries
    ==============  ================================================

    :raises ManifestError: on a malformed number or an odd length hex run.
    """
    if raw is None:
        return DecodedValue(RegistryValueType.STRING, "")
    if "\0" in raw:
        return DecodedValue(RegistryValueType.MULTI_STRING, tuple(from_multi_sz(raw)))
    if raw.startswith("##"):
        return DecodedValue(RegistryValueType.STRING, raw[1:])
    if raw.startswith("#%"):
        return DecodedValue(RegistryValueType.EXPAND_STRING, raw[2:])
    if raw.startswith(("#x", "#X")):
        digits = raw[2:]
        if len(digits) % 2:
            raise ManifestError(
                "binary registry value has an odd number of hex digits",
                context={"value": raw},
            )
        try:
            return DecodedValue(RegistryValueType.BINARY, bytes.fromhex(digits))
        except ValueError:
            raise ManifestError(
                "binary registry value is not hexadecimal",
                context={"value": raw},
            ) from None
    if raw.startswith("#"):
        try:
            return DecodedValue(RegistryValueType.DWORD, int(raw[1:], 10))
        except ValueError:
            raise ManifestError(
                "integer registry value is not a number",
                context={"value": raw},
            ) from None
    return DecodedValue(RegistryValueType.STRING, raw)


def encode_value(value: DecodedValue) -> str:
    """Put the MSI decoration back on. Inverse of :func:`decode_value`."""
    match value.type:
        case RegistryValueType.DWORD:
            assert isinstance(value.data, int)
            return f"#{value.data:d}"
        case RegistryValueType.BINARY:
            assert isinstance(value.data, bytes)
            return f"#x{value.data.hex().upper()}"
        case RegistryValueType.EXPAND_STRING:
            assert isinstance(value.data, str)
            return f"#%{value.data}"
        case RegistryValueType.MULTI_STRING:
            assert isinstance(value.data, tuple)
            return "\0".join(value.data)
        case _:
            assert isinstance(value.data, str)
            return f"#{value.data}" if value.data.startswith("#") else value.data


@dataclass(frozen=True, slots=True)
class RegistryEntry:
    """One row of the MSI ``Registry`` table."""

    registry: str
    root: RegistryRoot
    key: str
    component: str
    name: str | None = None
    value: str | None = None

    def __post_init__(self) -> None:
        if not self.key:
            raise ManifestError(
                "registry row has no key",
                context={"registry": self.registry},
            )

    @property
    def operation(self) -> KeyOperation:
        """What the ``Name`` cell is asking for."""
        match self.name:
            case "+":
                return KeyOperation.CREATE_KEY
            case "-":
                return KeyOperation.REMOVE_KEY_ON_UNINSTALL
            case "*":
                return KeyOperation.CREATE_KEY_AND_DEFAULT
            case _:
                return KeyOperation.WRITE_VALUE

    @property
    def is_default_value(self) -> bool:
        """True when the row writes the key's unnamed default value."""
        return self.name in (None, "", "*")

    @property
    def value_name(self) -> str | None:
        """The value name to write, or None for the default value."""
        if self.operation in (KeyOperation.CREATE_KEY, KeyOperation.REMOVE_KEY_ON_UNINSTALL):
            return None
        return None if self.is_default_value else self.name

    def decoded(self) -> DecodedValue:
        """The value with its decoration removed."""
        return decode_value(self.value)

    def hive(self, all_users: bool = True) -> RegistryRoot:
        """The concrete hive this row writes to.

        :param all_users: the value of the ``ALLUSERS`` property. The S-Jet 600
            package sets it to 1, so the default here is True.
        """
        return self.root.resolve(all_users)

    def full_path(self, all_users: bool = True) -> str:
        """``HKLM\\Software\\Matricode\\V4T`` style path, for reports and drift."""
        return f"{self.hive(all_users).hive_name}\\{self.key}"

    def describe(self, all_users: bool = True) -> str:
        """One line naming the path, the value and the type."""
        path = self.full_path(all_users)
        if self.operation is KeyOperation.CREATE_KEY:
            return f"{path} (create key)"
        if self.operation is KeyOperation.REMOVE_KEY_ON_UNINSTALL:
            return f"{path} (remove on uninstall)"
        decoded = self.decoded()
        name = self.value_name or "(Default)"
        return f"{path}\\{name} = {decoded} [{decoded.type.value}]"


__all__ = [
    "DecodedValue",
    "KeyOperation",
    "RegistryEntry",
    "RegistryRoot",
    "RegistryValueType",
    "decode_value",
    "encode_value",
]
