from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import PurePosixPath

import pytest

from stavewright.__about__ import MANIFEST_SCHEMA_VERSION, PRODUCT_NAME, UPGRADE_CODE
from stavewright.errors import ManifestError, ManifestSchemaError
from stavewright.model.manifest import Manifest, loads, read_manifest, utc_now
from stavewright.model.payload import PayloadItem, PayloadKind
from stavewright.model.profile import Architecture, TerminalModel
from stavewright.util.digest import digest_bytes
from stavewright.util.msiver import MsiVersion

STAMP = datetime(2025, 5, 8, 12, 0, tzinfo=UTC)


def _items() -> list[PayloadItem]:
    return [
        PayloadItem(
            item_id="file.v4t",
            kind=PayloadKind.FILE,
            name="V4T.exe",
            directory="APPDIR",
            component="V4T.exe",
            source=PurePosixPath("payload/V4T.exe"),
            digest=digest_bytes(b"MZ\x90\x00"),
            provides=("exe",),
        ),
        PayloadItem(
            item_id="ocx.c1sizer.wow",
            kind=PayloadKind.COM_SERVER,
            name="C1Sizer.ocx",
            directory="SystemFolder",
            source=PurePosixPath("payload/ocx/SysWOW64/C1Sizer.ocx"),
            architecture=Architecture.X64,
            requires=("sxs.mfc",),
        ),
        PayloadItem(
            item_id="fpga.pcdc",
            kind=PayloadKind.FPGA_IMAGE,
            name="pcdc_top.rbf",
            directory="FpgaImage_Dir",
            source=PurePosixPath("payload/FpgaImage/pcdc_top.rbf"),
            machines=frozenset({TerminalModel.SJET_600, TerminalModel.SJET_600_TIJ}),
        ),
        PayloadItem(
            item_id="lang.polish",
            kind=PayloadKind.LANGUAGE_PACK,
            name="LangPolish.dll",
            directory="APPDIR",
            source=PurePosixPath("payload/LangPolish.dll"),
            language_ids=frozenset({1045}),
        ),
    ]


def _manifest() -> Manifest:
    return Manifest(generated_at=STAMP, items=_items(), notes="bench build")


def test_defaults_describe_the_shipped_product():
    manifest = Manifest()
    assert manifest.product == PRODUCT_NAME
    assert manifest.upgrade_code == UPGRADE_CODE
    assert manifest.schema_version == MANIFEST_SCHEMA_VERSION
    assert manifest.version == MsiVersion(1, 0, 0)


def test_sequence_protocol():
    manifest = _manifest()
    assert len(manifest) == 4
    assert "fpga.pcdc" in manifest
    assert "nope" not in manifest
    assert next(item.item_id for item in manifest) == "file.v4t"


def test_get_and_require():
    manifest = _manifest()
    assert manifest.get("fpga.pcdc").name == "pcdc_top.rbf"
    assert manifest.get("nope") is None
    assert manifest.require("fpga.pcdc").item_id == "fpga.pcdc"


def test_require_reports_a_missing_item():
    with pytest.raises(ManifestError, match="no such item"):
        _manifest().require("nope")


def test_of_kind():
    assert [i.item_id for i in _manifest().of_kind(PayloadKind.FILE)] == ["file.v4t"]
    assert _manifest().of_kind(PayloadKind.FONT) == []


def test_a_valid_manifest_validates():
    _manifest().validate()


def test_a_manifest_with_no_product_is_refused():
    manifest = _manifest()
    manifest.product = ""
    with pytest.raises(ManifestError, match="no product name"):
        manifest.validate()


def test_duplicate_item_ids_are_refused():
    manifest = _manifest()
    manifest.items.append(manifest.items[0])
    with pytest.raises(ManifestError, match="duplicate item ids") as excinfo:
        manifest.validate()
    assert excinfo.value.context["items"] == ["file.v4t"]


def test_a_source_that_climbs_out_is_refused():
    manifest = Manifest(
        items=[
            PayloadItem(
                item_id="escape",
                kind=PayloadKind.FILE,
                name="x",
                directory="APPDIR",
                source=PurePosixPath("../../etc/passwd"),
            )
        ]
    )
    with pytest.raises(ManifestError, match="climbs out of the bundle"):
        manifest.validate()


def test_an_absolute_source_is_refused():
    manifest = Manifest(
        items=[
            PayloadItem(
                item_id="absolute",
                kind=PayloadKind.FILE,
                name="x",
                directory="APPDIR",
                source=PurePosixPath("/etc/passwd"),
            )
        ]
    )
    with pytest.raises(ManifestError, match="must be relative"):
        manifest.validate()


def test_round_trip_through_a_dict():
    original = _manifest()
    rebuilt = Manifest.from_dict(original.to_dict())
    assert rebuilt.to_dict() == original.to_dict()
    assert rebuilt.items == original.items


def test_round_trip_through_json_text():
    original = _manifest()
    assert loads(original.dumps()).to_dict() == original.to_dict()


def test_round_trip_preserves_every_item_field():
    original = _manifest()
    rebuilt = loads(original.dumps())
    ocx = rebuilt.require("ocx.c1sizer.wow")
    assert ocx.architecture is Architecture.X64
    assert ocx.requires == ("sxs.mfc",)
    fpga = rebuilt.require("fpga.pcdc")
    assert fpga.machines == frozenset({TerminalModel.SJET_600, TerminalModel.SJET_600_TIJ})
    assert rebuilt.require("lang.polish").language_ids == frozenset({1045})
    assert rebuilt.require("file.v4t").digest == digest_bytes(b"MZ\x90\x00")


def test_round_trip_preserves_the_timestamp():
    assert loads(_manifest().dumps()).generated_at == STAMP


def test_empty_fields_are_left_out_of_the_serialised_form():
    payload = json.loads(_manifest().dumps())
    entry = next(e for e in payload["items"] if e["item_id"] == "file.v4t")
    assert "architecture" not in entry
    assert "machines" not in entry
    assert "language_ids" not in entry


def test_the_canonical_form_is_compact_and_sorted():
    canonical = _manifest().canonical()
    assert ", " not in canonical
    assert canonical.index('"items"') < canonical.index('"product"')


def test_the_digest_is_stable():
    assert _manifest().digest() == _manifest().digest()


def test_the_digest_moves_when_an_item_changes():
    other = _manifest()
    other.items[0] = PayloadItem(
        item_id="file.v4t",
        kind=PayloadKind.FILE,
        name="V4T.exe",
        directory="APPDIR",
        source=PurePosixPath("payload/V4T.exe"),
        digest=digest_bytes(b"different"),
    )
    assert _manifest().digest() != other.digest()


def test_the_digest_moves_when_the_timestamp_changes():
    other = _manifest()
    other.generated_at = datetime(2025, 5, 9, 12, 0, tzinfo=UTC)
    assert _manifest().digest() != other.digest()


def test_a_newer_schema_is_refused():
    payload = _manifest().to_dict()
    payload["schema_version"] = MANIFEST_SCHEMA_VERSION + 1
    with pytest.raises(ManifestSchemaError, match="newer toolkit") as excinfo:
        Manifest.from_dict(payload)
    assert excinfo.value.context["found"] == MANIFEST_SCHEMA_VERSION + 1


def test_an_older_schema_is_accepted():
    payload = _manifest().to_dict()
    payload["schema_version"] = 1
    assert Manifest.from_dict(payload).schema_version == 1


def test_a_non_numeric_schema_is_refused():
    payload = _manifest().to_dict()
    payload["schema_version"] = "one"
    with pytest.raises(ManifestError, match="not a number"):
        Manifest.from_dict(payload)


def test_a_bad_version_is_refused():
    payload = _manifest().to_dict()
    payload["version"] = "v1"
    with pytest.raises(ManifestError, match="not an MSI version"):
        Manifest.from_dict(payload)


def test_an_item_with_no_kind_is_refused():
    with pytest.raises(ManifestError, match="has no kind"):
        Manifest.from_dict({"items": [{"item_id": "x", "name": "x"}]})


def test_an_item_with_an_unknown_kind_is_refused():
    with pytest.raises(ManifestError, match="unknown kind") as excinfo:
        Manifest.from_dict({"items": [{"item_id": "x", "kind": "teleport", "name": "x"}]})
    assert excinfo.value.context["kind"] == "teleport"


def test_an_item_with_an_unreadable_field_is_refused():
    with pytest.raises(ManifestError, match="unreadable field"):
        Manifest.from_dict(
            {
                "items": [
                    {
                        "item_id": "x",
                        "kind": "file",
                        "name": "x",
                        "directory": "APPDIR",
                        "source": "payload/x",
                        "architecture": "ia64",
                    }
                ]
            }
        )


def test_text_that_is_not_json_is_refused():
    with pytest.raises(ManifestError, match="not valid JSON"):
        loads("{not json")


def test_json_that_is_not_an_object_is_refused():
    with pytest.raises(ManifestError, match="not a JSON object"):
        loads("[1, 2, 3]")


def test_write_and_read_a_manifest(tmp_path):
    path = tmp_path / "bundle" / "manifest.json"
    _manifest().write(path)
    rebuilt = read_manifest(path)
    assert rebuilt.to_dict() == _manifest().to_dict()
    assert rebuilt.source == path


def test_a_written_manifest_is_readable_by_hand(tmp_path):
    path = tmp_path / "manifest.json"
    _manifest().write(path)
    text = path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert "\n  " in text


def test_read_reports_a_missing_file(tmp_path):
    with pytest.raises(ManifestError, match="cannot read manifest"):
        read_manifest(tmp_path / "nope.json")


def test_describe_an_in_memory_manifest():
    assert _manifest().describe() == "(in memory): S-Jet 600 1.0.0, 4 items"


def test_describe_a_manifest_from_disk(tmp_path):
    path = tmp_path / "manifest.json"
    _manifest().write(path)
    assert read_manifest(path).describe().startswith("manifest.json:")


def test_an_empty_manifest_round_trips():
    assert loads(Manifest().dumps()).items == []


def test_utc_now_is_timezone_aware():
    assert utc_now().tzinfo is not None
