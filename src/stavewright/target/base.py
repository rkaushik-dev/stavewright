"""What a commissioning run is allowed to do to a machine.

The surface is deliberately narrow. Every operation here corresponds to
something a step actually needs, and adding one means a new kind of step, which
means a new action, a new inverse and a new drift check. Making it easy to add
a method would make it easy to add an effect that reconcile cannot see.

Every mutating call is idempotent and every one returns whether it changed
anything. A run that reports fourteen changes on a terminal that was already
commissioned is a run that has told you something is wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from stavewright.model.registry import DecodedValue, RegistryRoot
from stavewright.util.digest import Digest


@dataclass(frozen=True, slots=True)
class SystemFacts:
    """What preflight needs to know about a machine before it commits."""

    os_build: int
    architecture: str
    is_elevated: bool
    free_bytes: int
    computer_name: str = ""
    installed_fonts: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class AssemblyIdentity:
    """A side by side assembly, identified the way the store identifies it."""

    name: str
    version: str
    public_key_token: str
    processor_architecture: str = "x86"

    def __str__(self) -> str:
        return (
            f"{self.name},version={self.version},"
            f"publicKeyToken={self.public_key_token},"
            f"processorArchitecture={self.processor_architecture}"
        )


@dataclass(frozen=True, slots=True)
class ShortcutSpec:
    """Everything needed to create a shortcut."""

    name: str
    directory: str
    target: str
    working_directory: str = ""
    description: str = ""
    icon: str = ""


class TargetError(Exception):
    """Raised by an implementation when the machine refuses an operation."""


@runtime_checkable
class TargetSystem(Protocol):
    """The machine a plan is applied to."""

    # Facts

    def facts(self) -> SystemFacts:
        """Read what preflight needs. Cheap enough to call more than once."""
        ...

    # Filesystem

    def path_exists(self, path: str) -> bool: ...

    def is_directory(self, path: str) -> bool: ...

    def make_directory(self, path: str) -> bool:
        """Create *path* and its parents. Returns whether anything was created."""
        ...

    def remove_directory(self, path: str) -> bool:
        """Remove *path* and its contents. Returns whether anything was removed."""
        ...

    def read_file(self, path: str) -> bytes:
        """Read a file.

        :raises TargetError: when it is not there or cannot be read.
        """
        ...

    def write_file(self, path: str, data: bytes) -> bool:
        """Write a file, creating parents. Returns whether the content changed."""
        ...

    def remove_file(self, path: str) -> bool:
        """Delete a file. Returns whether anything was removed."""
        ...

    def file_digest(self, path: str) -> Digest | None:
        """Digest of a file, or None when it is not there."""
        ...

    def list_directory(self, path: str) -> list[str]:
        """Names directly inside *path*, sorted. Empty when it is not there."""
        ...

    # Registry

    def key_exists(self, root: RegistryRoot, key: str) -> bool: ...

    def create_key(self, root: RegistryRoot, key: str) -> bool:
        """Create a key and its parents. Returns whether anything was created."""
        ...

    def remove_key(self, root: RegistryRoot, key: str) -> bool:
        """Remove a key and everything under it."""
        ...

    def read_value(self, root: RegistryRoot, key: str, name: str | None) -> DecodedValue | None:
        """Read one value, or None when the key or value is absent."""
        ...

    def write_value(
        self,
        root: RegistryRoot,
        key: str,
        name: str | None,
        value: DecodedValue,
    ) -> bool:
        """Write one value, creating the key. Returns whether it changed."""
        ...

    def remove_value(self, root: RegistryRoot, key: str, name: str | None) -> bool:
        """Delete one value. Returns whether anything was removed."""
        ...

    def list_values(self, root: RegistryRoot, key: str) -> list[str]:
        """Value names under *key*, sorted, with the default value as ``""``."""
        ...

    # COM

    def is_com_registered(self, path: str) -> bool: ...

    def register_com(self, path: str) -> bool:
        """Self register a COM server. Returns whether it was not already.

        :raises TargetError: when registration fails.
        """
        ...

    def unregister_com(self, path: str) -> bool: ...

    # Side by side store

    def has_assembly(self, identity: AssemblyIdentity) -> bool: ...

    def install_assembly(self, identity: AssemblyIdentity, source: str) -> bool: ...

    def remove_assembly(self, identity: AssemblyIdentity) -> bool: ...

    def has_policy(self, identity: AssemblyIdentity) -> bool: ...

    def install_policy(self, identity: AssemblyIdentity, source: str) -> bool: ...

    def remove_policy(self, identity: AssemblyIdentity) -> bool: ...

    # Driver store

    def has_driver(self, inf_name: str) -> bool: ...

    def stage_driver(self, source: str, inf_name: str) -> bool: ...

    def remove_driver(self, inf_name: str) -> bool: ...

    # Controller

    def controller_revision(self) -> str | None:
        """Bitstream revision the controller reports, or None when absent."""
        ...

    def program_controller(self, source: str, revision: str) -> bool:
        """Write a bitstream. Not reversible.

        :raises TargetError: when the controller is absent or refuses.
        """
        ...

    # Fonts

    def has_font(self, name: str) -> bool: ...

    def install_font(self, source: str, name: str) -> bool: ...

    def remove_font(self, name: str) -> bool: ...

    # Shortcuts

    def has_shortcut(self, spec: ShortcutSpec) -> bool: ...

    def create_shortcut(self, spec: ShortcutSpec) -> bool: ...

    def remove_shortcut(self, spec: ShortcutSpec) -> bool: ...

    # Processes

    def run_installer(self, path: str, arguments: list[str]) -> int:
        """Run an installer and return its exit code.

        :raises TargetError: when the executable cannot be started.
        """
        ...


__all__ = [
    "AssemblyIdentity",
    "ShortcutSpec",
    "SystemFacts",
    "TargetError",
    "TargetSystem",
]
