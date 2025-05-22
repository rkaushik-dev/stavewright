"""A terminal that lives in a dictionary.

This is what every test runs against and what a dry run applies to. It is not a
mock: it enforces the same invariants a real machine does, including the ones
people forget.

A file cannot be written into a path whose parent is a file. Removing a
directory removes what is under it. Registry keys are case insensitive because
Windows registry keys are. COM registration fails on a path that does not
exist, because ``regsvr32`` on a missing DLL is an error rather than a no-op.
Programming the controller fails when no controller is attached.

Windows path comparison is case insensitive and separator agnostic, so the
store normalises both. Getting that wrong makes tests pass that would fail on a
bench, which is worse than having no tests.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field

from v4t_setup.model.registry import DecodedValue, RegistryRoot, RegistryValueType
from v4t_setup.target.base import AssemblyIdentity, ShortcutSpec, SystemFacts, TargetError
from v4t_setup.util.digest import Digest, digest_bytes

DEFAULT_FREE_BYTES = 40 * 1024 * 1024 * 1024


def normalise_path(path: str) -> str:
    """Fold a Windows path the way the filesystem compares them.

    Backslashes become forward slashes, the case is lowered, and a trailing
    separator is dropped. A drive letter keeps its colon.
    """
    folded = path.replace("\\", "/").rstrip("/").lower()
    return folded or "/"


def normalise_key(key: str) -> str:
    """Fold a registry key path. Same rules, minus the drive letter."""
    return key.replace("/", "\\").strip("\\").lower()


@dataclass
class MemoryTarget:
    """An in memory implementation of :class:`~v4t_setup.target.base.TargetSystem`."""

    files: dict[str, bytes] = field(default_factory=dict)
    directories: set[str] = field(default_factory=set)
    registry: dict[tuple[int, str], dict[str, DecodedValue]] = field(default_factory=dict)
    com_servers: set[str] = field(default_factory=set)
    assemblies: set[str] = field(default_factory=set)
    policies: set[str] = field(default_factory=set)
    drivers: set[str] = field(default_factory=set)
    fonts: set[str] = field(default_factory=set)
    shortcuts: set[str] = field(default_factory=set)
    installed_revision: str | None = None
    has_controller: bool = True
    os_build: int = 7601
    architecture: str = "x64"
    is_elevated: bool = True
    free_bytes: int = DEFAULT_FREE_BYTES
    computer_name: str = "LINE-PC-04"
    installer_exit_codes: dict[str, int] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    # Facts

    def facts(self) -> SystemFacts:
        return SystemFacts(
            os_build=self.os_build,
            architecture=self.architecture,
            is_elevated=self.is_elevated,
            free_bytes=self.free_bytes,
            computer_name=self.computer_name,
            installed_fonts=frozenset(self.fonts),
        )

    # Filesystem

    def _record(self, operation: str, subject: str) -> None:
        self.calls.append(f"{operation} {subject}")

    def _ancestors(self, path: str) -> list[str]:
        parts = normalise_path(path).split("/")
        return ["/".join(parts[:index]) for index in range(1, len(parts))]

    def path_exists(self, path: str) -> bool:
        folded = normalise_path(path)
        return folded in self.files or folded in self.directories

    def is_directory(self, path: str) -> bool:
        return normalise_path(path) in self.directories

    def make_directory(self, path: str) -> bool:
        folded = normalise_path(path)
        if folded in self.files:
            raise TargetError(f"a file already exists at {path}")
        created = folded not in self.directories
        for ancestor in [*self._ancestors(path), folded]:
            if ancestor and ancestor not in self.files:
                self.directories.add(ancestor)
        if created:
            self._record("mkdir", folded)
        return created

    def remove_directory(self, path: str) -> bool:
        folded = normalise_path(path)
        prefix = f"{folded}/"
        removed = folded in self.directories
        for name in [n for n in self.files if n == folded or n.startswith(prefix)]:
            del self.files[name]
            removed = True
        for name in [n for n in self.directories if n == folded or n.startswith(prefix)]:
            self.directories.discard(name)
        if removed:
            self._record("rmdir", folded)
        return removed

    def read_file(self, path: str) -> bytes:
        folded = normalise_path(path)
        if folded not in self.files:
            raise TargetError(f"no such file: {path}")
        return self.files[folded]

    def write_file(self, path: str, data: bytes) -> bool:
        folded = normalise_path(path)
        if folded in self.directories:
            raise TargetError(f"a directory already exists at {path}")
        for ancestor in self._ancestors(path):
            if ancestor in self.files:
                raise TargetError(f"cannot write below a file: {ancestor}")
        parent = posixpath.dirname(folded)
        if parent:
            self.make_directory(parent)
        changed = self.files.get(folded) != data
        self.files[folded] = data
        if changed:
            self._record("write", folded)
        return changed

    def remove_file(self, path: str) -> bool:
        folded = normalise_path(path)
        removed = self.files.pop(folded, None) is not None
        if removed:
            self._record("delete", folded)
        return removed

    def file_digest(self, path: str) -> Digest | None:
        folded = normalise_path(path)
        if folded not in self.files:
            return None
        return digest_bytes(self.files[folded])

    def list_directory(self, path: str) -> list[str]:
        folded = normalise_path(path)
        prefix = f"{folded}/"
        names: set[str] = set()
        for name in [*self.files, *self.directories]:
            if name.startswith(prefix):
                names.add(name[len(prefix) :].split("/")[0])
        return sorted(names)

    # Registry

    def _slot(self, root: RegistryRoot, key: str) -> tuple[int, str]:
        return (int(root), normalise_key(key))

    def key_exists(self, root: RegistryRoot, key: str) -> bool:
        return self._slot(root, key) in self.registry

    def create_key(self, root: RegistryRoot, key: str) -> bool:
        slot = self._slot(root, key)
        created = slot not in self.registry
        parts = slot[1].split("\\")
        for depth in range(1, len(parts) + 1):
            self.registry.setdefault((slot[0], "\\".join(parts[:depth])), {})
        if created:
            self._record("createkey", f"{root.hive_name}\\{key}")
        return created

    def remove_key(self, root: RegistryRoot, key: str) -> bool:
        slot = self._slot(root, key)
        prefix = f"{slot[1]}\\"
        victims = [
            candidate
            for candidate in self.registry
            if candidate[0] == slot[0]
            and (candidate[1] == slot[1] or candidate[1].startswith(prefix))
        ]
        for victim in victims:
            del self.registry[victim]
        if victims:
            self._record("removekey", f"{root.hive_name}\\{key}")
        return bool(victims)

    def read_value(self, root: RegistryRoot, key: str, name: str | None) -> DecodedValue | None:
        values = self.registry.get(self._slot(root, key))
        if values is None:
            return None
        return values.get((name or "").lower())

    def write_value(
        self,
        root: RegistryRoot,
        key: str,
        name: str | None,
        value: DecodedValue,
    ) -> bool:
        self.create_key(root, key)
        values = self.registry[self._slot(root, key)]
        folded = (name or "").lower()
        changed = values.get(folded) != value
        values[folded] = value
        if changed:
            self._record("setvalue", f"{root.hive_name}\\{key}\\{name or '(Default)'}")
        return changed

    def remove_value(self, root: RegistryRoot, key: str, name: str | None) -> bool:
        values = self.registry.get(self._slot(root, key))
        if values is None:
            return False
        removed = values.pop((name or "").lower(), None) is not None
        if removed:
            self._record("delvalue", f"{root.hive_name}\\{key}\\{name or '(Default)'}")
        return removed

    def list_values(self, root: RegistryRoot, key: str) -> list[str]:
        return sorted(self.registry.get(self._slot(root, key), {}))

    # COM

    def is_com_registered(self, path: str) -> bool:
        return normalise_path(path) in self.com_servers

    def register_com(self, path: str) -> bool:
        folded = normalise_path(path)
        if folded not in self.files:
            raise TargetError(f"cannot register a server that is not there: {path}")
        added = folded not in self.com_servers
        self.com_servers.add(folded)
        if added:
            self._record("regsvr32", folded)
        return added

    def unregister_com(self, path: str) -> bool:
        folded = normalise_path(path)
        removed = folded in self.com_servers
        self.com_servers.discard(folded)
        if removed:
            self._record("regsvr32 /u", folded)
        return removed

    # Side by side store

    def has_assembly(self, identity: AssemblyIdentity) -> bool:
        return str(identity) in self.assemblies

    def install_assembly(self, identity: AssemblyIdentity, source: str) -> bool:
        added = str(identity) not in self.assemblies
        self.assemblies.add(str(identity))
        if added:
            self._record("sxs install", identity.name)
        return added

    def remove_assembly(self, identity: AssemblyIdentity) -> bool:
        removed = str(identity) in self.assemblies
        self.assemblies.discard(str(identity))
        return removed

    def has_policy(self, identity: AssemblyIdentity) -> bool:
        return str(identity) in self.policies

    def install_policy(self, identity: AssemblyIdentity, source: str) -> bool:
        if str(identity) not in self.assemblies:
            raise TargetError(f"no assembly for policy {identity.name}")
        added = str(identity) not in self.policies
        self.policies.add(str(identity))
        if added:
            self._record("sxs policy", identity.name)
        return added

    def remove_policy(self, identity: AssemblyIdentity) -> bool:
        removed = str(identity) in self.policies
        self.policies.discard(str(identity))
        return removed

    # Driver store

    def has_driver(self, inf_name: str) -> bool:
        return inf_name.lower() in self.drivers

    def stage_driver(self, source: str, inf_name: str) -> bool:
        added = inf_name.lower() not in self.drivers
        self.drivers.add(inf_name.lower())
        if added:
            self._record("pnputil", inf_name)
        return added

    def remove_driver(self, inf_name: str) -> bool:
        removed = inf_name.lower() in self.drivers
        self.drivers.discard(inf_name.lower())
        return removed

    # Controller

    def controller_revision(self) -> str | None:
        return self.installed_revision

    def program_controller(self, source: str, revision: str) -> bool:
        if not self.has_controller:
            raise TargetError("no controller board is attached")
        changed = self.installed_revision != revision
        self.installed_revision = revision
        if changed:
            self._record("fpga", revision)
        return changed

    # Fonts

    def has_font(self, name: str) -> bool:
        return name.lower() in {font.lower() for font in self.fonts}

    def install_font(self, source: str, name: str) -> bool:
        added = not self.has_font(name)
        self.fonts.add(name)
        if added:
            self._record("font", name)
        return added

    def remove_font(self, name: str) -> bool:
        matches = {font for font in self.fonts if font.lower() == name.lower()}
        self.fonts -= matches
        return bool(matches)

    # Shortcuts

    def _shortcut_key(self, spec: ShortcutSpec) -> str:
        return f"{normalise_path(spec.directory)}/{spec.name.lower()}"

    def has_shortcut(self, spec: ShortcutSpec) -> bool:
        return self._shortcut_key(spec) in self.shortcuts

    def create_shortcut(self, spec: ShortcutSpec) -> bool:
        key = self._shortcut_key(spec)
        added = key not in self.shortcuts
        self.shortcuts.add(key)
        if added:
            self._record("shortcut", key)
        return added

    def remove_shortcut(self, spec: ShortcutSpec) -> bool:
        key = self._shortcut_key(spec)
        removed = key in self.shortcuts
        self.shortcuts.discard(key)
        return removed

    # Processes

    def run_installer(self, path: str, arguments: list[str]) -> int:
        folded = normalise_path(path)
        if folded not in self.files:
            raise TargetError(f"cannot run an installer that is not there: {path}")
        self._record("run", folded)
        return self.installer_exit_codes.get(folded, 0)

    # Test helpers

    def seed_file(self, path: str, data: bytes = b"") -> None:
        """Put a file in place without recording it as a change."""
        self.write_file(path, data)
        self.calls.clear()

    def snapshot(self) -> dict[str, object]:
        """Everything this target holds, for comparing before and after."""
        return {
            "files": {name: digest_bytes(data).hexdigest for name, data in self.files.items()},
            "directories": sorted(self.directories),
            "registry": {
                f"{root}\\{key}": sorted(values)
                for (root, key), values in self.registry.items()
            },
            "com": sorted(self.com_servers),
            "assemblies": sorted(self.assemblies),
            "policies": sorted(self.policies),
            "drivers": sorted(self.drivers),
            "fonts": sorted(self.fonts),
            "shortcuts": sorted(self.shortcuts),
            "revision": self.installed_revision,
        }


def string_value(text: str) -> DecodedValue:
    """Shorthand for a REG_SZ, which is most of what a test writes."""
    return DecodedValue(RegistryValueType.STRING, text)


def dword_value(number: int) -> DecodedValue:
    """Shorthand for a REG_DWORD."""
    return DecodedValue(RegistryValueType.DWORD, number)


__all__ = [
    "DEFAULT_FREE_BYTES",
    "MemoryTarget",
    "dword_value",
    "normalise_key",
    "normalise_path",
    "string_value",
]
