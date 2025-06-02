from __future__ import annotations

import pytest

from v4t_setup.apply.actions import (
    ACTIONS,
    CopyFileAction,
    CreateDirectoryAction,
    CreateShortcutAction,
    DirectoryPayload,
    InstallFontAction,
    InstallSxsAssemblyAction,
    InstallSxsPolicyAction,
    InstallTemplateSetAction,
    ProgramFpgaAction,
    RegisterComAction,
    RunRedistributableAction,
    StageDriverAction,
    WriteRegistryAction,
    action_for,
)
from v4t_setup.errors import ApplyError
from v4t_setup.model.registry import RegistryRoot, RegistryValueType
from v4t_setup.plan.steps import Step, StepKind
from v4t_setup.target.memory import MemoryTarget, string_value
from v4t_setup.util.digest import digest_bytes

APPDIR = r"C:\Sellenis\S-jet600"
MFC_DETAIL = {
    "assembly": "Microsoft.VC80.MFC",
    "version": "8.0.50727.6195",
    "public_key_token": "1fc8b3b9a1e18e3b",
}


@pytest.fixture
def target():
    return MemoryTarget()


@pytest.fixture
def payload(tmp_path):
    (tmp_path / "payload").mkdir()
    (tmp_path / "payload" / "V4T.exe").write_bytes(b"MZ\x90\x00")
    (tmp_path / "payload" / "C1Sizer.ocx").write_bytes(b"MZocx")
    (tmp_path / "payload" / "ocrb.ttf").write_bytes(b"ttf")
    (tmp_path / "payload" / "vcredist.exe").write_bytes(b"MZvc")
    (tmp_path / "payload" / "winsxs").mkdir()
    (tmp_path / "payload" / "Templates").mkdir()
    (tmp_path / "payload" / "Templates" / "T1.spf").write_bytes(b"spf-one")
    (tmp_path / "payload" / "Templates" / "GS1-1.txt").write_bytes(b"csv")
    (tmp_path / "payload" / "ftdimfr").mkdir()
    return DirectoryPayload(tmp_path)


def step(kind: StepKind, **kwargs) -> Step:
    kwargs.setdefault("title", "a step")
    kwargs.setdefault("step_id", "s1")
    return Step(kind=kind, **kwargs)


def test_every_step_kind_has_an_action():
    assert set(ACTIONS) == set(StepKind) - {StepKind.REMOVE_OLD_PRODUCT, StepKind.SET_PATH}


def test_action_for_finds_the_handler():
    assert isinstance(action_for(StepKind.COPY_FILE), CopyFileAction)


def test_action_for_reports_an_unhandled_kind():
    with pytest.raises(ApplyError, match="no action implements"):
        action_for(StepKind.SET_PATH)


def test_payload_reads_from_the_staging_tree(payload):
    assert payload.exists("payload/V4T.exe")
    assert payload.read("payload/V4T.exe") == b"MZ\x90\x00"


def test_payload_refuses_to_climb_out(payload):
    with pytest.raises(ApplyError, match="climbs out of the staging tree"):
        payload.read("../secrets")


def test_payload_reports_a_missing_file(payload):
    with pytest.raises(ApplyError, match="cannot read payload"):
        payload.read("payload/missing.dll")


def test_payload_accepts_backslashes(payload):
    assert payload.read(r"payload\V4T.exe") == b"MZ\x90\x00"


def test_create_directory(target, payload):
    action = CreateDirectoryAction()
    s = step(StepKind.CREATE_DIRECTORY, target=rf"{APPDIR}\Templates")
    assert not action.is_satisfied(target, s, payload)
    assert action.apply(target, s, payload)
    assert action.is_satisfied(target, s, payload)
    assert not action.apply(target, s, payload)
    assert action.revert(target, s, payload)
    assert not action.is_satisfied(target, s, payload)


def test_a_step_without_a_target_is_refused(target, payload):
    with pytest.raises(ApplyError, match="no target to act on"):
        CreateDirectoryAction().apply(target, step(StepKind.CREATE_DIRECTORY), payload)


def test_a_step_without_a_source_is_refused(target, payload):
    with pytest.raises(ApplyError, match="no source to install from"):
        CopyFileAction().apply(
            target, step(StepKind.COPY_FILE, target=rf"{APPDIR}\V4T.exe"), payload
        )


def test_copy_file(target, payload):
    action = CopyFileAction()
    s = step(StepKind.COPY_FILE, target=rf"{APPDIR}\V4T.exe", source="payload/V4T.exe")
    assert not action.is_satisfied(target, s, payload)
    assert action.apply(target, s, payload)
    assert action.is_satisfied(target, s, payload)
    assert not action.apply(target, s, payload)
    assert target.read_file(rf"{APPDIR}\V4T.exe") == b"MZ\x90\x00"
    assert action.revert(target, s, payload)


def test_copy_file_checks_a_declared_digest(target, payload):
    action = CopyFileAction()
    s = step(
        StepKind.COPY_FILE,
        target=rf"{APPDIR}\V4T.exe",
        source="payload/V4T.exe",
        digest=digest_bytes(b"MZ\x90\x00"),
    )
    assert action.apply(target, s, payload)
    assert action.is_satisfied(target, s, payload)


def test_copy_file_refuses_payload_that_does_not_match_its_digest(target, payload):
    action = CopyFileAction()
    s = step(
        StepKind.COPY_FILE,
        target=rf"{APPDIR}\V4T.exe",
        source="payload/V4T.exe",
        digest=digest_bytes(b"something else"),
    )
    with pytest.raises(ApplyError, match="does not match the digest"):
        action.apply(target, s, payload)
    assert not target.path_exists(rf"{APPDIR}\V4T.exe")


def test_a_file_with_the_wrong_content_is_not_satisfied(target, payload):
    action = CopyFileAction()
    s = step(StepKind.COPY_FILE, target=rf"{APPDIR}\V4T.exe", source="payload/V4T.exe")
    target.write_file(rf"{APPDIR}\V4T.exe", b"an older build")
    assert not action.is_satisfied(target, s, payload)
    assert action.apply(target, s, payload)


def test_write_registry(target, payload):
    action = WriteRegistryAction()
    s = step(
        StepKind.WRITE_REGISTRY,
        detail={
            "root": int(RegistryRoot.LOCAL_MACHINE),
            "key": r"Software\Matricode\V4T",
            "name": "Version",
            "value": "1.0.0",
            "value_type": RegistryValueType.STRING.value,
        },
    )
    assert not action.is_satisfied(target, s, payload)
    assert action.apply(target, s, payload)
    assert action.is_satisfied(target, s, payload)
    assert not action.apply(target, s, payload)
    assert target.read_value(
        RegistryRoot.LOCAL_MACHINE, r"Software\Matricode\V4T", "Version"
    ) == string_value("1.0.0")
    assert action.revert(target, s, payload)


def test_a_registry_step_without_a_key_is_refused(target, payload):
    with pytest.raises(ApplyError, match="missing its root or key"):
        WriteRegistryAction().apply(target, step(StepKind.WRITE_REGISTRY), payload)


def test_register_com_writes_the_file_then_registers(target, payload):
    action = RegisterComAction()
    s = step(
        StepKind.REGISTER_COM,
        target=r"C:\Windows\SysWOW64\C1Sizer.ocx",
        source="payload/C1Sizer.ocx",
    )
    assert not action.is_satisfied(target, s, payload)
    assert action.apply(target, s, payload)
    assert action.is_satisfied(target, s, payload)
    assert target.is_com_registered(r"C:\Windows\SysWOW64\C1Sizer.ocx")
    assert not action.apply(target, s, payload)


def test_reverting_com_registration_removes_the_file_too(target, payload):
    action = RegisterComAction()
    s = step(
        StepKind.REGISTER_COM,
        target=r"C:\Windows\SysWOW64\C1Sizer.ocx",
        source="payload/C1Sizer.ocx",
    )
    action.apply(target, s, payload)
    assert action.revert(target, s, payload)
    assert not target.path_exists(r"C:\Windows\SysWOW64\C1Sizer.ocx")


def test_a_file_present_but_not_registered_is_not_satisfied(target, payload):
    action = RegisterComAction()
    s = step(
        StepKind.REGISTER_COM,
        target=r"C:\Windows\SysWOW64\C1Sizer.ocx",
        source="payload/C1Sizer.ocx",
    )
    target.write_file(r"C:\Windows\SysWOW64\C1Sizer.ocx", b"MZocx")
    assert not action.is_satisfied(target, s, payload)


def test_install_assembly(target, payload):
    action = InstallSxsAssemblyAction()
    s = step(StepKind.INSTALL_SXS_ASSEMBLY, source="payload/winsxs", detail=MFC_DETAIL)
    assert not action.is_satisfied(target, s, payload)
    assert action.apply(target, s, payload)
    assert action.is_satisfied(target, s, payload)
    assert not action.apply(target, s, payload)
    assert action.revert(target, s, payload)


def test_an_assembly_step_without_an_identity_is_refused(target, payload):
    with pytest.raises(ApplyError, match="missing its identity"):
        InstallSxsAssemblyAction().apply(
            target, step(StepKind.INSTALL_SXS_ASSEMBLY, source="payload/winsxs"), payload
        )


def test_install_policy_needs_its_assembly(target, payload):
    from v4t_setup.target.base import TargetError

    s = step(StepKind.INSTALL_SXS_POLICY, source="payload/winsxs", detail=MFC_DETAIL)
    with pytest.raises(TargetError, match="no assembly for policy"):
        InstallSxsPolicyAction().apply(target, s, payload)


def test_install_policy_once_the_assembly_is_there(target, payload):
    assembly = step(StepKind.INSTALL_SXS_ASSEMBLY, source="payload/winsxs", detail=MFC_DETAIL)
    policy = step(StepKind.INSTALL_SXS_POLICY, source="payload/winsxs", detail=MFC_DETAIL)
    InstallSxsAssemblyAction().apply(target, assembly, payload)
    action = InstallSxsPolicyAction()
    assert action.apply(target, policy, payload)
    assert action.is_satisfied(target, policy, payload)
    assert action.revert(target, policy, payload)


def test_stage_driver(target, payload):
    action = StageDriverAction()
    s = step(StepKind.STAGE_DRIVER, source="payload/ftdimfr", detail={"inf": "ftdibus.inf"})
    assert not action.is_satisfied(target, s, payload)
    assert action.apply(target, s, payload)
    assert action.is_satisfied(target, s, payload)
    assert not action.apply(target, s, payload)
    assert action.revert(target, s, payload)


def test_a_driver_step_without_an_inf_is_refused(target, payload):
    with pytest.raises(ApplyError, match="does not name its INF"):
        StageDriverAction().apply(
            target, step(StepKind.STAGE_DRIVER, source="payload/ftdimfr"), payload
        )


def test_program_fpga(target, payload):
    action = ProgramFpgaAction()
    s = step(
        StepKind.PROGRAM_FPGA, source="payload/V4T.exe", detail={"revision": "PCDC-1.4"}
    )
    assert not action.is_satisfied(target, s, payload)
    assert action.apply(target, s, payload)
    assert action.is_satisfied(target, s, payload)
    assert not action.apply(target, s, payload)


def test_program_fpga_is_not_reversible(target, payload):
    action = ProgramFpgaAction()
    assert not action.reversible
    s = step(
        StepKind.PROGRAM_FPGA, source="payload/V4T.exe", detail={"revision": "PCDC-1.4"}
    )
    with pytest.raises(ApplyError, match="cannot be rolled back"):
        action.revert(target, s, payload)


def test_a_bitstream_step_without_a_revision_is_refused(target, payload):
    with pytest.raises(ApplyError, match="does not name a revision"):
        ProgramFpgaAction().apply(
            target, step(StepKind.PROGRAM_FPGA, source="payload/V4T.exe"), payload
        )


def test_install_font(target, payload):
    action = InstallFontAction()
    s = step(StepKind.INSTALL_FONT, source="payload/ocrb.ttf", detail={"font": "OCR-B"})
    assert not action.is_satisfied(target, s, payload)
    assert action.apply(target, s, payload)
    assert action.is_satisfied(target, s, payload)
    assert action.revert(target, s, payload)


def test_a_font_name_falls_back_to_the_file_stem(target, payload):
    action = InstallFontAction()
    s = step(StepKind.INSTALL_FONT, source="payload/ocrb.ttf")
    action.apply(target, s, payload)
    assert target.has_font("ocrb")


def test_create_shortcut(target, payload):
    action = CreateShortcutAction()
    s = step(
        StepKind.CREATE_SHORTCUT,
        detail={
            "name": "S-jet 600",
            "directory": r"C:\Users\Public\Desktop",
            "target": rf"{APPDIR}\V4T.exe",
        },
    )
    assert not action.is_satisfied(target, s, payload)
    assert action.apply(target, s, payload)
    assert action.is_satisfied(target, s, payload)
    assert not action.apply(target, s, payload)
    assert action.revert(target, s, payload)


def test_a_shortcut_step_without_a_target_is_refused(target, payload):
    with pytest.raises(ApplyError, match="missing its directory or target"):
        CreateShortcutAction().apply(
            target, step(StepKind.CREATE_SHORTCUT, detail={"directory": "C:\\x"}), payload
        )


def test_install_template_set(target, payload):
    action = InstallTemplateSetAction()
    s = step(
        StepKind.INSTALL_TEMPLATE_SET,
        target=rf"{APPDIR}\Templates\Templates",
        source="payload/Templates",
        detail={"files": ["T1.spf", "GS1-1.txt"]},
    )
    assert not action.is_satisfied(target, s, payload)
    assert action.apply(target, s, payload)
    assert action.is_satisfied(target, s, payload)
    assert not action.apply(target, s, payload)
    assert target.read_file(rf"{APPDIR}\Templates\T1.spf") == b"spf-one"
    assert action.revert(target, s, payload)
    assert not target.path_exists(rf"{APPDIR}\Templates\T1.spf")


def test_a_half_installed_template_set_is_not_satisfied(target, payload):
    action = InstallTemplateSetAction()
    s = step(
        StepKind.INSTALL_TEMPLATE_SET,
        target=rf"{APPDIR}\Templates\Templates",
        source="payload/Templates",
        detail={"files": ["T1.spf", "GS1-1.txt"]},
    )
    action.apply(target, s, payload)
    target.remove_file(rf"{APPDIR}\Templates\GS1-1.txt")
    assert not action.is_satisfied(target, s, payload)


def test_a_template_set_with_a_changed_file_is_not_satisfied(target, payload):
    action = InstallTemplateSetAction()
    s = step(
        StepKind.INSTALL_TEMPLATE_SET,
        target=rf"{APPDIR}\Templates\Templates",
        source="payload/Templates",
        detail={"files": ["T1.spf"]},
    )
    action.apply(target, s, payload)
    target.write_file(rf"{APPDIR}\Templates\T1.spf", b"edited on the bench")
    assert not action.is_satisfied(target, s, payload)


def test_a_template_set_with_no_files_is_refused(target, payload):
    with pytest.raises(ApplyError, match="lists no files"):
        InstallTemplateSetAction().apply(
            target,
            step(
                StepKind.INSTALL_TEMPLATE_SET,
                target=rf"{APPDIR}\Templates\Templates",
                source="payload/Templates",
            ),
            payload,
        )


def test_run_redistributable(target, payload):
    action = RunRedistributableAction()
    s = step(
        StepKind.RUN_REDISTRIBUTABLE,
        target=r"C:\stage\vcredist.exe",
        source="payload/vcredist.exe",
    )
    assert not action.is_satisfied(target, s, payload)
    assert action.apply(target, s, payload)
    assert any(call.startswith("run ") for call in target.calls)


def test_a_redistributable_with_a_marker_can_be_detected(target, payload):
    action = RunRedistributableAction()
    s = step(
        StepKind.RUN_REDISTRIBUTABLE,
        target=r"C:\stage\vcredist.exe",
        source="payload/vcredist.exe",
        detail={"marker_key": r"Software\Microsoft\VC80", "marker_name": "Installed"},
    )
    assert not action.is_satisfied(target, s, payload)
    target.write_value(
        RegistryRoot.LOCAL_MACHINE, r"Software\Microsoft\VC80", "Installed", string_value("1")
    )
    assert action.is_satisfied(target, s, payload)


def test_a_reboot_required_exit_code_is_accepted(target, payload):
    from v4t_setup.target.memory import normalise_path

    action = RunRedistributableAction()
    s = step(
        StepKind.RUN_REDISTRIBUTABLE,
        target=r"C:\stage\vcredist.exe",
        source="payload/vcredist.exe",
    )
    target.installer_exit_codes[normalise_path(r"C:\stage\vcredist.exe")] = 3010
    assert action.apply(target, s, payload)


def test_an_unexpected_exit_code_is_an_error(target, payload):
    from v4t_setup.target.memory import normalise_path

    action = RunRedistributableAction()
    s = step(
        StepKind.RUN_REDISTRIBUTABLE,
        target=r"C:\stage\vcredist.exe",
        source="payload/vcredist.exe",
    )
    target.installer_exit_codes[normalise_path(r"C:\stage\vcredist.exe")] = 1603
    with pytest.raises(ApplyError, match="unexpected exit code") as excinfo:
        action.apply(target, s, payload)
    assert excinfo.value.context["exit_code"] == 1603


def test_the_redistributable_is_not_reversible(target, payload):
    action = RunRedistributableAction()
    assert not action.reversible
    s = step(
        StepKind.RUN_REDISTRIBUTABLE,
        target=r"C:\stage\vcredist.exe",
        source="payload/vcredist.exe",
    )
    with pytest.raises(ApplyError, match="not ours to remove"):
        action.revert(target, s, payload)


def test_every_action_is_idempotent(target, payload):
    # Apply twice, and the second time must report no change. This is the
    # property a resumed run depends on.
    cases = [
        (CreateDirectoryAction(), step(StepKind.CREATE_DIRECTORY, target=APPDIR)),
        (
            CopyFileAction(),
            step(StepKind.COPY_FILE, target=rf"{APPDIR}\V4T.exe", source="payload/V4T.exe"),
        ),
        (
            InstallSxsAssemblyAction(),
            step(StepKind.INSTALL_SXS_ASSEMBLY, source="payload/winsxs", detail=MFC_DETAIL),
        ),
        (
            StageDriverAction(),
            step(StepKind.STAGE_DRIVER, source="payload/ftdimfr", detail={"inf": "f.inf"}),
        ),
        (
            InstallFontAction(),
            step(StepKind.INSTALL_FONT, source="payload/ocrb.ttf", detail={"font": "OCR-B"}),
        ),
    ]
    for action, s in cases:
        assert action.apply(target, s, payload), s.kind
        assert not action.apply(target, s, payload), s.kind


def test_reverting_something_that_was_never_applied_is_safe(target, payload):
    cases = [
        (CreateDirectoryAction(), step(StepKind.CREATE_DIRECTORY, target=APPDIR)),
        (CopyFileAction(), step(StepKind.COPY_FILE, target=rf"{APPDIR}\V4T.exe")),
        (
            InstallSxsAssemblyAction(),
            step(StepKind.INSTALL_SXS_ASSEMBLY, source="payload/winsxs", detail=MFC_DETAIL),
        ),
        (
            StageDriverAction(),
            step(StepKind.STAGE_DRIVER, source="payload/ftdimfr", detail={"inf": "f.inf"}),
        ),
    ]
    for action, s in cases:
        assert not action.revert(target, s, payload), s.kind


def test_the_base_action_is_abstract(target, payload):
    from v4t_setup.apply.actions import Action

    for method in ("is_satisfied", "apply", "revert"):
        with pytest.raises(NotImplementedError):
            getattr(Action(), method)(target, step(StepKind.COPY_FILE), payload)
