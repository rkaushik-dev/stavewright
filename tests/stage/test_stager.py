from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from v4t_setup.errors import StageError
from v4t_setup.model.manifest import Manifest
from v4t_setup.model.payload import PayloadItem, PayloadKind
from v4t_setup.signing import generate_key, sign_manifest
from v4t_setup.stage.stager import (
    MANIFEST_NAME,
    SIGNATURE_NAME,
    IndexEntry,
    Stager,
    open_bundle,
    read_index,
)
from v4t_setup.util.digest import digest_bytes


@pytest.fixture
def source_root(tmp_path):
    root = tmp_path / "build"
    (root / "payload").mkdir(parents=True)
    (root / "payload" / "V4T.exe").write_bytes(b"MZ\x90\x00")
    (root / "payload" / "qu_imager.dll").write_bytes(b"MZimager")
    (root / "payload" / "ftdimfr" / "i386").mkdir(parents=True)
    (root / "payload" / "ftdimfr" / "ftdibus.inf").write_bytes(b"[Version]")
    (root / "payload" / "ftdimfr" / "i386" / "ftdibus.sys").write_bytes(b"MZsys")
    return root


@pytest.fixture
def stage_root(tmp_path):
    return tmp_path / "_staging"


def manifest_with(*items: PayloadItem) -> Manifest:
    return Manifest(items=list(items))


def file_item(item_id: str, name: str, source: str, **kwargs) -> PayloadItem:
    return PayloadItem(
        item_id=item_id,
        kind=PayloadKind.FILE,
        name=name,
        directory="APPDIR",
        source=PurePosixPath(source),
        **kwargs,
    )


def driver_item() -> PayloadItem:
    return PayloadItem(
        item_id="driver.ftdi",
        kind=PayloadKind.DRIVER_PACKAGE,
        name="ftdimfr",
        source=PurePosixPath("payload/ftdimfr"),
    )


def test_staging_a_single_file(source_root, stage_root):
    manifest = manifest_with(file_item("file.v4t", "V4T.exe", "payload/V4T.exe"))
    report = Stager(stage_root).stage(manifest, source_root)
    assert report.staged == ["file.v4t"]
    assert report.bundle.read("payload/V4T.exe") == b"MZ\x90\x00"


def test_the_bundle_is_named_after_the_manifest_digest(source_root, stage_root):
    manifest = manifest_with(file_item("file.v4t", "V4T.exe", "payload/V4T.exe"))
    report = Stager(stage_root).stage(manifest, source_root)
    assert report.bundle.root.name == manifest.digest().short


def test_restaging_the_same_bundle_lands_in_the_same_place(source_root, stage_root):
    manifest = manifest_with(file_item("file.v4t", "V4T.exe", "payload/V4T.exe"))
    first = Stager(stage_root).stage(manifest, source_root).bundle.root
    second = Stager(stage_root).stage(manifest, source_root).bundle.root
    assert first == second


def test_two_different_bundles_do_not_collide(source_root, stage_root):
    one = manifest_with(file_item("file.v4t", "V4T.exe", "payload/V4T.exe"))
    two = manifest_with(file_item("file.imager", "qu_imager.dll", "payload/qu_imager.dll"))
    stager = Stager(stage_root)
    assert stager.stage(one, source_root).bundle.root != stager.stage(two, source_root).bundle.root


def test_staging_several_files(source_root, stage_root):
    manifest = manifest_with(
        file_item("file.v4t", "V4T.exe", "payload/V4T.exe"),
        file_item("file.imager", "qu_imager.dll", "payload/qu_imager.dll"),
    )
    bundle = Stager(stage_root).stage(manifest, source_root).bundle
    assert bundle.read("payload/V4T.exe") == b"MZ\x90\x00"
    assert bundle.read("payload/qu_imager.dll") == b"MZimager"


def test_a_declared_digest_is_checked(source_root, stage_root):
    manifest = manifest_with(
        file_item("file.v4t", "V4T.exe", "payload/V4T.exe", digest=digest_bytes(b"MZ\x90\x00"))
    )
    assert Stager(stage_root).stage(manifest, source_root).item_count == 1


def test_a_payload_that_does_not_match_its_digest_is_refused(source_root, stage_root):
    manifest = manifest_with(
        file_item("file.v4t", "V4T.exe", "payload/V4T.exe", digest=digest_bytes(b"a fake build"))
    )
    with pytest.raises(StageError, match="does not match the digest") as excinfo:
        Stager(stage_root).stage(manifest, source_root)
    assert excinfo.value.context["item"] == "file.v4t"


def test_a_bad_payload_is_not_left_in_the_store(source_root, stage_root):
    manifest = manifest_with(
        file_item("file.v4t", "V4T.exe", "payload/V4T.exe", digest=digest_bytes(b"a fake build"))
    )
    with pytest.raises(StageError):
        Stager(stage_root).stage(manifest, source_root)
    from v4t_setup.stage.store import ObjectStore

    store = ObjectStore(Stager(stage_root).bundle_root(manifest))
    assert not store.has(digest_bytes(b"MZ\x90\x00"))


def test_a_missing_source_is_reported(source_root, stage_root):
    manifest = manifest_with(file_item("file.gone", "gone.dll", "payload/gone.dll"))
    with pytest.raises(StageError, match="payload source is missing") as excinfo:
        Stager(stage_root).stage(manifest, source_root)
    assert excinfo.value.context["item"] == "file.gone"


def test_an_item_with_no_source_is_skipped(source_root, stage_root):
    manifest = manifest_with(
        PayloadItem(item_id="reg", kind=PayloadKind.REGISTRY, name="ProductInformation")
    )
    report = Stager(stage_root).stage(manifest, source_root)
    assert report.skipped == {"reg": "item has no payload to stage"}
    assert report.item_count == 0


def test_staging_a_directory_copies_the_whole_tree(source_root, stage_root):
    report = Stager(stage_root).stage(manifest_with(driver_item()), source_root)
    local = report.bundle.local_path("payload/ftdimfr")
    from pathlib import Path

    assert (Path(local) / "ftdibus.inf").read_bytes() == b"[Version]"
    assert (Path(local) / "i386" / "ftdibus.sys").read_bytes() == b"MZsys"


def test_reading_a_directory_item_as_bytes_is_refused(source_root, stage_root):
    report = Stager(stage_root).stage(manifest_with(driver_item()), source_root)
    with pytest.raises(StageError, match="is a directory"):
        report.bundle.read("payload/ftdimfr")


def test_a_directory_digest_is_checked(source_root, stage_root):
    from v4t_setup.util.digest import digest_tree

    expected = digest_tree(source_root / "payload" / "ftdimfr")
    item = PayloadItem(
        item_id="driver.ftdi",
        kind=PayloadKind.DRIVER_PACKAGE,
        name="ftdimfr",
        source=PurePosixPath("payload/ftdimfr"),
        digest=expected,
    )
    assert Stager(stage_root).stage(manifest_with(item), source_root).item_count == 1


def test_a_directory_that_does_not_match_its_digest_is_refused(source_root, stage_root):
    item = PayloadItem(
        item_id="driver.ftdi",
        kind=PayloadKind.DRIVER_PACKAGE,
        name="ftdimfr",
        source=PurePosixPath("payload/ftdimfr"),
        digest=digest_bytes(b"not the tree"),
    )
    with pytest.raises(StageError, match="tree does not match the digest"):
        Stager(stage_root).stage(manifest_with(item), source_root)


def test_restaging_a_directory_replaces_it(source_root, stage_root):
    from pathlib import Path

    stager = Stager(stage_root)
    manifest = manifest_with(driver_item())
    stager.stage(manifest, source_root)
    (source_root / "payload" / "ftdimfr" / "extra.inf").write_bytes(b"new")
    report = stager.stage(manifest, source_root)
    assert (Path(report.bundle.local_path("payload/ftdimfr")) / "extra.inf").exists()


def test_the_manifest_is_written_beside_the_payload(source_root, stage_root):
    manifest = manifest_with(file_item("file.v4t", "V4T.exe", "payload/V4T.exe"))
    report = Stager(stage_root).stage(manifest, source_root)
    assert (report.bundle.root / MANIFEST_NAME).is_file()


def test_a_signature_is_written_when_given(source_root, stage_root):
    manifest = manifest_with(file_item("file.v4t", "V4T.exe", "payload/V4T.exe"))
    signature = sign_manifest(manifest, generate_key())
    report = Stager(stage_root).stage(manifest, source_root, signature=signature)
    assert (report.bundle.root / SIGNATURE_NAME).is_file()


def test_no_signature_file_when_none_is_given(source_root, stage_root):
    manifest = manifest_with(file_item("file.v4t", "V4T.exe", "payload/V4T.exe"))
    report = Stager(stage_root).stage(manifest, source_root)
    assert not (report.bundle.root / SIGNATURE_NAME).exists()


def test_the_index_records_every_item(source_root, stage_root):
    manifest = manifest_with(
        file_item("file.v4t", "V4T.exe", "payload/V4T.exe"),
        driver_item(),
    )
    report = Stager(stage_root).stage(manifest, source_root)
    index = read_index(report.bundle.root)
    assert set(index) == {"payload/V4T.exe", "payload/ftdimfr"}
    assert index["payload/V4T.exe"].kind == "file"
    assert index["payload/ftdimfr"].kind == "tree"


def test_the_index_is_sorted_so_two_stagings_match(source_root, stage_root, tmp_path):
    manifest = manifest_with(
        file_item("file.imager", "qu_imager.dll", "payload/qu_imager.dll"),
        file_item("file.v4t", "V4T.exe", "payload/V4T.exe"),
    )
    one = Stager(stage_root).stage(manifest, source_root).bundle.root
    two = Stager(tmp_path / "other").stage(manifest, source_root).bundle.root
    assert (one / "index.json").read_text() == (two / "index.json").read_text()


def test_the_index_records_sizes(source_root, stage_root):
    manifest = manifest_with(file_item("file.v4t", "V4T.exe", "payload/V4T.exe"))
    report = Stager(stage_root).stage(manifest, source_root)
    assert read_index(report.bundle.root)["payload/V4T.exe"].size == 4


def test_reading_a_missing_index_is_reported(tmp_path):
    with pytest.raises(StageError, match="cannot read the staging index"):
        read_index(tmp_path)


def test_an_index_that_is_not_json_is_reported(tmp_path):
    (tmp_path / "index.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(StageError, match="not valid JSON"):
        read_index(tmp_path)


def test_an_index_entry_missing_a_field_is_reported():
    with pytest.raises(StageError, match="entry is unreadable"):
        IndexEntry.from_dict({"item_id": "x"})


def test_reopening_a_staged_bundle(source_root, stage_root):
    manifest = manifest_with(
        file_item("file.v4t", "V4T.exe", "payload/V4T.exe"),
        driver_item(),
    )
    root = Stager(stage_root).stage(manifest, source_root).bundle.root
    reopened = open_bundle(root)
    assert reopened.read("payload/V4T.exe") == b"MZ\x90\x00"
    assert reopened.manifest.to_dict() == manifest.to_dict()


def test_reopening_something_that_is_not_a_bundle(tmp_path):
    with pytest.raises(StageError, match="has no manifest"):
        open_bundle(tmp_path)


def test_asking_for_a_path_that_was_never_staged(source_root, stage_root):
    manifest = manifest_with(file_item("file.v4t", "V4T.exe", "payload/V4T.exe"))
    bundle = Stager(stage_root).stage(manifest, source_root).bundle
    with pytest.raises(StageError, match="nothing staged under that source path"):
        bundle.read("payload/nowhere.dll")


def test_exists_answers_for_staged_and_unstaged_paths(source_root, stage_root):
    manifest = manifest_with(file_item("file.v4t", "V4T.exe", "payload/V4T.exe"))
    bundle = Stager(stage_root).stage(manifest, source_root).bundle
    assert bundle.exists("payload/V4T.exe")
    assert not bundle.exists("payload/nowhere.dll")


def test_backslashes_resolve_the_same_as_forward_slashes(source_root, stage_root):
    manifest = manifest_with(file_item("file.v4t", "V4T.exe", "payload/V4T.exe"))
    bundle = Stager(stage_root).stage(manifest, source_root).bundle
    assert bundle.read(r"payload\V4T.exe") == b"MZ\x90\x00"


def test_the_bundle_describes_itself(source_root, stage_root):
    manifest = manifest_with(file_item("file.v4t", "V4T.exe", "payload/V4T.exe"))
    bundle = Stager(stage_root).stage(manifest, source_root).bundle
    assert bundle.describe().endswith("1 items, 0.0 MB")


def test_report_lines_read_as_a_summary(source_root, stage_root):
    manifest = manifest_with(
        file_item("file.v4t", "V4T.exe", "payload/V4T.exe"),
        PayloadItem(item_id="reg", kind=PayloadKind.REGISTRY, name="ProductInformation"),
    )
    lines = Stager(stage_root).stage(manifest, source_root).lines()
    assert "1 staged, 0 reused" in lines
    assert any(line.startswith("skipped reg") for line in lines)


def test_an_invalid_manifest_is_refused(source_root, stage_root):
    manifest = manifest_with(file_item("file.v4t", "V4T.exe", "payload/V4T.exe"))
    manifest.product = ""
    with pytest.raises(Exception, match="no product name"):
        Stager(stage_root).stage(manifest, source_root)


def test_a_staged_bundle_works_as_a_payload_source(source_root, stage_root, profile, tmp_path):
    from v4t_setup.apply.executor import Executor
    from v4t_setup.plan.steps import Plan, Step, StepKind
    from v4t_setup.target.memory import MemoryTarget

    manifest = manifest_with(file_item("file.v4t", "V4T.exe", "payload/V4T.exe"))
    bundle = Stager(stage_root).stage(manifest, source_root).bundle
    plan = Plan(
        profile=profile,
        steps=[
            Step(
                step_id="file.v4t",
                kind=StepKind.COPY_FILE,
                title="copy V4T.exe",
                target=r"C:\Sellenis\S-jet600\V4T.exe",
                source="payload/V4T.exe",
            )
        ],
    )
    target = MemoryTarget()
    result = Executor(target, bundle, tmp_path / "run.jsonl").run(plan, "run0001")
    assert result.succeeded
    assert target.read_file(r"C:\Sellenis\S-jet600\V4T.exe") == b"MZ\x90\x00"
