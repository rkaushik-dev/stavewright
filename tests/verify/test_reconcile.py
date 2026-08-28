from __future__ import annotations

import pytest

from stavewright.apply.actions import DirectoryPayload
from stavewright.apply.executor import Executor
from stavewright.model.registry import RegistryRoot, RegistryValueType
from stavewright.plan.steps import Plan, Step, StepKind
from stavewright.target.base import AssemblyIdentity, ShortcutSpec
from stavewright.target.memory import MemoryTarget, string_value
from stavewright.verify.drift import DriftKind
from stavewright.verify.reconcile import PROBES, find_extra_files, reconcile

APPDIR = r"C:\Sellenis\S-jet600"
MFC = AssemblyIdentity(
    name="Microsoft.VC80.MFC", version="8.0.50727.6195", public_key_token="1fc8b3b9a1e18e3b"
)


@pytest.fixture
def payload(tmp_path):
    root = tmp_path / "stage"
    (root / "payload").mkdir(parents=True)
    (root / "payload" / "V4T.exe").write_bytes(b"MZ\x90\x00")
    (root / "payload" / "C1Sizer.ocx").write_bytes(b"MZocx")
    (root / "payload" / "Templates").mkdir()
    (root / "payload" / "Templates" / "T1.spf").write_bytes(b"spf")
    (root / "payload" / "winsxs").mkdir()
    return DirectoryPayload(root)


@pytest.fixture
def target():
    return MemoryTarget()


def full_plan(profile) -> Plan:
    return Plan(
        profile=profile,
        steps=[
            Step(
                step_id="dir.appdir",
                kind=StepKind.CREATE_DIRECTORY,
                title="make APPDIR",
                target=APPDIR,
            ),
            Step(
                step_id="file.v4t",
                kind=StepKind.COPY_FILE,
                title="copy V4T.exe",
                target=rf"{APPDIR}\V4T.exe",
                source="payload/V4T.exe",
            ),
            Step(
                step_id="ocx.c1sizer",
                kind=StepKind.REGISTER_COM,
                title="register C1Sizer",
                target=r"C:\Windows\SysWOW64\C1Sizer.ocx",
                source="payload/C1Sizer.ocx",
            ),
            Step(
                step_id="sxs.mfc",
                kind=StepKind.INSTALL_SXS_ASSEMBLY,
                title="install MFC",
                source="payload/winsxs",
                detail={
                    "assembly": "Microsoft.VC80.MFC",
                    "version": "8.0.50727.6195",
                    "public_key_token": "1fc8b3b9a1e18e3b",
                },
            ),
            Step(
                step_id="driver.ftdi",
                kind=StepKind.STAGE_DRIVER,
                title="stage the FTDI package",
                source="payload/winsxs",
                detail={"inf": "ftdibus.inf"},
            ),
            Step(
                step_id="fpga",
                kind=StepKind.PROGRAM_FPGA,
                title="program the controller",
                source="payload/V4T.exe",
                detail={"revision": "PCDC-1.4"},
            ),
            Step(
                step_id="font.ocrb",
                kind=StepKind.INSTALL_FONT,
                title="install OCR-B",
                source="payload/V4T.exe",
                detail={"font": "OCR-B"},
            ),
            Step(
                step_id="shortcut.desktop",
                kind=StepKind.CREATE_SHORTCUT,
                title="desktop shortcut",
                detail={
                    "name": "S-jet 600",
                    "directory": r"C:\Users\Public\Desktop",
                    "target": rf"{APPDIR}\V4T.exe",
                },
            ),
            Step(
                step_id="templates.gs1",
                kind=StepKind.INSTALL_TEMPLATE_SET,
                title="install templates",
                target=rf"{APPDIR}\Templates\Templates",
                source="payload/Templates",
                detail={"files": ["T1.spf"]},
            ),
            Step(
                step_id="reg.version",
                kind=StepKind.WRITE_REGISTRY,
                title="write Version",
                detail={
                    "root": int(RegistryRoot.LOCAL_MACHINE),
                    "key": r"Software\Matricode\V4T",
                    "name": "Version",
                    "value": "1.0.0",
                    "value_type": RegistryValueType.STRING.value,
                },
            ),
        ],
    )


def commission(profile, target, payload, tmp_path) -> Plan:
    plan = full_plan(profile)
    result = Executor(target, payload, tmp_path / "run.jsonl").run(plan, "run0001")
    assert result.succeeded, result.failure
    return plan


def test_every_step_kind_in_a_plan_has_a_probe(profile):
    for step in full_plan(profile):
        assert step.kind in PROBES


def test_a_freshly_commissioned_terminal_is_clean(profile, target, payload, tmp_path):
    plan = commission(profile, target, payload, tmp_path)
    report = reconcile(plan, target, payload)
    assert report.is_clean, report.lines()
    assert report.checked == len(plan)


def test_the_report_names_the_terminal_and_the_plan(profile, target, payload, tmp_path):
    plan = commission(profile, target, payload, tmp_path)
    report = reconcile(plan, target, payload)
    assert report.terminal == "LINE-PC-04"
    assert report.plan_digest == str(plan.digest())


def test_an_uncommissioned_terminal_is_missing_everything(profile, target, payload):
    report = reconcile(full_plan(profile), target, payload)
    assert not report.is_clean
    kinds = {record.kind for record in report}
    assert DriftKind.MISSING in kinds


def test_a_deleted_file_is_missing(profile, target, payload, tmp_path):
    plan = commission(profile, target, payload, tmp_path)
    target.remove_file(rf"{APPDIR}\V4T.exe")
    report = reconcile(plan, target, payload)
    missing = report.of_kind(DriftKind.MISSING)
    assert [r.subject for r in missing] == [rf"{APPDIR}\V4T.exe"]


def test_a_replaced_file_is_modified_not_missing(profile, target, payload, tmp_path):
    plan = commission(profile, target, payload, tmp_path)
    target.write_file(rf"{APPDIR}\V4T.exe", b"a different build")
    report = reconcile(plan, target, payload)
    assert report.of_kind(DriftKind.MISSING) == []
    modified = report.of_kind(DriftKind.MODIFIED)
    assert modified[0].subject == rf"{APPDIR}\V4T.exe"
    assert modified[0].expected != modified[0].actual


def test_a_removed_directory_is_missing(profile, target, payload, tmp_path):
    plan = commission(profile, target, payload, tmp_path)
    target.directories.discard("c:/sellenis/s-jet600")
    assert reconcile(plan, target, payload).of_kind(DriftKind.MISSING)


def test_a_control_that_is_present_but_unregistered(profile, target, payload, tmp_path):
    plan = commission(profile, target, payload, tmp_path)
    target.unregister_com(r"C:\Windows\SysWOW64\C1Sizer.ocx")
    report = reconcile(plan, target, payload)
    unregistered = report.of_kind(DriftKind.UNREGISTERED)
    assert unregistered[0].subject == r"C:\Windows\SysWOW64\C1Sizer.ocx"
    assert report.of_kind(DriftKind.MISSING) == []


def test_a_control_that_is_gone_entirely(profile, target, payload, tmp_path):
    plan = commission(profile, target, payload, tmp_path)
    target.remove_file(r"C:\Windows\SysWOW64\C1Sizer.ocx")
    report = reconcile(plan, target, payload)
    assert report.of_kind(DriftKind.MISSING)
    assert report.of_kind(DriftKind.UNREGISTERED) == []


def test_a_removed_assembly_is_missing(profile, target, payload, tmp_path):
    plan = commission(profile, target, payload, tmp_path)
    target.remove_assembly(MFC)
    report = reconcile(plan, target, payload)
    assert [r.subject for r in report.of_kind(DriftKind.MISSING)] == ["Microsoft.VC80.MFC"]


def test_a_removed_driver_is_missing(profile, target, payload, tmp_path):
    plan = commission(profile, target, payload, tmp_path)
    target.remove_driver("ftdibus.inf")
    assert reconcile(plan, target, payload).of_kind(DriftKind.MISSING)


def test_a_reprogrammed_controller_is_the_wrong_revision(profile, target, payload, tmp_path):
    plan = commission(profile, target, payload, tmp_path)
    target.installed_revision = "PCDC-1.2"
    report = reconcile(plan, target, payload)
    wrong = report.of_kind(DriftKind.WRONG_REVISION)
    assert wrong[0].expected == "PCDC-1.4"
    assert wrong[0].actual == "PCDC-1.2"


def test_a_blank_controller_is_missing(profile, target, payload, tmp_path):
    plan = commission(profile, target, payload, tmp_path)
    target.installed_revision = None
    assert reconcile(plan, target, payload).of_kind(DriftKind.MISSING)


def test_a_removed_font_is_missing(profile, target, payload, tmp_path):
    plan = commission(profile, target, payload, tmp_path)
    target.remove_font("OCR-B")
    assert [r.subject for r in reconcile(plan, target, payload).of_kind(DriftKind.MISSING)] == [
        "OCR-B"
    ]


def test_a_removed_shortcut_is_missing(profile, target, payload, tmp_path):
    plan = commission(profile, target, payload, tmp_path)
    target.remove_shortcut(
        ShortcutSpec(
            name="S-jet 600", directory=r"C:\Users\Public\Desktop", target=rf"{APPDIR}\V4T.exe"
        )
    )
    assert reconcile(plan, target, payload).of_kind(DriftKind.MISSING)


def test_a_changed_registry_value_is_reported(profile, target, payload, tmp_path):
    plan = commission(profile, target, payload, tmp_path)
    target.write_value(
        RegistryRoot.LOCAL_MACHINE, r"Software\Matricode\V4T", "Version", string_value("0.9.0")
    )
    report = reconcile(plan, target, payload)
    wrong = report.of_kind(DriftKind.WRONG_VALUE)
    assert wrong[0].expected == "1.0.0"
    assert wrong[0].actual == "0.9.0"


def test_a_removed_registry_value_is_missing(profile, target, payload, tmp_path):
    plan = commission(profile, target, payload, tmp_path)
    target.remove_value(RegistryRoot.LOCAL_MACHINE, r"Software\Matricode\V4T", "Version")
    report = reconcile(plan, target, payload)
    subjects = [r.subject for r in report.of_kind(DriftKind.MISSING)]
    assert r"HKLM\Software\Matricode\V4T\Version" in subjects


def test_an_edited_template_is_modified(profile, target, payload, tmp_path):
    plan = commission(profile, target, payload, tmp_path)
    target.write_file(rf"{APPDIR}\Templates\T1.spf", b"edited on the bench")
    report = reconcile(plan, target, payload)
    assert [r.subject for r in report.of_kind(DriftKind.MODIFIED)] == [
        rf"{APPDIR}\Templates\T1.spf"
    ]


def test_a_deleted_template_is_missing(profile, target, payload, tmp_path):
    plan = commission(profile, target, payload, tmp_path)
    target.remove_file(rf"{APPDIR}\Templates\T1.spf")
    assert reconcile(plan, target, payload).of_kind(DriftKind.MISSING)


def test_without_a_payload_a_file_can_only_be_reported_as_missing(profile, target, payload, tmp_path):
    plan = commission(profile, target, payload, tmp_path)
    target.write_file(rf"{APPDIR}\V4T.exe", b"a different build")
    report = reconcile(plan, target, None)
    assert report.of_kind(DriftKind.MODIFIED) == []
    assert report.of_kind(DriftKind.UNCHECKABLE)


def test_a_plan_digest_lets_a_file_be_checked_without_a_payload(profile, target):
    from stavewright.util.digest import digest_bytes

    plan = Plan(
        profile=profile,
        steps=[
            Step(
                step_id="file.v4t",
                kind=StepKind.COPY_FILE,
                title="copy V4T.exe",
                target=rf"{APPDIR}\V4T.exe",
                digest=digest_bytes(b"MZ\x90\x00"),
            )
        ],
    )
    target.write_file(rf"{APPDIR}\V4T.exe", b"MZ\x90\x00")
    assert reconcile(plan, target, None).is_clean
    target.write_file(rf"{APPDIR}\V4T.exe", b"different")
    assert reconcile(plan, target, None).of_kind(DriftKind.MODIFIED)


def test_a_redistributable_without_a_marker_is_uncheckable(profile, target):
    plan = Plan(
        profile=profile,
        steps=[
            Step(
                step_id="vcredist",
                kind=StepKind.RUN_REDISTRIBUTABLE,
                title="run vcredist",
                target=r"C:\stage\vcredist.exe",
                source="payload/vcredist.exe",
            )
        ],
    )
    report = reconcile(plan, target, None)
    assert report.of_kind(DriftKind.UNCHECKABLE)
    assert report.is_clean


def test_a_redistributable_with_a_marker_is_checkable(profile, target):
    plan = Plan(
        profile=profile,
        steps=[
            Step(
                step_id="vcredist",
                kind=StepKind.RUN_REDISTRIBUTABLE,
                title="run vcredist",
                target=r"C:\stage\vcredist.exe",
                source="payload/vcredist.exe",
                detail={"marker_key": r"Software\Microsoft\VC80"},
            )
        ],
    )
    assert reconcile(plan, target, None).of_kind(DriftKind.MISSING)
    target.write_value(
        RegistryRoot.LOCAL_MACHINE, r"Software\Microsoft\VC80", "Installed", string_value("1")
    )
    assert reconcile(plan, target, None).is_clean


def test_a_step_with_no_detail_is_uncheckable_rather_than_clean(profile, target):
    plan = Plan(
        profile=profile,
        steps=[
            Step(step_id="fpga", kind=StepKind.PROGRAM_FPGA, title="program the controller")
        ],
    )
    report = reconcile(plan, target, None)
    assert report.of_kind(DriftKind.UNCHECKABLE)
    assert not report.of_kind(DriftKind.MISSING)


def test_only_checks_the_steps_it_is_asked_to(profile, target, payload, tmp_path):
    plan = commission(profile, target, payload, tmp_path)
    target.remove_file(rf"{APPDIR}\V4T.exe")
    report = reconcile(plan, target, payload, only={"reg.version"})
    assert report.checked == 1
    assert report.is_clean


def test_extra_files_are_found(profile, target, payload, tmp_path):
    plan = commission(profile, target, payload, tmp_path)
    target.write_file(rf"{APPDIR}\somebodys-tool.exe", b"MZ")
    extra = find_extra_files(plan, target, APPDIR)
    assert [record.kind for record in extra] == [DriftKind.EXTRA]
    assert extra[0].subject.lower().endswith("somebodys-tool.exe")


def test_a_subdirectory_the_plan_created_is_not_extra(profile, target, payload, tmp_path):
    plan = commission(profile, target, payload, tmp_path)
    assert target.is_directory(rf"{APPDIR}\Templates")
    subjects = [record.subject.lower() for record in find_extra_files(plan, target, APPDIR)]
    assert not any("templates" in subject for subject in subjects)


def test_planned_files_are_not_extra(profile, target, payload, tmp_path):
    plan = commission(profile, target, payload, tmp_path)
    subjects = [record.subject.lower() for record in find_extra_files(plan, target, APPDIR)]
    assert not any("v4t.exe" in subject for subject in subjects)


def test_extra_files_do_not_make_a_report_dirty(profile, target, payload, tmp_path):
    plan = commission(profile, target, payload, tmp_path)
    target.write_file(rf"{APPDIR}\somebodys-tool.exe", b"MZ")
    report = reconcile(plan, target, payload)
    report.records.extend(find_extra_files(plan, target, APPDIR))
    assert report.is_clean
    assert report.of_kind(DriftKind.EXTRA)


def test_an_empty_directory_has_no_extras(profile, target):
    assert find_extra_files(full_plan(profile), target, APPDIR) == []


def test_several_kinds_of_drift_at_once(profile, target, payload, tmp_path):
    plan = commission(profile, target, payload, tmp_path)
    target.remove_file(rf"{APPDIR}\V4T.exe")
    target.unregister_com(r"C:\Windows\SysWOW64\C1Sizer.ocx")
    target.installed_revision = "PCDC-1.2"
    target.remove_font("OCR-B")
    report = reconcile(plan, target, payload)
    assert report.counts() == {"missing": 2, "unregistered": 1, "wrong-revision": 1}
    assert len(report.errors) == 4
