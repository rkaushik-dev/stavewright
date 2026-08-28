"""One class per step kind, each able to check, do and undo itself.

Three methods and a rule about them. ``is_satisfied`` must not change anything.
``apply`` must be safe to call when ``is_satisfied`` is already true. ``revert``
must be safe to call when ``apply`` never ran.

That triple is what makes a resumed run correct. After an interruption the
executor does not know how far the last step got, so it re-runs it, and the
only thing standing between that and a corrupted terminal is that every action
here does nothing when there is nothing to do.

Two actions cannot undo themselves and say so rather than pretending. Writing a
bitstream overwrites the previous one with nowhere to keep it, and the VC80
redistributable installs into the refcounted machine wide store.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Protocol

from stavewright.errors import ApplyError
from stavewright.model.registry import DecodedValue, RegistryRoot, RegistryValueType
from stavewright.plan.steps import Step, StepKind
from stavewright.target.base import AssemblyIdentity, ShortcutSpec, TargetError, TargetSystem
from stavewright.util.digest import digest_bytes


class PayloadSource(Protocol):
    """Where an action reads the bytes it is going to install."""

    def exists(self, relative: str) -> bool: ...

    def read(self, relative: str) -> bytes: ...

    def local_path(self, relative: str) -> str: ...


class DirectoryPayload:
    """A payload source backed by a staging directory."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _resolve(self, relative: str) -> Path:
        parts = PurePosixPath(relative.replace("\\", "/")).parts
        if any(part == ".." for part in parts):
            raise ApplyError(
                "payload source climbs out of the staging tree",
                context={"source": relative},
            )
        return self.root.joinpath(*parts)

    def exists(self, relative: str) -> bool:
        return self._resolve(relative).exists()

    def read(self, relative: str) -> bytes:
        path = self._resolve(relative)
        try:
            return path.read_bytes()
        except OSError as exc:
            raise ApplyError(
                "cannot read payload",
                context={"source": relative, "detail": str(exc)},
            ) from exc

    def local_path(self, relative: str) -> str:
        return str(self._resolve(relative))


class Action:
    """Base class. Subclasses implement all three methods."""

    kind: StepKind

    def is_satisfied(self, target: TargetSystem, step: Step, payload: PayloadSource) -> bool:
        raise NotImplementedError

    def apply(self, target: TargetSystem, step: Step, payload: PayloadSource) -> bool:
        raise NotImplementedError

    def revert(self, target: TargetSystem, step: Step, payload: PayloadSource) -> bool:
        raise NotImplementedError

    @property
    def reversible(self) -> bool:
        return True

    def _target(self, step: Step) -> str:
        if not step.target:
            raise ApplyError(
                "step has no target to act on",
                context={"step": step.step_id, "kind": step.kind.value},
            )
        return step.target

    def _source(self, step: Step) -> str:
        if not step.source:
            raise ApplyError(
                "step has no source to install from",
                context={"step": step.step_id, "kind": step.kind.value},
            )
        return step.source


class CreateDirectoryAction(Action):
    kind = StepKind.CREATE_DIRECTORY

    def is_satisfied(
        self, target: TargetSystem, step: Step, payload: PayloadSource
    ) -> bool:
        return target.is_directory(self._target(step))

    def apply(
        self, target: TargetSystem, step: Step, payload: PayloadSource
    ) -> bool:
        return target.make_directory(self._target(step))

    def revert(
        self, target: TargetSystem, step: Step, payload: PayloadSource
    ) -> bool:
        return target.remove_directory(self._target(step))


class CopyFileAction(Action):
    kind = StepKind.COPY_FILE

    def is_satisfied(
        self, target: TargetSystem, step: Step, payload: PayloadSource
    ) -> bool:
        current = target.file_digest(self._target(step))
        if current is None:
            return False
        if step.digest is not None:
            return current == step.digest
        return current == digest_bytes(payload.read(self._source(step)))

    def apply(
        self, target: TargetSystem, step: Step, payload: PayloadSource
    ) -> bool:
        data = payload.read(self._source(step))
        if step.digest is not None:
            actual = digest_bytes(data)
            if actual != step.digest:
                raise ApplyError(
                    "payload does not match the digest in the manifest",
                    context={
                        "step": step.step_id,
                        "expected": str(step.digest),
                        "found": str(actual),
                    },
                )
        return target.write_file(self._target(step), data)

    def revert(
        self, target: TargetSystem, step: Step, payload: PayloadSource
    ) -> bool:
        return target.remove_file(self._target(step))


class InstallLanguagePackAction(CopyFileAction):
    """A language pack is a file. It exists as its own kind for reporting."""

    kind = StepKind.INSTALL_LANGUAGE_PACK


class WriteRegistryAction(Action):
    kind = StepKind.WRITE_REGISTRY

    def _parts(self, step: Step) -> tuple[RegistryRoot, str, str | None, DecodedValue]:
        detail = step.detail
        try:
            root = RegistryRoot(int(detail["root"]))
            key = str(detail["key"])
        except (KeyError, ValueError) as exc:
            raise ApplyError(
                "registry step is missing its root or key",
                context={"step": step.step_id},
            ) from exc
        name = detail.get("name")
        value = DecodedValue(
            RegistryValueType(detail.get("value_type", RegistryValueType.STRING.value)),
            detail.get("value", ""),
        )
        return root, key, (str(name) if name is not None else None), value

    def is_satisfied(
        self, target: TargetSystem, step: Step, payload: PayloadSource
    ) -> bool:
        root, key, name, value = self._parts(step)
        return target.read_value(root, key, name) == value

    def apply(
        self, target: TargetSystem, step: Step, payload: PayloadSource
    ) -> bool:
        root, key, name, value = self._parts(step)
        return target.write_value(root, key, name, value)

    def revert(
        self, target: TargetSystem, step: Step, payload: PayloadSource
    ) -> bool:
        root, key, name, _ = self._parts(step)
        return target.remove_value(root, key, name)


class RegisterComAction(Action):
    kind = StepKind.REGISTER_COM

    def is_satisfied(
        self, target: TargetSystem, step: Step, payload: PayloadSource
    ) -> bool:
        path = self._target(step)
        return target.path_exists(path) and target.is_com_registered(path)

    def apply(
        self, target: TargetSystem, step: Step, payload: PayloadSource
    ) -> bool:
        path = self._target(step)
        changed = target.write_file(path, payload.read(self._source(step)))
        return target.register_com(path) or changed

    def revert(
        self, target: TargetSystem, step: Step, payload: PayloadSource
    ) -> bool:
        path = self._target(step)
        unregistered = target.unregister_com(path)
        removed = target.remove_file(path)
        return unregistered or removed


class _AssemblyAction(Action):
    """Shared identity parsing for the assembly and the publisher policy."""

    def _identity(self, step: Step) -> AssemblyIdentity:
        detail = step.detail
        try:
            return AssemblyIdentity(
                name=str(detail["assembly"]),
                version=str(detail["version"]),
                public_key_token=str(detail["public_key_token"]),
                processor_architecture=str(detail.get("processor_architecture", "x86")),
            )
        except KeyError as exc:
            raise ApplyError(
                "assembly step is missing its identity",
                context={"step": step.step_id, "missing": str(exc)},
            ) from exc


class InstallSxsAssemblyAction(_AssemblyAction):
    kind = StepKind.INSTALL_SXS_ASSEMBLY

    def is_satisfied(
        self, target: TargetSystem, step: Step, payload: PayloadSource
    ) -> bool:
        return target.has_assembly(self._identity(step))

    def apply(
        self, target: TargetSystem, step: Step, payload: PayloadSource
    ) -> bool:
        return target.install_assembly(
            self._identity(step), payload.local_path(self._source(step))
        )

    def revert(
        self, target: TargetSystem, step: Step, payload: PayloadSource
    ) -> bool:
        return target.remove_assembly(self._identity(step))


class InstallSxsPolicyAction(_AssemblyAction):
    kind = StepKind.INSTALL_SXS_POLICY

    def is_satisfied(
        self, target: TargetSystem, step: Step, payload: PayloadSource
    ) -> bool:
        return target.has_policy(self._identity(step))

    def apply(
        self, target: TargetSystem, step: Step, payload: PayloadSource
    ) -> bool:
        return target.install_policy(
            self._identity(step), payload.local_path(self._source(step))
        )

    def revert(
        self, target: TargetSystem, step: Step, payload: PayloadSource
    ) -> bool:
        return target.remove_policy(self._identity(step))


class StageDriverAction(Action):
    kind = StepKind.STAGE_DRIVER

    def _inf(self, step: Step) -> str:
        inf = step.detail.get("inf")
        if not inf:
            raise ApplyError(
                "driver step does not name its INF",
                context={"step": step.step_id},
            )
        return str(inf)

    def is_satisfied(
        self, target: TargetSystem, step: Step, payload: PayloadSource
    ) -> bool:
        return target.has_driver(self._inf(step))

    def apply(
        self, target: TargetSystem, step: Step, payload: PayloadSource
    ) -> bool:
        return target.stage_driver(payload.local_path(self._source(step)), self._inf(step))

    def revert(
        self, target: TargetSystem, step: Step, payload: PayloadSource
    ) -> bool:
        return target.remove_driver(self._inf(step))


class ProgramFpgaAction(Action):
    kind = StepKind.PROGRAM_FPGA

    @property
    def reversible(self) -> bool:
        return False

    def _revision(self, step: Step) -> str:
        revision = step.detail.get("revision")
        if not revision:
            raise ApplyError(
                "bitstream step does not name a revision",
                context={"step": step.step_id},
            )
        return str(revision)

    def is_satisfied(
        self, target: TargetSystem, step: Step, payload: PayloadSource
    ) -> bool:
        return target.controller_revision() == self._revision(step)

    def apply(
        self, target: TargetSystem, step: Step, payload: PayloadSource
    ) -> bool:
        return target.program_controller(
            payload.local_path(self._source(step)), self._revision(step)
        )

    def revert(
        self, target: TargetSystem, step: Step, payload: PayloadSource
    ) -> bool:
        raise ApplyError(
            "a programmed controller cannot be rolled back, the previous "
            "bitstream is gone",
            context={"step": step.step_id},
        )


class InstallFontAction(Action):
    kind = StepKind.INSTALL_FONT

    def _name(self, step: Step) -> str:
        return str(step.detail.get("font") or Path(step.source or "font").stem)

    def is_satisfied(
        self, target: TargetSystem, step: Step, payload: PayloadSource
    ) -> bool:
        return target.has_font(self._name(step))

    def apply(
        self, target: TargetSystem, step: Step, payload: PayloadSource
    ) -> bool:
        return target.install_font(payload.local_path(self._source(step)), self._name(step))

    def revert(
        self, target: TargetSystem, step: Step, payload: PayloadSource
    ) -> bool:
        return target.remove_font(self._name(step))


class CreateShortcutAction(Action):
    kind = StepKind.CREATE_SHORTCUT

    def _spec(self, step: Step) -> ShortcutSpec:
        detail = step.detail
        directory = detail.get("directory")
        shortcut_target = detail.get("target")
        if not directory or not shortcut_target:
            raise ApplyError(
                "shortcut step is missing its directory or target",
                context={"step": step.step_id},
            )
        return ShortcutSpec(
            name=str(detail.get("name") or step.step_id),
            directory=str(directory),
            target=str(shortcut_target),
            working_directory=str(detail.get("working_directory", "")),
            description=str(detail.get("description", "")),
        )

    def is_satisfied(
        self, target: TargetSystem, step: Step, payload: PayloadSource
    ) -> bool:
        return target.has_shortcut(self._spec(step))

    def apply(
        self, target: TargetSystem, step: Step, payload: PayloadSource
    ) -> bool:
        return target.create_shortcut(self._spec(step))

    def revert(
        self, target: TargetSystem, step: Step, payload: PayloadSource
    ) -> bool:
        return target.remove_shortcut(self._spec(step))


class InstallTemplateSetAction(Action):
    kind = StepKind.INSTALL_TEMPLATE_SET

    def _files(self, step: Step) -> list[str]:
        files = step.detail.get("files")
        if not isinstance(files, list) or not files:
            raise ApplyError(
                "template set step lists no files",
                context={"step": step.step_id},
            )
        return [str(name) for name in files]

    def _destination(self, step: Step, name: str) -> str:
        return str(PureWindowsPath(self._target(step)).parent / name)

    def is_satisfied(
        self, target: TargetSystem, step: Step, payload: PayloadSource
    ) -> bool:
        source = self._source(step)
        for name in self._files(step):
            current = target.file_digest(self._destination(step, name))
            if current is None:
                return False
            if current != digest_bytes(payload.read(f"{source}/{name}")):
                return False
        return True

    def apply(
        self, target: TargetSystem, step: Step, payload: PayloadSource
    ) -> bool:
        source = self._source(step)
        changed = False
        for name in self._files(step):
            data = payload.read(f"{source}/{name}")
            changed |= target.write_file(self._destination(step, name), data)
        return changed

    def revert(
        self, target: TargetSystem, step: Step, payload: PayloadSource
    ) -> bool:
        removed = False
        for name in self._files(step):
            removed |= target.remove_file(self._destination(step, name))
        return removed


class RunRedistributableAction(Action):
    kind = StepKind.RUN_REDISTRIBUTABLE

    @property
    def reversible(self) -> bool:
        return False

    def _marker(self, step: Step) -> tuple[RegistryRoot, str, str] | None:
        detail = step.detail
        if "marker_key" not in detail:
            return None
        return (
            RegistryRoot(int(detail.get("marker_root", RegistryRoot.LOCAL_MACHINE))),
            str(detail["marker_key"]),
            str(detail.get("marker_name", "Installed")),
        )

    def is_satisfied(
        self, target: TargetSystem, step: Step, payload: PayloadSource
    ) -> bool:
        marker = self._marker(step)
        if marker is None:
            return False
        root, key, name = marker
        return target.read_value(root, key, name) is not None

    def apply(
        self, target: TargetSystem, step: Step, payload: PayloadSource
    ) -> bool:
        source = self._source(step)
        local = self._target(step) or payload.local_path(source)
        target.write_file(local, payload.read(source))
        arguments = [str(part) for part in step.detail.get("arguments", ["/q"])]
        code = target.run_installer(local, arguments)
        allowed = {int(value) for value in step.detail.get("allowed_exit_codes", [0, 3010])}
        if code not in allowed:
            raise ApplyError(
                "redistributable returned an unexpected exit code",
                context={"step": step.step_id, "exit_code": code, "allowed": sorted(allowed)},
            )
        return True

    def revert(
        self, target: TargetSystem, step: Step, payload: PayloadSource
    ) -> bool:
        raise ApplyError(
            "the redistributable installs into the shared side by side store "
            "and is not ours to remove",
            context={"step": step.step_id},
        )


ACTIONS: dict[StepKind, Action] = {
    action.kind: action
    for action in (
        CreateDirectoryAction(),
        CopyFileAction(),
        InstallLanguagePackAction(),
        WriteRegistryAction(),
        RegisterComAction(),
        InstallSxsAssemblyAction(),
        InstallSxsPolicyAction(),
        StageDriverAction(),
        ProgramFpgaAction(),
        InstallFontAction(),
        CreateShortcutAction(),
        InstallTemplateSetAction(),
        RunRedistributableAction(),
    )
}


def action_for(kind: StepKind) -> Action:
    """The action that handles *kind*.

    :raises ApplyError: when nothing does, which is a gap in this module.
    """
    try:
        return ACTIONS[kind]
    except KeyError:
        raise ApplyError(
            "no action implements this step kind",
            context={"kind": kind.value},
        ) from None


def wrap_target_error(step: Step, exc: TargetError) -> ApplyError:
    """Turn a machine level refusal into something with the step attached."""
    return ApplyError(
        str(exc),
        context={"step": step.step_id, "kind": step.kind.value},
    )


__all__ = [
    "ACTIONS",
    "Action",
    "CopyFileAction",
    "CreateDirectoryAction",
    "CreateShortcutAction",
    "DirectoryPayload",
    "InstallFontAction",
    "InstallLanguagePackAction",
    "InstallSxsAssemblyAction",
    "InstallSxsPolicyAction",
    "InstallTemplateSetAction",
    "PayloadSource",
    "ProgramFpgaAction",
    "RegisterComAction",
    "RunRedistributableAction",
    "StageDriverAction",
    "WriteRegistryAction",
    "action_for",
    "wrap_target_error",
]
