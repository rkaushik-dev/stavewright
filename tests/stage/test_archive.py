from __future__ import annotations

import zipfile
from pathlib import PurePosixPath

import pytest

from stavewright.errors import StageError
from stavewright.model.manifest import Manifest
from stavewright.model.payload import PayloadItem, PayloadKind
from stavewright.stage.archive import (
    COMMENT,
    MAX_MEMBER_BYTES,
    archive_manifest_digest,
    is_signed,
    open_archive,
    pack,
    unpack,
    verify_unpacked,
)
from stavewright.stage.stager import MANIFEST_NAME, SIGNATURE_NAME, Stager, open_bundle


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "build"
    (root / "payload").mkdir(parents=True)
    (root / "payload" / "V4T.exe").write_bytes(b"MZ" + b"\x00" * 400)
    (root / "payload" / "qu_imager.dll").write_bytes(b"MZimager")
    (root / "payload" / "winsxs").mkdir()
    (root / "payload" / "winsxs" / "mfc80.dll").write_bytes(b"MZmfc")
    return root


@pytest.fixture
def manifest():
    return Manifest(
        items=[
            PayloadItem(
                item_id="file.v4t",
                kind=PayloadKind.FILE,
                name="V4T.exe",
                directory="APPDIR",
                source=PurePosixPath("payload/V4T.exe"),
            ),
            PayloadItem(
                item_id="file.imager",
                kind=PayloadKind.FILE,
                name="qu_imager.dll",
                directory="APPDIR",
                source=PurePosixPath("payload/qu_imager.dll"),
            ),
            PayloadItem(
                item_id="tree.winsxs",
                kind=PayloadKind.SXS_ASSEMBLY,
                name="winsxs",
                directory="WindowsFolder",
                source=PurePosixPath("payload/winsxs"),
            ),
        ]
    )


@pytest.fixture
def bundle(tmp_path, source, manifest):
    staged = Stager(tmp_path / "_staging").stage(manifest, source)
    return staged.bundle


def test_packing_writes_one_file(tmp_path, bundle):
    out = tmp_path / "bundle.swbundle"
    report = pack(bundle, out)
    assert out.is_file()
    assert report.members > 3
    assert report.bytes_written > 0


def test_the_archive_carries_its_comment(tmp_path, bundle):
    out = tmp_path / "bundle.swbundle"
    pack(bundle, out)
    with zipfile.ZipFile(out) as archive:
        assert archive.comment == COMMENT


def test_the_archive_is_not_compressed(tmp_path, bundle):
    out = tmp_path / "bundle.swbundle"
    pack(bundle, out)
    with zipfile.ZipFile(out) as archive:
        assert all(info.compress_type == zipfile.ZIP_STORED for info in archive.infolist())


def test_the_manifest_is_in_it(tmp_path, bundle):
    out = tmp_path / "bundle.swbundle"
    pack(bundle, out)
    with zipfile.ZipFile(out) as archive:
        assert MANIFEST_NAME in archive.namelist()


def test_packing_over_an_existing_archive_is_refused(tmp_path, bundle):
    out = tmp_path / "bundle.swbundle"
    pack(bundle, out)
    with pytest.raises(StageError, match="refusing to overwrite"):
        pack(bundle, out)


def test_packing_a_directory_with_no_manifest(tmp_path, bundle):
    (bundle.root / MANIFEST_NAME).unlink()
    with pytest.raises(StageError, match="no manifest"):
        pack(bundle, tmp_path / "bundle.swbundle")


def test_a_round_trip_gives_the_same_files(tmp_path, bundle):
    out = tmp_path / "bundle.swbundle"
    pack(bundle, out)
    back = tmp_path / "unpacked"
    report = unpack(out, back)
    assert report.members == len(list(bundle.root.rglob("*"))) - len(
        [p for p in bundle.root.rglob("*") if p.is_dir()]
    )
    assert report.verified == 2


def test_a_round_trip_gives_a_bundle_that_opens(tmp_path, bundle):
    out = tmp_path / "bundle.swbundle"
    pack(bundle, out)
    back = tmp_path / "unpacked"
    unpack(out, back)
    reopened = open_bundle(back)
    assert reopened.read("payload/V4T.exe") == bundle.read("payload/V4T.exe")


def test_open_archive_does_both_in_one_step(tmp_path, bundle):
    out = tmp_path / "bundle.swbundle"
    pack(bundle, out)
    reopened = open_archive(out, tmp_path / "unpacked")
    assert [item.item_id for item in reopened.manifest] == [
        item.item_id for item in bundle.manifest
    ]


def test_the_tree_survives_the_round_trip(tmp_path, bundle):
    # A tree is staged whole rather than into the object store, so it is
    # addressed by the directory it came from and read off disk.
    out = tmp_path / "bundle.swbundle"
    pack(bundle, out)
    reopened = open_archive(out, tmp_path / "unpacked")
    from pathlib import Path

    inside = Path(reopened.local_path("payload/winsxs")) / "mfc80.dll"
    assert inside.read_bytes() == b"MZmfc"


def test_unpacking_into_a_directory_with_something_in_it(tmp_path, bundle):
    out = tmp_path / "bundle.swbundle"
    pack(bundle, out)
    back = tmp_path / "unpacked"
    back.mkdir()
    (back / "something").write_text("x", encoding="utf-8")
    with pytest.raises(StageError, match="not empty"):
        unpack(out, back)


def test_unpacking_into_an_empty_directory_that_exists(tmp_path, bundle):
    out = tmp_path / "bundle.swbundle"
    pack(bundle, out)
    back = tmp_path / "unpacked"
    back.mkdir()
    assert unpack(out, back).members > 0


def test_unpacking_something_that_is_not_a_zip(tmp_path):
    path = tmp_path / "not.swbundle"
    path.write_bytes(b"MZ not a zip at all")
    with pytest.raises(StageError, match="not a zip archive"):
        unpack(path, tmp_path / "out")


def test_unpacking_a_zip_that_is_not_a_bundle(tmp_path):
    path = tmp_path / "other.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("readme.txt", "nothing to see")
    with pytest.raises(StageError, match="not a staged bundle") as caught:
        unpack(path, tmp_path / "out")
    assert MANIFEST_NAME in caught.value.context["missing"]


def test_an_archive_that_names_a_member_outside_the_directory(tmp_path, bundle):
    out = tmp_path / "bundle.swbundle"
    pack(bundle, out)
    with zipfile.ZipFile(out, "a") as archive:
        archive.writestr("../escaped.txt", "somewhere else")
    with pytest.raises(StageError, match="outside the directory") as caught:
        unpack(out, tmp_path / "unpacked")
    assert caught.value.context["member"] == "../escaped.txt"


def test_an_archive_that_names_an_absolute_member(tmp_path, bundle):
    out = tmp_path / "bundle.swbundle"
    pack(bundle, out)
    with zipfile.ZipFile(out, "a") as archive:
        archive.writestr("/etc/passwd", "root")
    with pytest.raises(StageError, match="outside the directory"):
        unpack(out, tmp_path / "unpacked")


def test_a_tampered_object_is_caught(tmp_path, bundle):
    out = tmp_path / "bundle.swbundle"
    pack(bundle, out)
    back = tmp_path / "unpacked"
    unpack(out, back, verify=False)
    victim = next((back / "objects").rglob("*"))
    while victim.is_dir():
        victim = next(victim.iterdir())
    victim.write_bytes(b"something else entirely")
    with pytest.raises(StageError, match="does not match the digest") as caught:
        verify_unpacked(back)
    assert caught.value.context["expected"] != caught.value.context["found"]


def test_a_missing_object_is_caught(tmp_path, bundle):
    out = tmp_path / "bundle.swbundle"
    pack(bundle, out)
    back = tmp_path / "unpacked"
    unpack(out, back, verify=False)
    victim = next(path for path in (back / "objects").rglob("*") if path.is_file())
    victim.unlink()
    with pytest.raises(StageError, match="missing an object"):
        verify_unpacked(back)


def test_verification_can_be_turned_off(tmp_path, bundle):
    out = tmp_path / "bundle.swbundle"
    pack(bundle, out)
    report = unpack(out, tmp_path / "unpacked", verify=False)
    assert report.verified == 0


def test_the_manifest_digest_can_be_read_without_unpacking(tmp_path, bundle):
    out = tmp_path / "bundle.swbundle"
    pack(bundle, out)
    from stavewright.util.digest import digest_bytes

    expected = digest_bytes((bundle.root / MANIFEST_NAME).read_bytes())
    assert archive_manifest_digest(out) == str(expected)


def test_two_archives_of_one_bundle_have_the_same_manifest_digest(tmp_path, bundle):
    first = tmp_path / "one.swbundle"
    second = tmp_path / "two.swbundle"
    pack(bundle, first)
    pack(bundle, second)
    assert archive_manifest_digest(first) == archive_manifest_digest(second)


def test_the_manifest_digest_of_a_zip_with_no_manifest(tmp_path):
    path = tmp_path / "other.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("readme.txt", "nothing")
    with pytest.raises(StageError, match="holds no manifest"):
        archive_manifest_digest(path)


def test_the_manifest_digest_of_something_unreadable(tmp_path):
    with pytest.raises(StageError, match="cannot read archive"):
        archive_manifest_digest(tmp_path / "gone.swbundle")


def test_an_unsigned_bundle_is_reported_as_unsigned(tmp_path, bundle):
    out = tmp_path / "bundle.swbundle"
    pack(bundle, out)
    assert not is_signed(out)


def test_a_signed_bundle_is_reported_as_signed(tmp_path, bundle):
    (bundle.root / SIGNATURE_NAME).write_text("{}", encoding="utf-8")
    out = tmp_path / "bundle.swbundle"
    pack(bundle, out)
    assert is_signed(out)


def test_something_that_is_not_an_archive_is_not_signed(tmp_path):
    assert not is_signed(tmp_path / "gone.swbundle")


def test_an_implausibly_large_member_is_refused(tmp_path, bundle, monkeypatch):
    out = tmp_path / "bundle.swbundle"
    pack(bundle, out)
    monkeypatch.setattr("stavewright.stage.archive.MAX_MEMBER_BYTES", 4)
    with pytest.raises(StageError, match="implausibly large"):
        unpack(out, tmp_path / "unpacked")


def test_the_limit_is_the_documented_one():
    assert MAX_MEMBER_BYTES == 2 * 1024 * 1024 * 1024


def test_the_report_describes_itself(tmp_path, bundle):
    out = tmp_path / "bundle.swbundle"
    report = pack(bundle, out)
    assert report.describe().startswith("bundle.swbundle:")
