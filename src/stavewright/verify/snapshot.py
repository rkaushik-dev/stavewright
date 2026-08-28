"""Taking a terminal's state away with you.

A line PC on a factory floor has no network and the person standing at it is
not the person who can work out what is wrong. What they can do is run one
command, put the result on a USB stick and send it.

A snapshot is everything a plan asks about, read off the machine and written to
one JSON file. Loading it back gives an object that satisfies the target
protocol for reading, so :func:`~stavewright.verify.reconcile.reconcile` runs
against it exactly as it would against the real machine.

It is read only. Every mutating call on a loaded snapshot raises, because a
snapshot is a photograph and applying to a photograph is a way of believing you
have fixed something.

What gets captured is driven by the plan rather than by walking the disk. A
terminal has tens of thousands of files and none of the ones the plan does not
mention are any of our business.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn

from stavewright import clock
from stavewright.errors import VerifyError
from stavewright.model.registry import DecodedValue, RegistryRoot, RegistryValueType
from stavewright.plan.steps import Plan, StepKind
from stavewright.target.base import (
    AssemblyIdentity,
    ShortcutSpec,
    SystemFacts,
    TargetError,
    TargetSystem,
)
from stavewright.util.digest import Digest
from stavewright.util.fsops import atomic_write_text
from stavewright.util.winpath import fold_path, registry_path, shortcut_key

SNAPSHOT_SCHEMA_VERSION = 1
SNAPSHOT_SUFFIX = ".swsnap.json"


class ReadOnlyTargetError(TargetError):
    """Raised on any attempt to change a snapshot."""


def _identity_of(step: object) -> AssemblyIdentity | None:
    detail = getattr(step, "detail", {})
    if not {"assembly", "version", "public_key_token"} <= set(detail):
        return None
    return AssemblyIdentity(
        name=str(detail["assembly"]),
        version=str(detail["version"]),
        public_key_token=str(detail["public_key_token"]),
        processor_architecture=str(detail.get("processor_architecture", "x86")),
    )


@dataclass
class Snapshot:
    """A terminal's state, frozen.

    Satisfies the read half of :class:`~stavewright.target.base.TargetSystem`.
    """

    terminal: str = ""
    plan_digest: str = ""
    taken_at: str = ""
    system_facts: SystemFacts | None = None
    """What the machine reported. None when the snapshot did not record it.

    Not called ``facts``. The target protocol has a ``facts()`` method, and a
    field of the same name means a Snapshot reads like a target right up until
    something calls it.
    """
    files: dict[str, str] = field(default_factory=dict)
    """Path to digest. The bytes are not captured, only what they hash to."""

    directories: list[str] = field(default_factory=list)
    registry: dict[str, tuple[str, str]] = field(default_factory=dict)
    """``HIVE\\key\\name`` to (type, rendered value)."""

    com_servers: list[str] = field(default_factory=list)
    assemblies: list[str] = field(default_factory=list)
    policies: list[str] = field(default_factory=list)
    drivers: list[str] = field(default_factory=list)
    fonts: list[str] = field(default_factory=list)
    shortcuts: list[str] = field(default_factory=list)
    controller_revision_value: str | None = None
    source: Path | None = None

    # Reading

    def facts(self) -> SystemFacts:
        """What the machine reported when the snapshot was taken.

        A snapshot with no recorded facts answers with zeroes rather than
        raising, so a preflight run against one fails its checks honestly
        instead of blowing up in the middle of them.
        """
        if self.system_facts is None:
            return SystemFacts(
                os_build=0, architecture="", is_elevated=False, free_bytes=0
            )
        return self.system_facts

    def _refuse(self, what: str) -> NoReturn:
        """Refuse a write, and tell the type checker this never returns.

        Written as ``-> None`` it did return as far as mypy was concerned, so
        every one of the twenty two write methods needed an unreachable
        ``raise AssertionError`` under the call to satisfy its return type.
        """
        raise ReadOnlyTargetError(
            f"a snapshot cannot {what}. It is a photograph of a terminal, "
            "not the terminal."
        )

    def path_exists(self, path: str) -> bool:
        key = fold_path(path)
        return key in self.files or key in {fold_path(p) for p in self.directories}

    def is_directory(self, path: str) -> bool:
        return fold_path(path) in {fold_path(p) for p in self.directories}

    def file_digest(self, path: str) -> Digest | None:
        value = self.files.get(fold_path(path))
        return Digest.parse(value) if value else None

    def read_file(self, path: str) -> bytes:
        raise ReadOnlyTargetError(
            "a snapshot records what a file hashed to, not what was in it"
        )

    def list_directory(self, path: str) -> list[str]:
        prefix = fold_path(path) + "/"
        names = {
            name[len(prefix) :].split("/")[0]
            for name in [*self.files, *(fold_path(p) for p in self.directories)]
            if name.startswith(prefix)
        }
        return sorted(names)

    def key_exists(self, root: RegistryRoot, key: str) -> bool:
        prefix = f"{root.hive_name}\\{key}\\".lower()
        return any(name.lower().startswith(prefix) for name in self.registry)

    def read_value(self, root: RegistryRoot, key: str, name: str | None) -> DecodedValue | None:
        stored = self.registry.get(registry_path(root, key, name))
        if stored is None:
            return None
        kind, rendered = stored
        return _decode(kind, rendered)

    def list_values(self, root: RegistryRoot, key: str) -> list[str]:
        prefix = f"{root.hive_name}\\{key}\\".lower()
        return sorted(
            entry.split("\\")[-1]
            for entry in self.registry
            if entry.lower().startswith(prefix)
        )

    def is_com_registered(self, path: str) -> bool:
        return fold_path(path) in {fold_path(p) for p in self.com_servers}

    def has_assembly(self, identity: AssemblyIdentity) -> bool:
        return str(identity) in self.assemblies

    def has_policy(self, identity: AssemblyIdentity) -> bool:
        return str(identity) in self.policies

    def has_driver(self, inf_name: str) -> bool:
        return inf_name.lower() in {name.lower() for name in self.drivers}

    def has_font(self, name: str) -> bool:
        return name.lower() in {font.lower() for font in self.fonts}

    def has_shortcut(self, spec: ShortcutSpec) -> bool:
        return shortcut_key(spec) in self.shortcuts

    def controller_revision(self) -> str | None:
        return self.controller_revision_value

    # Writing, all of which is refused

    def make_directory(self, path: str) -> bool:
        self._refuse("create a directory")

    def remove_directory(self, path: str) -> bool:
        self._refuse("remove a directory")

    def write_file(self, path: str, data: bytes) -> bool:
        self._refuse("write a file")

    def remove_file(self, path: str) -> bool:
        self._refuse("remove a file")

    def create_key(self, root: RegistryRoot, key: str) -> bool:
        self._refuse("create a registry key")

    def remove_key(self, root: RegistryRoot, key: str) -> bool:
        self._refuse("remove a registry key")

    def write_value(
        self, root: RegistryRoot, key: str, name: str | None, value: DecodedValue
    ) -> bool:
        self._refuse("write a registry value")

    def remove_value(self, root: RegistryRoot, key: str, name: str | None) -> bool:
        self._refuse("remove a registry value")

    def register_com(self, path: str) -> bool:
        self._refuse("register a control")

    def unregister_com(self, path: str) -> bool:
        self._refuse("unregister a control")

    def install_assembly(self, identity: AssemblyIdentity, source: str) -> bool:
        self._refuse("install an assembly")

    def remove_assembly(self, identity: AssemblyIdentity) -> bool:
        self._refuse("remove an assembly")

    def install_policy(self, identity: AssemblyIdentity, source: str) -> bool:
        self._refuse("install a policy")

    def remove_policy(self, identity: AssemblyIdentity) -> bool:
        self._refuse("remove a policy")

    def stage_driver(self, source: str, inf_name: str) -> bool:
        self._refuse("stage a driver")

    def remove_driver(self, inf_name: str) -> bool:
        self._refuse("remove a driver")

    def program_controller(self, source: str, revision: str) -> bool:
        self._refuse("program a controller")

    def install_font(self, source: str, name: str) -> bool:
        self._refuse("install a font")

    def remove_font(self, name: str) -> bool:
        self._refuse("remove a font")

    def create_shortcut(self, spec: ShortcutSpec) -> bool:
        self._refuse("create a shortcut")

    def remove_shortcut(self, spec: ShortcutSpec) -> bool:
        self._refuse("remove a shortcut")

    def run_installer(self, path: str, arguments: list[str]) -> int:
        self._refuse("run an installer")

    # Serialisation

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "terminal": self.terminal,
            "plan": self.plan_digest,
            "taken_at": self.taken_at,
            "files": dict(sorted(self.files.items())),
            "directories": sorted(self.directories),
            "registry": {
                key: list(value) for key, value in sorted(self.registry.items())
            },
            "com_servers": sorted(self.com_servers),
            "assemblies": sorted(self.assemblies),
            "policies": sorted(self.policies),
            "drivers": sorted(self.drivers),
            "fonts": sorted(self.fonts),
            "shortcuts": sorted(self.shortcuts),
            "controller_revision": self.controller_revision_value,
        }
        if self.system_facts is not None:
            payload["facts"] = {
                "os_build": self.system_facts.os_build,
                "architecture": self.system_facts.architecture,
                "is_elevated": self.system_facts.is_elevated,
                "free_bytes": self.system_facts.free_bytes,
                "computer_name": self.system_facts.computer_name,
            }
        return payload

    def dumps(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, indent=2) + "\n"

    def write(self, path: Path) -> None:
        atomic_write_text(path, self.dumps())

    def describe(self) -> str:
        where = self.source.name if self.source else "(in memory)"
        return (
            f"{where}: {self.terminal or 'unknown terminal'}, "
            f"{len(self.files)} files, {len(self.registry)} registry values, "
            f"taken {self.taken_at or 'at an unknown time'}"
        )


def _decode(kind: str, rendered: str) -> DecodedValue:
    value_type = RegistryValueType(kind)
    if value_type is RegistryValueType.DWORD:
        return DecodedValue(value_type, int(rendered))
    if value_type is RegistryValueType.BINARY:
        return DecodedValue(value_type, bytes.fromhex(rendered))
    if value_type is RegistryValueType.MULTI_STRING:
        return DecodedValue(value_type, tuple(rendered.split(" | ")) if rendered else ())
    return DecodedValue(value_type, rendered)


def capture(plan: Plan, target: TargetSystem) -> Snapshot:
    """Read everything *plan* asks about off *target*.

    Driven by the plan rather than by walking the disk. A terminal has tens of
    thousands of files and the ones the plan does not mention are none of our
    business.
    """
    snapshot = Snapshot(
        terminal=plan.profile.terminal_id,
        plan_digest=str(plan.digest()),
        taken_at=clock.stamp(),
        system_facts=target.facts(),
        controller_revision_value=target.controller_revision(),
    )

    for step in plan:
        if step.target:
            if step.kind is StepKind.CREATE_DIRECTORY:
                if target.is_directory(step.target):
                    snapshot.directories.append(step.target)
            else:
                digest = target.file_digest(step.target)
                if digest is not None:
                    snapshot.files[fold_path(step.target)] = str(digest)

        match step.kind:
            case StepKind.REGISTER_COM:
                if step.target and target.is_com_registered(step.target):
                    snapshot.com_servers.append(step.target)
            case StepKind.INSTALL_SXS_ASSEMBLY:
                identity = _identity_of(step)
                if identity is not None and target.has_assembly(identity):
                    snapshot.assemblies.append(str(identity))
            case StepKind.INSTALL_SXS_POLICY:
                identity = _identity_of(step)
                if identity is not None and target.has_policy(identity):
                    snapshot.policies.append(str(identity))
            case StepKind.STAGE_DRIVER:
                inf = step.detail.get("inf")
                if inf and target.has_driver(str(inf)):
                    snapshot.drivers.append(str(inf))
            case StepKind.INSTALL_FONT:
                font = step.detail.get("font")
                if font and target.has_font(str(font)):
                    snapshot.fonts.append(str(font))
            case StepKind.CREATE_SHORTCUT:
                spec = _shortcut_of(step)
                if spec is not None and target.has_shortcut(spec):
                    snapshot.shortcuts.append(shortcut_key(spec))
            case StepKind.WRITE_REGISTRY:
                _capture_registry(step, target, snapshot)
            case StepKind.INSTALL_TEMPLATE_SET:
                _capture_templates(step, target, snapshot)
            case _:
                pass
    return snapshot


def _shortcut_of(step: object) -> ShortcutSpec | None:
    detail = getattr(step, "detail", {})
    if not detail.get("directory") or not detail.get("target"):
        return None
    return ShortcutSpec(
        name=str(detail.get("name") or getattr(step, "step_id", "")),
        directory=str(detail["directory"]),
        target=str(detail["target"]),
    )


def _capture_registry(step: object, target: TargetSystem, snapshot: Snapshot) -> None:
    detail = getattr(step, "detail", {})
    if "root" not in detail or "key" not in detail:
        return
    root = RegistryRoot(int(detail["root"]))
    key = str(detail["key"])
    name = detail.get("name")
    value = target.read_value(root, key, str(name) if name is not None else None)
    if value is not None:
        snapshot.registry[registry_path(root, key, name)] = (
            value.type.value,
            str(value),
        )


def _capture_templates(step: object, target: TargetSystem, snapshot: Snapshot) -> None:
    from pathlib import PureWindowsPath

    files = getattr(step, "detail", {}).get("files")
    step_target = getattr(step, "target", None)
    if not isinstance(files, list) or not step_target:
        return
    parent = PureWindowsPath(step_target).parent
    for name in files:
        path = str(parent / str(name))
        digest = target.file_digest(path)
        if digest is not None:
            snapshot.files[fold_path(path)] = str(digest)


def loads(text: str, *, source: Path | None = None) -> Snapshot:
    """Parse a snapshot.

    :raises VerifyError: when the text is not a snapshot this build can read.
    """
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise VerifyError(
            "snapshot is not valid JSON",
            context={"detail": str(exc)},
        ) from exc
    if not isinstance(payload, dict):
        raise VerifyError("snapshot is not a JSON object")
    schema = payload.get("schema_version", SNAPSHOT_SCHEMA_VERSION)
    if not isinstance(schema, int) or schema > SNAPSHOT_SCHEMA_VERSION:
        raise VerifyError(
            "snapshot was written by a newer toolkit",
            context={"found": schema, "understood": SNAPSHOT_SCHEMA_VERSION},
        )
    facts_payload = payload.get("facts")
    facts = None
    if isinstance(facts_payload, dict):
        facts = SystemFacts(
            os_build=int(facts_payload.get("os_build", 0)),
            architecture=str(facts_payload.get("architecture", "")),
            is_elevated=bool(facts_payload.get("is_elevated", False)),
            free_bytes=int(facts_payload.get("free_bytes", 0)),
            computer_name=str(facts_payload.get("computer_name", "")),
        )
    return Snapshot(
        terminal=str(payload.get("terminal", "")),
        plan_digest=str(payload.get("plan", "")),
        taken_at=str(payload.get("taken_at", "")),
        system_facts=facts,
        files=dict(payload.get("files") or {}),
        directories=list(payload.get("directories") or []),
        registry={
            key: (value[0], value[1])
            for key, value in (payload.get("registry") or {}).items()
        },
        com_servers=list(payload.get("com_servers") or []),
        assemblies=list(payload.get("assemblies") or []),
        policies=list(payload.get("policies") or []),
        drivers=list(payload.get("drivers") or []),
        fonts=list(payload.get("fonts") or []),
        shortcuts=list(payload.get("shortcuts") or []),
        controller_revision_value=payload.get("controller_revision"),
        source=source,
    )


def read_snapshot(path: Path) -> Snapshot:
    """Read a snapshot from disk.

    :raises VerifyError: when the file cannot be read or does not parse.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise VerifyError(
            "cannot read snapshot",
            context={"path": str(path), "detail": str(exc)},
        ) from exc
    return loads(text, source=path)


__all__ = [
    "SNAPSHOT_SCHEMA_VERSION",
    "SNAPSHOT_SUFFIX",
    "ReadOnlyTargetError",
    "Snapshot",
    "capture",
    "loads",
    "read_snapshot",
]
