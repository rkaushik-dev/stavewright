"""Comparing a terminal against the plan that was applied to it.

Reconcile could have been written as "ask each action whether it is still
satisfied". It is not, because a boolean is the wrong answer. Knowing that
``file.v4t`` is unsatisfied does not tell you whether the file is gone or has
been replaced with a different build, and those two need different people.

So each step kind gets a probe that says what it actually found. Where a kind
genuinely cannot be checked, the probe says so rather than returning a pass,
because a report that says a terminal is clean when nobody looked is worse than
one that admits it.
"""

from __future__ import annotations

from collections.abc import Callable

from stavewright.apply.actions import PayloadSource
from stavewright.model.registry import DecodedValue, RegistryRoot, RegistryValueType
from stavewright.plan.steps import Plan, Step, StepKind
from stavewright.target.base import AssemblyIdentity, ShortcutSpec, TargetError, TargetSystem
from stavewright.util.digest import Digest, digest_bytes
from stavewright.verify.drift import DriftKind, DriftRecord, DriftReport

Probe = Callable[[Step, TargetSystem, PayloadSource | None], list[DriftRecord]]


def _missing(step: Step, subject: str, **detail: object) -> DriftRecord:
    return DriftRecord(
        kind=DriftKind.MISSING, subject=subject, step_id=step.step_id, detail=dict(detail)
    )


def _uncheckable(step: Step, subject: str, why: str) -> DriftRecord:
    return DriftRecord(
        kind=DriftKind.UNCHECKABLE,
        subject=subject,
        step_id=step.step_id,
        detail={"reason": why},
    )


def _expected_digest(step: Step, payload: PayloadSource | None) -> Digest | None:
    if step.digest is not None:
        return step.digest
    if payload is not None and step.source:
        try:
            return digest_bytes(payload.read(step.source))
        except Exception:
            return None
    return None


def probe_directory(
    step: Step,
    target: TargetSystem,
    payload: PayloadSource | None,
) -> list[DriftRecord]:
    if not step.target:
        return [_uncheckable(step, step.step_id, "step names no directory")]
    if target.is_directory(step.target):
        return []
    return [_missing(step, step.target)]


def probe_file(
    step: Step,
    target: TargetSystem,
    payload: PayloadSource | None,
) -> list[DriftRecord]:
    if not step.target:
        return [_uncheckable(step, step.step_id, "step names no target")]
    actual = target.file_digest(step.target)
    if actual is None:
        return [_missing(step, step.target)]
    expected = _expected_digest(step, payload)
    if expected is None:
        return [_uncheckable(step, step.target, "no digest to compare against")]
    if actual != expected:
        return [
            DriftRecord(
                kind=DriftKind.MODIFIED,
                subject=step.target,
                step_id=step.step_id,
                expected=str(expected),
                actual=str(actual),
            )
        ]
    return []


def probe_registry(
    step: Step,
    target: TargetSystem,
    payload: PayloadSource | None,
) -> list[DriftRecord]:
    detail = step.detail
    if "root" not in detail or "key" not in detail:
        return [_uncheckable(step, step.step_id, "step names no registry key")]
    root = RegistryRoot(int(detail["root"]))
    key = str(detail["key"])
    name = detail.get("name")
    subject = f"{root.hive_name}\\{key}\\{name or '(Default)'}"
    expected = DecodedValue(
        RegistryValueType(detail.get("value_type", RegistryValueType.STRING.value)),
        detail.get("value", ""),
    )
    actual = target.read_value(root, key, str(name) if name is not None else None)
    if actual is None:
        return [_missing(step, subject)]
    if actual != expected:
        return [
            DriftRecord(
                kind=DriftKind.WRONG_VALUE,
                subject=subject,
                step_id=step.step_id,
                expected=str(expected),
                actual=str(actual),
            )
        ]
    return []


def probe_com(
    step: Step,
    target: TargetSystem,
    payload: PayloadSource | None,
) -> list[DriftRecord]:
    if not step.target:
        return [_uncheckable(step, step.step_id, "step names no server")]
    if not target.path_exists(step.target):
        return [_missing(step, step.target)]
    records = probe_file(step, target, payload)
    if not target.is_com_registered(step.target):
        records.append(
            DriftRecord(
                kind=DriftKind.UNREGISTERED, subject=step.target, step_id=step.step_id
            )
        )
    return records


def _identity(step: Step) -> AssemblyIdentity | None:
    detail = step.detail
    if not {"assembly", "version", "public_key_token"} <= set(detail):
        return None
    return AssemblyIdentity(
        name=str(detail["assembly"]),
        version=str(detail["version"]),
        public_key_token=str(detail["public_key_token"]),
        processor_architecture=str(detail.get("processor_architecture", "x86")),
    )


def probe_assembly(
    step: Step,
    target: TargetSystem,
    payload: PayloadSource | None,
) -> list[DriftRecord]:
    identity = _identity(step)
    if identity is None:
        return [_uncheckable(step, step.step_id, "step names no assembly identity")]
    if target.has_assembly(identity):
        return []
    return [_missing(step, identity.name, version=identity.version)]


def probe_policy(
    step: Step,
    target: TargetSystem,
    payload: PayloadSource | None,
) -> list[DriftRecord]:
    identity = _identity(step)
    if identity is None:
        return [_uncheckable(step, step.step_id, "step names no assembly identity")]
    if target.has_policy(identity):
        return []
    return [_missing(step, f"policy for {identity.name}", version=identity.version)]


def probe_driver(
    step: Step,
    target: TargetSystem,
    payload: PayloadSource | None,
) -> list[DriftRecord]:
    inf = step.detail.get("inf")
    if not inf:
        return [_uncheckable(step, step.step_id, "step names no INF")]
    try:
        present = target.has_driver(str(inf))
    except TargetError as exc:
        return [_uncheckable(step, str(inf), str(exc))]
    return [] if present else [_missing(step, str(inf))]


def probe_controller(
    step: Step,
    target: TargetSystem,
    payload: PayloadSource | None,
) -> list[DriftRecord]:
    wanted = step.detail.get("revision")
    if not wanted:
        return [_uncheckable(step, step.step_id, "step names no revision")]
    actual = target.controller_revision()
    if actual is None:
        return [_missing(step, "print head controller", wanted=str(wanted))]
    if actual != str(wanted):
        return [
            DriftRecord(
                kind=DriftKind.WRONG_REVISION,
                subject="print head controller",
                step_id=step.step_id,
                expected=str(wanted),
                actual=actual,
            )
        ]
    return []


def probe_font(
    step: Step,
    target: TargetSystem,
    payload: PayloadSource | None,
) -> list[DriftRecord]:
    name = step.detail.get("font")
    if not name:
        return [_uncheckable(step, step.step_id, "step names no font")]
    return [] if target.has_font(str(name)) else [_missing(step, str(name))]


def probe_shortcut(
    step: Step,
    target: TargetSystem,
    payload: PayloadSource | None,
) -> list[DriftRecord]:
    detail = step.detail
    if not detail.get("directory") or not detail.get("target"):
        return [_uncheckable(step, step.step_id, "step names no shortcut")]
    spec = ShortcutSpec(
        name=str(detail.get("name") or step.step_id),
        directory=str(detail["directory"]),
        target=str(detail["target"]),
    )
    if target.has_shortcut(spec):
        return []
    return [_missing(step, f"{spec.directory}\\{spec.name}")]


def probe_template_set(
    step: Step,
    target: TargetSystem,
    payload: PayloadSource | None,
) -> list[DriftRecord]:
    files = step.detail.get("files")
    if not isinstance(files, list) or not files or not step.target:
        return [_uncheckable(step, step.step_id, "step lists no template files")]
    from pathlib import PureWindowsPath

    parent = PureWindowsPath(step.target).parent
    records: list[DriftRecord] = []
    for name in files:
        path = str(parent / str(name))
        actual = target.file_digest(path)
        if actual is None:
            records.append(_missing(step, path))
            continue
        if payload is None or not step.source:
            records.append(_uncheckable(step, path, "no staged copy to compare against"))
            continue
        try:
            expected = digest_bytes(payload.read(f"{step.source}/{name}"))
        except Exception:
            records.append(_uncheckable(step, path, "staged copy could not be read"))
            continue
        if actual != expected:
            records.append(
                DriftRecord(
                    kind=DriftKind.MODIFIED,
                    subject=path,
                    step_id=step.step_id,
                    expected=str(expected),
                    actual=str(actual),
                )
            )
    return records


def probe_redistributable(
    step: Step,
    target: TargetSystem,
    payload: PayloadSource | None,
) -> list[DriftRecord]:
    detail = step.detail
    if "marker_key" not in detail:
        return [
            _uncheckable(
                step,
                step.step_id,
                "the redistributable leaves no marker this toolkit can read",
            )
        ]
    root = RegistryRoot(int(detail.get("marker_root", RegistryRoot.LOCAL_MACHINE)))
    key = str(detail["marker_key"])
    name = str(detail.get("marker_name", "Installed"))
    if target.read_value(root, key, name) is not None:
        return []
    return [_missing(step, f"{root.hive_name}\\{key}\\{name}")]


PROBES: dict[StepKind, Probe] = {
    StepKind.CREATE_DIRECTORY: probe_directory,
    StepKind.COPY_FILE: probe_file,
    StepKind.INSTALL_LANGUAGE_PACK: probe_file,
    StepKind.WRITE_REGISTRY: probe_registry,
    StepKind.REGISTER_COM: probe_com,
    StepKind.INSTALL_SXS_ASSEMBLY: probe_assembly,
    StepKind.INSTALL_SXS_POLICY: probe_policy,
    StepKind.STAGE_DRIVER: probe_driver,
    StepKind.PROGRAM_FPGA: probe_controller,
    StepKind.INSTALL_FONT: probe_font,
    StepKind.CREATE_SHORTCUT: probe_shortcut,
    StepKind.INSTALL_TEMPLATE_SET: probe_template_set,
    StepKind.RUN_REDISTRIBUTABLE: probe_redistributable,
}


def reconcile(
    plan: Plan,
    target: TargetSystem,
    payload: PayloadSource | None = None,
    *,
    only: set[str] | None = None,
) -> DriftReport:
    """Compare *target* against *plan*.

    :param payload: the staged bundle, when there is one. Without it a file can
        be reported as missing but not as modified, unless the plan carries a
        digest.
    :param only: step ids to check. Defaults to all of them.
    """
    report = DriftReport(terminal=plan.profile.terminal_id, plan_digest=str(plan.digest()))
    for step in plan:
        if only is not None and step.step_id not in only:
            continue
        report.checked += 1
        probe = PROBES.get(step.kind)
        if probe is None:
            report.records.append(
                _uncheckable(step, step.step_id, f"no probe for {step.kind.value}")
            )
            continue
        report.records.extend(probe(step, target, payload))
    return report


def find_extra_files(
    plan: Plan,
    target: TargetSystem,
    directory: str,
) -> list[DriftRecord]:
    """Files in *directory* on the terminal that the plan does not put there.

    Only the one directory, only its immediate contents, and only files.
    Walking the whole product tree turns up log files and job files the
    terminal writes for itself, and a report full of those is a report nobody
    reads. Subdirectories are left out for the same reason: Templates, log and
    temp are all created by the plan and listing them as unexpected is noise.
    """
    from pathlib import PureWindowsPath

    root = PureWindowsPath(directory)
    planned = {
        PureWindowsPath(step.target).name.lower()
        for step in plan
        if step.target and PureWindowsPath(step.target).parent == root
    }
    records: list[DriftRecord] = []
    for name in target.list_directory(directory):
        if name.lower() in planned:
            continue
        path = root / name
        if target.is_directory(str(path)):
            continue
        records.append(DriftRecord(kind=DriftKind.EXTRA, subject=str(path)))
    return records


__all__ = [
    "PROBES",
    "Probe",
    "find_extra_files",
    "reconcile",
]
