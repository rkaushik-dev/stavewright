from __future__ import annotations

import json

import pytest

from v4t_setup import clock
from v4t_setup.apply.actions import DirectoryPayload
from v4t_setup.apply.executor import Executor
from v4t_setup.errors import VerifyError
from v4t_setup.model.registry import RegistryRoot, RegistryValueType
from v4t_setup.plan.steps import Plan, Step, StepKind
from v4t_setup.target.base import ShortcutSpec
from v4t_setup.target.memory import MemoryTarget, string_value
from v4t_setup.verify.drift import DriftKind
from v4t_setup.verify.reconcile import reconcile
from v4t_setup.verify.snapshot import (
    ReadOnlyTargetError,
    Snapshot,
    capture,
    loads,
    read_snapshot,
)

APPDIR = r"C:\Sellenis\S-jet600"
TAKEN = clock.utc(2026, 4, 2, 11, 30)


@pytest.fixture
def payload(tmp_path):
    root = tmp_path / "stage"
    (root / "payload").mkdir(parents=True)
    (root / "payload" / "V4T.exe").write_bytes(b"MZ\x90\x00")
    (root / "payload" / "C1Sizer.ocx").write_bytes(b"MZocx")
    (root / "payload" / "winsxs").mkdir()
    (root / "payload" / "Templates").mkdir()
    (root / "payload" / "Templates" / "T1.spf").write_bytes(b"spf")
    return DirectoryPayload(root)


def full_plan(profile) -> Plan:
    return Plan(
        profile=profile,
        steps=[
            Step(step_id="dir.appdir", kind=StepKind.CREATE_DIRECTORY,
                 title="make APPDIR", target=APPDIR),
            Step(step_id="file.v4t", kind=StepKind.COPY_FILE, title="copy V4T.exe",
                 target=rf"{APPDIR}\V4T.exe", source="payload/V4T.exe"),
            Step(step_id="ocx", kind=StepKind.REGISTER_COM, title="register",
                 target=r"C:\Windows\SysWOW64\C1Sizer.ocx", source="payload/C1Sizer.ocx"),
            Step(step_id="sxs.mfc", kind=StepKind.INSTALL_SXS_ASSEMBLY, title="mfc",
                 source="payload/winsxs",
                 detail={"assembly": "Microsoft.VC80.MFC", "version": "8.0.50727.6195",
                         "public_key_token": "1fc8b3b9a1e18e3b"}),
            Step(step_id="driver", kind=StepKind.STAGE_DRIVER, title="ftdi",
                 source="payload/winsxs", detail={"inf": "ftdibus.inf"}),
            Step(step_id="fpga", kind=StepKind.PROGRAM_FPGA, title="program",
                 source="payload/V4T.exe", detail={"revision": "PCDC-1.4"}),
            Step(step_id="font", kind=StepKind.INSTALL_FONT, title="ocrb",
                 source="payload/V4T.exe", detail={"font": "OCR-B"}),
            Step(step_id="shortcut", kind=StepKind.CREATE_SHORTCUT, title="desktop",
                 detail={"name": "S-jet 600", "directory": r"C:\Users\Public\Desktop",
                         "target": rf"{APPDIR}\V4T.exe"}),
            Step(step_id="templates", kind=StepKind.INSTALL_TEMPLATE_SET, title="templates",
                 target=rf"{APPDIR}\Templates\Templates", source="payload/Templates",
                 detail={"files": ["T1.spf"]}),
            Step(step_id="reg.version", kind=StepKind.WRITE_REGISTRY, title="version",
                 detail={"root": int(RegistryRoot.LOCAL_MACHINE),
                         "key": r"Software\Matricode\V4T", "name": "Version",
                         "value": "1.0.0", "value_type": RegistryValueType.STRING.value}),
        ],
    )


@pytest.fixture
def commissioned(profile, payload, tmp_path):
    target = MemoryTarget()
    plan = full_plan(profile)
    result = Executor(target, payload, tmp_path / "run.jsonl").run(plan, "run0001")
    assert result.succeeded, result.failure
    return plan, target


def test_capture_records_the_terminal_and_the_plan(commissioned):
    plan, target = commissioned
    with clock.frozen(TAKEN):
        snapshot = capture(plan, target)
    assert snapshot.terminal == "LINE-PC-04"
    assert snapshot.plan_digest == str(plan.digest())
    assert snapshot.taken_at == TAKEN.isoformat()


def test_capture_records_the_facts(commissioned):
    plan, target = commissioned
    snapshot = capture(plan, target)
    assert snapshot.facts is not None
    assert snapshot.facts.architecture == "x64"


def test_capture_picks_up_every_kind(commissioned):
    plan, target = commissioned
    snapshot = capture(plan, target)
    assert snapshot.files
    assert snapshot.directories
    assert snapshot.com_servers
    assert snapshot.assemblies
    assert snapshot.drivers
    assert snapshot.fonts
    assert snapshot.shortcuts
    assert snapshot.registry
    assert snapshot.controller_revision_value == "PCDC-1.4"


def test_capture_ignores_files_the_plan_does_not_mention(commissioned):
    plan, target = commissioned
    target.write_file(rf"{APPDIR}\somebodys-tool.exe", b"MZ")
    snapshot = capture(plan, target)
    assert not any("somebodys-tool" in name for name in snapshot.files)


def test_a_snapshot_of_an_empty_machine_records_nothing(profile):
    snapshot = capture(full_plan(profile), MemoryTarget())
    assert snapshot.files == {}
    assert snapshot.com_servers == []
    assert snapshot.controller_revision_value is None


def test_reconcile_against_a_snapshot_matches_the_real_thing(commissioned):
    plan, target = commissioned
    snapshot = capture(plan, target)
    live = reconcile(plan, target, None)
    frozen = reconcile(plan, snapshot, None)
    assert frozen.counts() == live.counts()
    assert frozen.is_clean == live.is_clean


def test_a_snapshot_of_a_drifted_terminal_shows_the_drift(commissioned):
    plan, target = commissioned
    target.remove_file(rf"{APPDIR}\V4T.exe")
    target.unregister_com(r"C:\Windows\SysWOW64\C1Sizer.ocx")
    snapshot = capture(plan, target)
    report = reconcile(plan, snapshot, None)
    assert report.of_kind(DriftKind.MISSING)
    assert report.of_kind(DriftKind.UNREGISTERED)


def test_a_snapshot_round_trips(commissioned):
    plan, target = commissioned
    with clock.frozen(TAKEN):
        original = capture(plan, target)
    rebuilt = loads(original.dumps())
    assert rebuilt.to_dict() == original.to_dict()


def test_a_reloaded_snapshot_reconciles_the_same(commissioned):
    plan, target = commissioned
    snapshot = capture(plan, target)
    rebuilt = loads(snapshot.dumps())
    assert reconcile(plan, rebuilt, None).counts() == reconcile(plan, snapshot, None).counts()


def test_writing_and_reading_a_snapshot(tmp_path, commissioned):
    plan, target = commissioned
    path = tmp_path / "LINE-PC-04.v4tsnap.json"
    capture(plan, target).write(path)
    reopened = read_snapshot(path)
    assert reopened.terminal == "LINE-PC-04"
    assert reopened.source == path


def test_the_file_is_sorted_so_two_captures_diff_cleanly(tmp_path, commissioned):
    plan, target = commissioned
    with clock.frozen(TAKEN):
        first = capture(plan, target).dumps()
        second = capture(plan, target).dumps()
    assert first == second


def test_reading_a_missing_snapshot(tmp_path):
    with pytest.raises(VerifyError, match="cannot read snapshot"):
        read_snapshot(tmp_path / "nope.json")


def test_a_snapshot_that_is_not_json(tmp_path):
    path = tmp_path / "snap.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(VerifyError, match="not valid JSON"):
        read_snapshot(path)


def test_a_snapshot_that_is_not_an_object():
    with pytest.raises(VerifyError, match="not a JSON object"):
        loads("[]")


def test_a_snapshot_from_a_newer_toolkit_is_refused():
    with pytest.raises(VerifyError, match="newer toolkit"):
        loads(json.dumps({"schema_version": 99}))


def test_registry_values_survive_their_type(commissioned):
    plan, target = commissioned
    target.write_value(
        RegistryRoot.LOCAL_MACHINE, r"Software\Matricode\V4T", "Version", string_value("1.0.0")
    )
    snapshot = loads(capture(plan, target).dumps())
    value = snapshot.read_value(
        RegistryRoot.LOCAL_MACHINE, r"Software\Matricode\V4T", "Version"
    )
    assert value == string_value("1.0.0")


def test_a_dword_survives_the_round_trip(profile):
    from v4t_setup.target.memory import dword_value

    target = MemoryTarget()
    target.write_value(
        RegistryRoot.LOCAL_MACHINE, r"Software\X", "EnableLUA", dword_value(0)
    )
    plan = Plan(
        profile=profile,
        steps=[
            Step(step_id="reg", kind=StepKind.WRITE_REGISTRY, title="lua",
                 detail={"root": int(RegistryRoot.LOCAL_MACHINE), "key": r"Software\X",
                         "name": "EnableLUA", "value": 0,
                         "value_type": RegistryValueType.DWORD.value}),
        ],
    )
    snapshot = loads(capture(plan, target).dumps())
    value = snapshot.read_value(RegistryRoot.LOCAL_MACHINE, r"Software\X", "EnableLUA")
    assert value == dword_value(0)


def test_a_snapshot_lists_a_directory(commissioned):
    plan, target = commissioned
    snapshot = capture(plan, target)
    assert "v4t.exe" in snapshot.list_directory(APPDIR)


def test_a_snapshot_lists_registry_values(commissioned):
    plan, target = commissioned
    snapshot = capture(plan, target)
    assert snapshot.list_values(RegistryRoot.LOCAL_MACHINE, r"Software\Matricode\V4T") == [
        "Version"
    ]


def test_key_exists_on_a_snapshot(commissioned):
    plan, target = commissioned
    snapshot = capture(plan, target)
    assert snapshot.key_exists(RegistryRoot.LOCAL_MACHINE, r"Software\Matricode\V4T")
    assert not snapshot.key_exists(RegistryRoot.LOCAL_MACHINE, r"Software\Nowhere")


def test_a_snapshot_does_not_hold_file_contents(commissioned):
    plan, target = commissioned
    snapshot = capture(plan, target)
    with pytest.raises(ReadOnlyTargetError, match="what a file hashed to"):
        snapshot.read_file(rf"{APPDIR}\V4T.exe")


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("make_directory", (APPDIR,)),
        ("remove_directory", (APPDIR,)),
        ("write_file", (rf"{APPDIR}\x.dll", b"MZ")),
        ("remove_file", (rf"{APPDIR}\x.dll",)),
        ("register_com", (r"C:\Windows\SysWOW64\C1Sizer.ocx",)),
        ("unregister_com", (r"C:\Windows\SysWOW64\C1Sizer.ocx",)),
        ("stage_driver", ("src", "ftdibus.inf")),
        ("remove_driver", ("ftdibus.inf",)),
        ("program_controller", ("src", "PCDC-1.4")),
        ("install_font", ("src", "OCR-B")),
        ("remove_font", ("OCR-B",)),
        ("run_installer", ("x.exe", [])),
    ],
)
def test_every_mutating_call_is_refused(method, args):
    snapshot = Snapshot()
    with pytest.raises(ReadOnlyTargetError, match="photograph"):
        getattr(snapshot, method)(*args)


def test_mutating_the_registry_of_a_snapshot_is_refused():
    snapshot = Snapshot()
    with pytest.raises(ReadOnlyTargetError):
        snapshot.write_value(RegistryRoot.LOCAL_MACHINE, "Software\\X", "V", string_value("a"))
    with pytest.raises(ReadOnlyTargetError):
        snapshot.remove_key(RegistryRoot.LOCAL_MACHINE, "Software\\X")


def test_mutating_an_assembly_on_a_snapshot_is_refused():
    from v4t_setup.target.base import AssemblyIdentity

    identity = AssemblyIdentity(name="X", version="1", public_key_token="0")
    snapshot = Snapshot()
    with pytest.raises(ReadOnlyTargetError):
        snapshot.install_assembly(identity, "src")
    with pytest.raises(ReadOnlyTargetError):
        snapshot.remove_policy(identity)


def test_mutating_a_shortcut_on_a_snapshot_is_refused():
    spec = ShortcutSpec(name="x", directory="C:\\d", target="C:\\t")
    with pytest.raises(ReadOnlyTargetError):
        Snapshot().create_shortcut(spec)


def test_describe_a_snapshot(commissioned):
    plan, target = commissioned
    with clock.frozen(TAKEN):
        described = capture(plan, target).describe()
    assert described.startswith("(in memory): LINE-PC-04")
    assert "registry values" in described


def test_describe_an_empty_snapshot():
    assert Snapshot().describe().startswith("(in memory): unknown terminal")
