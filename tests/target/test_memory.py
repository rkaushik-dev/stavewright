from __future__ import annotations

import pytest

from v4t_setup.model.registry import RegistryRoot, RegistryValueType
from v4t_setup.target.base import (
    AssemblyIdentity,
    ShortcutSpec,
    TargetError,
    TargetSystem,
)
from v4t_setup.target.memory import (
    MemoryTarget,
    dword_value,
    string_value,
)
from v4t_setup.util.digest import digest_bytes
from v4t_setup.util.winpath import fold_key, fold_path

APPDIR = r"C:\Sellenis\S-jet600"
EXE = rf"{APPDIR}\V4T.exe"
MFC = AssemblyIdentity(
    name="Microsoft.VC80.MFC", version="8.0.50727.6195", public_key_token="1fc8b3b9a1e18e3b"
)


@pytest.fixture
def target() -> MemoryTarget:
    return MemoryTarget()


def test_the_memory_target_satisfies_the_protocol(target):
    assert isinstance(target, TargetSystem)


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (r"C:\Sellenis\S-jet600", "c:/sellenis/s-jet600"),
        ("C:/Sellenis/S-jet600/", "c:/sellenis/s-jet600"),
        (r"C:\SELLENIS\s-JET600", "c:/sellenis/s-jet600"),
        ("/", "/"),
        ("", "/"),
    ],
)
def test_path_normalisation(given, expected):
    assert fold_path(given) == expected


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (r"Software\Matricode\V4T", "software\\matricode\\v4t"),
        ("Software/Matricode/V4T", "software\\matricode\\v4t"),
        (r"\Software\Matricode\V4T\\", "software\\matricode\\v4t"),
    ],
)
def test_key_normalisation(given, expected):
    assert fold_key(given) == expected


def test_writing_a_file_creates_its_parents(target):
    assert target.write_file(EXE, b"MZ")
    assert target.path_exists(EXE)
    assert target.is_directory(APPDIR)
    assert target.is_directory("C:\\Sellenis")


def test_paths_compare_case_insensitively(target):
    target.write_file(EXE, b"MZ")
    assert target.path_exists(r"c:\sellenis\s-jet600\v4t.exe")
    assert target.read_file(r"C:/Sellenis/S-jet600/V4T.exe") == b"MZ"


def test_rewriting_the_same_content_reports_no_change(target):
    assert target.write_file(EXE, b"MZ")
    assert not target.write_file(EXE, b"MZ")


def test_rewriting_different_content_reports_a_change(target):
    target.write_file(EXE, b"MZ")
    assert target.write_file(EXE, b"MZ\x90")


def test_reading_a_missing_file_is_an_error(target):
    with pytest.raises(TargetError, match="no such file"):
        target.read_file(EXE)


def test_removing_a_file(target):
    target.write_file(EXE, b"MZ")
    assert target.remove_file(EXE)
    assert not target.remove_file(EXE)
    assert not target.path_exists(EXE)


def test_file_digest(target):
    assert target.file_digest(EXE) is None
    target.write_file(EXE, b"MZ")
    assert target.file_digest(EXE) == digest_bytes(b"MZ")


def test_making_a_directory_is_idempotent(target):
    assert target.make_directory(APPDIR)
    assert not target.make_directory(APPDIR)


def test_a_directory_cannot_replace_a_file(target):
    target.write_file(EXE, b"MZ")
    with pytest.raises(TargetError, match="a file already exists"):
        target.make_directory(EXE)


def test_a_file_cannot_replace_a_directory(target):
    target.make_directory(APPDIR)
    with pytest.raises(TargetError, match="a directory already exists"):
        target.write_file(APPDIR, b"MZ")


def test_a_file_cannot_be_written_below_a_file(target):
    target.write_file(EXE, b"MZ")
    with pytest.raises(TargetError, match="cannot write below a file"):
        target.write_file(rf"{EXE}\nested.dll", b"MZ")


def test_removing_a_directory_takes_its_contents(target):
    target.write_file(EXE, b"MZ")
    target.write_file(rf"{APPDIR}\Templates\T1.spf", b"spf")
    assert target.remove_directory(APPDIR)
    assert not target.path_exists(EXE)
    assert not target.path_exists(rf"{APPDIR}\Templates")


def test_removing_a_missing_directory_reports_nothing(target):
    assert not target.remove_directory(APPDIR)


def test_listing_a_directory(target):
    target.write_file(EXE, b"MZ")
    target.write_file(rf"{APPDIR}\qu_imager.dll", b"MZ")
    target.make_directory(rf"{APPDIR}\Templates")
    # Names come back folded, so the sort is on the folded form.
    assert target.list_directory(APPDIR) == ["qu_imager.dll", "templates", "v4t.exe"]


def test_listing_a_missing_directory_is_empty(target):
    assert target.list_directory(APPDIR) == []


def test_writing_a_registry_value_creates_the_key(target):
    assert target.write_value(
        RegistryRoot.LOCAL_MACHINE, r"Software\Matricode\V4T", "Version", string_value("1.0.0")
    )
    assert target.key_exists(RegistryRoot.LOCAL_MACHINE, r"Software\Matricode\V4T")
    assert target.key_exists(RegistryRoot.LOCAL_MACHINE, "Software")


def test_registry_keys_compare_case_insensitively(target):
    target.write_value(
        RegistryRoot.LOCAL_MACHINE, r"Software\Matricode\V4T", "Version", string_value("1.0.0")
    )
    assert target.key_exists(RegistryRoot.LOCAL_MACHINE, r"SOFTWARE\matricode\v4t")


def test_registry_value_names_compare_case_insensitively(target):
    target.write_value(
        RegistryRoot.LOCAL_MACHINE, r"Software\Matricode\V4T", "Version", string_value("1.0.0")
    )
    read = target.read_value(RegistryRoot.LOCAL_MACHINE, r"Software\Matricode\V4T", "VERSION")
    assert read == string_value("1.0.0")


def test_different_hives_do_not_collide(target):
    target.write_value(RegistryRoot.LOCAL_MACHINE, "Software\\X", "V", string_value("machine"))
    target.write_value(RegistryRoot.CURRENT_USER, "Software\\X", "V", string_value("user"))
    assert target.read_value(RegistryRoot.LOCAL_MACHINE, "Software\\X", "V").data == "machine"
    assert target.read_value(RegistryRoot.CURRENT_USER, "Software\\X", "V").data == "user"


def test_rewriting_the_same_registry_value_reports_no_change(target):
    target.write_value(RegistryRoot.LOCAL_MACHINE, "Software\\X", "V", string_value("a"))
    assert not target.write_value(
        RegistryRoot.LOCAL_MACHINE, "Software\\X", "V", string_value("a")
    )


def test_a_dword_and_a_string_of_the_same_text_are_different(target):
    target.write_value(RegistryRoot.LOCAL_MACHINE, "Software\\X", "V", dword_value(0))
    assert target.write_value(
        RegistryRoot.LOCAL_MACHINE, "Software\\X", "V", string_value("0")
    )


def test_reading_a_missing_key_is_none(target):
    assert target.read_value(RegistryRoot.LOCAL_MACHINE, "Software\\Nope", "V") is None


def test_reading_a_missing_value_is_none(target):
    target.create_key(RegistryRoot.LOCAL_MACHINE, "Software\\X")
    assert target.read_value(RegistryRoot.LOCAL_MACHINE, "Software\\X", "V") is None


def test_the_default_value_is_addressable_as_none(target):
    target.write_value(RegistryRoot.CLASSES_ROOT, "V4T.Job", None, string_value("Job"))
    assert target.read_value(RegistryRoot.CLASSES_ROOT, "V4T.Job", None).data == "Job"
    assert target.list_values(RegistryRoot.CLASSES_ROOT, "V4T.Job") == [""]


def test_removing_a_registry_value(target):
    target.write_value(RegistryRoot.LOCAL_MACHINE, "Software\\X", "V", string_value("a"))
    assert target.remove_value(RegistryRoot.LOCAL_MACHINE, "Software\\X", "V")
    assert not target.remove_value(RegistryRoot.LOCAL_MACHINE, "Software\\X", "V")


def test_removing_a_key_takes_its_subkeys(target):
    target.write_value(
        RegistryRoot.LOCAL_MACHINE, r"Software\Matricode\V4T\account", "value", string_value("1234")
    )
    assert target.remove_key(RegistryRoot.LOCAL_MACHINE, r"Software\Matricode")
    assert not target.key_exists(RegistryRoot.LOCAL_MACHINE, r"Software\Matricode\V4T")
    assert target.key_exists(RegistryRoot.LOCAL_MACHINE, "Software")


def test_list_values_is_sorted(target):
    for name in ("Version", "Path", "Account"):
        target.write_value(RegistryRoot.LOCAL_MACHINE, "Software\\X", name, string_value("v"))
    assert target.list_values(RegistryRoot.LOCAL_MACHINE, "Software\\X") == [
        "account",
        "path",
        "version",
    ]


def test_registering_a_com_server(target):
    target.write_file(r"C:\Windows\SysWOW64\C1Sizer.ocx", b"MZ")
    assert target.register_com(r"C:\Windows\SysWOW64\C1Sizer.ocx")
    assert target.is_com_registered(r"C:\Windows\SysWOW64\C1Sizer.ocx")
    assert not target.register_com(r"C:\Windows\SysWOW64\C1Sizer.ocx")


def test_registering_something_that_is_not_there_fails(target):
    with pytest.raises(TargetError, match="not there"):
        target.register_com(r"C:\Windows\SysWOW64\C1Sizer.ocx")


def test_unregistering(target):
    target.write_file(r"C:\Windows\SysWOW64\C1Sizer.ocx", b"MZ")
    target.register_com(r"C:\Windows\SysWOW64\C1Sizer.ocx")
    assert target.unregister_com(r"C:\Windows\SysWOW64\C1Sizer.ocx")
    assert not target.unregister_com(r"C:\Windows\SysWOW64\C1Sizer.ocx")


def test_installing_an_assembly(target):
    assert target.install_assembly(MFC, "payload/winsxs/mfc")
    assert target.has_assembly(MFC)
    assert not target.install_assembly(MFC, "payload/winsxs/mfc")


def test_a_different_version_is_a_different_assembly(target):
    target.install_assembly(MFC, "src")
    other = AssemblyIdentity(
        name=MFC.name, version="8.0.50727.762", public_key_token=MFC.public_key_token
    )
    assert not target.has_assembly(other)


def test_a_policy_needs_its_assembly(target):
    with pytest.raises(TargetError, match="no assembly for policy"):
        target.install_policy(MFC, "src")


def test_a_policy_installs_once_the_assembly_is_there(target):
    target.install_assembly(MFC, "src")
    assert target.install_policy(MFC, "src")
    assert target.has_policy(MFC)


def test_removing_an_assembly_and_a_policy(target):
    target.install_assembly(MFC, "src")
    target.install_policy(MFC, "src")
    assert target.remove_policy(MFC)
    assert target.remove_assembly(MFC)
    assert not target.remove_assembly(MFC)


def test_staging_a_driver(target):
    assert target.stage_driver("payload/ftdimfr", "ftdibus.inf")
    assert target.has_driver("FTDIBUS.INF")
    assert not target.stage_driver("payload/ftdimfr", "ftdibus.inf")
    assert target.remove_driver("ftdibus.inf")


def test_programming_the_controller(target):
    assert target.controller_revision() is None
    assert target.program_controller("payload/pcdc_top.rbf", "PCDC-1.4")
    assert target.controller_revision() == "PCDC-1.4"
    assert not target.program_controller("payload/pcdc_top.rbf", "PCDC-1.4")


def test_programming_a_different_revision_is_a_change(target):
    target.program_controller("src", "PCDC-1.3")
    assert target.program_controller("src", "PCDC-1.4")


def test_programming_without_a_controller_fails(target):
    target.has_controller = False
    with pytest.raises(TargetError, match="no controller board"):
        target.program_controller("src", "PCDC-1.4")


def test_installing_a_font(target):
    assert target.install_font("payload/ocrb.ttf", "OCR-B")
    assert target.has_font("ocr-b")
    assert not target.install_font("payload/ocrb.ttf", "OCR-B")
    assert target.remove_font("OCR-B")
    assert not target.has_font("OCR-B")


def test_fonts_show_up_in_the_facts(target):
    target.install_font("src", "OCR-B")
    assert "OCR-B" in target.facts().installed_fonts


def test_creating_a_shortcut(target):
    spec = ShortcutSpec(
        name="S-jet 600", directory=r"C:\Users\Public\Desktop", target=EXE
    )
    assert target.create_shortcut(spec)
    assert target.has_shortcut(spec)
    assert not target.create_shortcut(spec)
    assert target.remove_shortcut(spec)


def test_shortcuts_in_different_directories_are_different(target):
    desktop = ShortcutSpec(name="S-jet 600", directory=r"C:\Users\Public\Desktop", target=EXE)
    start = ShortcutSpec(name="S-jet 600", directory=r"C:\ProgramData\Start", target=EXE)
    target.create_shortcut(desktop)
    assert not target.has_shortcut(start)


def test_running_an_installer(target):
    target.write_file(r"C:\stage\vcredist_x86.exe", b"MZ")
    assert target.run_installer(r"C:\stage\vcredist_x86.exe", ["/q"]) == 0


def test_an_installer_can_be_made_to_fail(target):
    target.write_file(r"C:\stage\vcredist_x86.exe", b"MZ")
    target.installer_exit_codes[fold_path(r"C:\stage\vcredist_x86.exe")] = 1603
    assert target.run_installer(r"C:\stage\vcredist_x86.exe", []) == 1603


def test_running_something_that_is_not_there_fails(target):
    with pytest.raises(TargetError, match="not there"):
        target.run_installer(r"C:\stage\missing.exe", [])


def test_facts_come_from_the_fields(target):
    target.os_build = 9600
    target.free_bytes = 1024
    target.is_elevated = False
    facts = target.facts()
    assert facts.os_build == 9600
    assert facts.free_bytes == 1024
    assert not facts.is_elevated
    assert facts.architecture == "x64"


def test_calls_are_recorded(target):
    target.write_file(EXE, b"MZ")
    target.write_value(RegistryRoot.LOCAL_MACHINE, "Software\\X", "V", string_value("a"))
    assert any(call.startswith("write ") for call in target.calls)
    assert any(call.startswith("setvalue ") for call in target.calls)


def test_a_no_op_write_records_nothing(target):
    target.write_file(EXE, b"MZ")
    target.calls.clear()
    target.write_file(EXE, b"MZ")
    assert target.calls == []


def test_seed_file_leaves_no_trace_in_the_call_log(target):
    target.seed_file(EXE, b"MZ")
    assert target.calls == []
    assert target.path_exists(EXE)


def test_snapshots_compare_equal_for_equal_state(target):
    other = MemoryTarget()
    for machine in (target, other):
        machine.write_file(EXE, b"MZ")
        machine.write_value(RegistryRoot.LOCAL_MACHINE, "Software\\X", "V", string_value("a"))
    assert target.snapshot() == other.snapshot()


def test_snapshots_differ_when_content_differs(target):
    other = MemoryTarget()
    target.write_file(EXE, b"MZ")
    other.write_file(EXE, b"MZ\x90")
    assert target.snapshot() != other.snapshot()


def test_the_value_helpers():
    assert string_value("x").type is RegistryValueType.STRING
    assert dword_value(3).type is RegistryValueType.DWORD
    assert dword_value(3).data == 3
