from __future__ import annotations

import pytest

from stavewright.util.digest import digest_bytes
from stavewright.util.fsops import (
    PathEscapeError,
    atomic_write_bytes,
    atomic_write_text,
    copy_with_digest,
    ensure_dir,
    is_reserved_name,
    remove_path,
    safe_join,
    scoped_directory,
)


@pytest.mark.parametrize(
    "relative",
    [
        "V4T.exe",
        "Templates/Sellenis/GS1-1.txt",
        "ftdimfr/i386/ftdibus.sys",
        "FpgaImage/pcdc_top.rbf",
    ],
)
def test_safe_join_accepts_ordinary_payload_paths(tmp_path, relative):
    joined = safe_join(tmp_path, relative)
    assert joined.parent.is_relative_to(tmp_path) or joined == tmp_path / relative


def test_safe_join_normalises_backslashes(tmp_path):
    assert safe_join(tmp_path, r"Templates\Sellenis\GS1-1.txt") == (
        tmp_path / "Templates" / "Sellenis" / "GS1-1.txt"
    )


@pytest.mark.parametrize(
    "relative",
    [
        "../outside.dll",
        "Templates/../../outside.dll",
        r"..\outside.dll",
    ],
)
def test_safe_join_rejects_traversal(tmp_path, relative):
    with pytest.raises(PathEscapeError, match="climbs above its root"):
        safe_join(tmp_path, relative)


@pytest.mark.parametrize(
    "relative",
    [
        r"C:\Windows\System32\ftdibus.sys",
        "/etc/passwd",
        r"\\line-pc-04\share\payload.dll",
        "//line-pc-04/share/payload.dll",
    ],
)
def test_safe_join_rejects_absolute_and_unc(tmp_path, relative):
    with pytest.raises(PathEscapeError):
        safe_join(tmp_path, relative)


def test_safe_join_rejects_empty(tmp_path):
    with pytest.raises(PathEscapeError, match="empty relative path"):
        safe_join(tmp_path, "")


@pytest.mark.parametrize("name", ["CON", "nul", "Lpt1", "COM4.txt", "PRN.spf"])
def test_reserved_device_names_are_flagged(name):
    assert is_reserved_name(name)


@pytest.mark.parametrize("name", ["CONFIG", "COM10", "NULL", "V4T.exe", "lpt"])
def test_ordinary_names_are_not_flagged(name):
    assert not is_reserved_name(name)


def test_safe_join_rejects_a_reserved_segment(tmp_path):
    with pytest.raises(PathEscapeError, match="reserved device name"):
        safe_join(tmp_path, "Templates/NUL/T1.spf")


def test_ensure_dir_is_idempotent(tmp_path):
    target = tmp_path / "Sellenis" / "S-jet600" / "log"
    assert ensure_dir(target) == target
    assert ensure_dir(target) == target
    assert target.is_dir()


def test_atomic_write_creates_parents(tmp_path):
    target = tmp_path / "Sellenis" / "S-jet600" / "System" / "state.bin"
    atomic_write_bytes(target, b"\x01\x02")
    assert target.read_bytes() == b"\x01\x02"


def test_atomic_write_leaves_no_temp_behind(tmp_path):
    target = tmp_path / "state.bin"
    atomic_write_bytes(target, b"payload")
    assert [p.name for p in tmp_path.iterdir()] == ["state.bin"]


def test_atomic_write_replaces_existing_content(tmp_path):
    target = tmp_path / "Version"
    atomic_write_bytes(target, b"1.0.0")
    atomic_write_bytes(target, b"1.0.1")
    assert target.read_bytes() == b"1.0.1"


def test_atomic_write_text_normalises_line_endings(tmp_path):
    target = tmp_path / "reg.bat"
    atomic_write_text(target, "regsvr32.exe c1sizer.ocx\r\nregsvr32.exe vsflex8n.ocx\r\n")
    assert target.read_bytes() == b"regsvr32.exe c1sizer.ocx\nregsvr32.exe vsflex8n.ocx\n"


def test_remove_path_reports_whether_it_did_anything(tmp_path):
    target = tmp_path / "gone.dll"
    assert remove_path(target) is False
    target.write_bytes(b"x")
    assert remove_path(target) is True
    assert not target.exists()


def test_remove_path_takes_a_whole_tree(tmp_path):
    tree = tmp_path / "ftdimfr" / "i386"
    tree.mkdir(parents=True)
    (tree / "ftdibus.sys").write_bytes(b"MZ")
    assert remove_path(tmp_path / "ftdimfr") is True
    assert not (tmp_path / "ftdimfr").exists()


def test_copy_with_digest_returns_the_destination_digest(tmp_path):
    source = tmp_path / "qu_imager.dll"
    source.write_bytes(b"MZ\x90\x00imager")
    destination = tmp_path / "out" / "qu_imager.dll"
    result = copy_with_digest(source, destination)
    assert result == digest_bytes(b"MZ\x90\x00imager")
    assert destination.read_bytes() == source.read_bytes()


def test_copy_with_digest_accepts_a_matching_expectation(tmp_path):
    source = tmp_path / "hpsc.dll"
    source.write_bytes(b"MZ\x90\x00hpsc")
    expected = digest_bytes(b"MZ\x90\x00hpsc")
    copy_with_digest(source, tmp_path / "out" / "hpsc.dll", expected=expected)


def test_copy_with_digest_refuses_a_mismatch_and_cleans_up(tmp_path):
    source = tmp_path / "hpsc.dll"
    source.write_bytes(b"MZ\x90\x00hpsc")
    destination = tmp_path / "out" / "hpsc.dll"
    with pytest.raises(ValueError, match="digests to"):
        copy_with_digest(source, destination, expected=digest_bytes(b"different"))
    assert not destination.exists()
    assert list((tmp_path / "out").iterdir()) == []


def test_scoped_directory_cleans_up_on_success(tmp_path):
    target = tmp_path / "_staging"
    with scoped_directory(target) as scoped:
        (scoped / "manifest.json").write_text("{}")
        assert scoped.is_dir()
    assert not target.exists()


def test_scoped_directory_cleans_up_after_a_failure(tmp_path):
    target = tmp_path / "_staging"
    with pytest.raises(RuntimeError), scoped_directory(target) as scoped:
        (scoped / "manifest.json").write_text("{}")
        raise RuntimeError("staging blew up")
    assert not target.exists()
