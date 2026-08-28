from __future__ import annotations

import struct
from datetime import UTC, datetime
from pathlib import Path

import pytest

from stavewright.msi.cfb import read_compound_file
from stavewright.msi.summary import (
    PID_CREATED,
    PID_TEMPLATE,
    SOURCE_ADMIN_IMAGE,
    SOURCE_PATCH,
    SOURCE_UNCOMPRESSED,
    STREAM_NAME,
    SummaryInformation,
    parse_summary,
    read_summary,
)
from stavewright.msi.tables import MsiError

MSI = Path(__file__).resolve().parents[2] / "msi-quattro" / "msi-Jet600.msi"

PACKAGE_CODE = "{345299C7-6DE9-4E9E-AFCD-D230D2F0F642}"


@pytest.fixture(scope="module")
def summary() -> SummaryInformation:
    return read_summary(read_compound_file(MSI))


def test_the_shipped_installer_has_a_summary_stream(summary):
    assert len(summary) > 10


def test_the_codepage(summary):
    assert summary.codepage == 1252


def test_the_package_code(summary):
    assert summary.package_code == PACKAGE_CODE


def test_the_package_code_is_not_the_upgrade_code(summary):
    # Different identifiers for different questions. The package code says
    # these are the same bytes; the upgrade code says the same product.
    from stavewright.msi.tables import read_msi

    assert summary.package_code != read_msi(MSI).property("UpgradeCode")


def test_this_build_put_the_product_name_in_title(summary):
    assert summary.title == "msi-Jet600"
    assert summary.subject == ""


def test_the_product_name_finds_it_anyway(summary):
    assert summary.product_name == "msi-Jet600"


def test_the_manufacturer_is_the_tool_default(summary):
    assert summary.manufacturer == "Default Company Name"


def test_the_template_and_what_comes_out_of_it(summary):
    assert summary.template == "Intel;1033"
    assert summary.platform == "Intel"
    assert summary.languages == (1033,)
    assert not summary.is_x64


def test_the_schema_version(summary):
    assert summary.schema_version == 200


def test_the_payload_is_compressed(summary):
    assert summary.source_flags == 0
    assert summary.is_compressed
    assert not summary.is_admin_image
    assert not summary.is_patch


def test_the_timestamps_are_real(summary):
    # Not 1601-01-01, which is what a FILETIME conversion that has lost its
    # precision produces for every value in the file.
    assert summary.created_at == datetime(1999, 6, 21, 7, 0, tzinfo=UTC)
    assert summary.saved_at is not None
    assert summary.saved_at.year == 2014


def test_the_timestamps_are_timezone_aware(summary):
    assert summary.created_at.tzinfo is not None
    assert summary.saved_at.tzinfo is not None


def test_the_creating_application(summary):
    assert summary.creating_application == "Windows Installer"


def test_the_security_field(summary):
    assert summary.security == 1


def test_it_describes_itself(summary):
    text = summary.describe()
    assert text.startswith("msi-Jet600, {345299C7")
    assert "built by Windows Installer" in text


def test_membership_by_property_id(summary):
    assert PID_TEMPLATE in summary
    assert 999 not in summary


def test_an_x64_template():
    entry = SummaryInformation(properties={PID_TEMPLATE: "x64;1033,1045"})
    assert entry.platform == "x64"
    assert entry.is_x64
    assert entry.languages == (1033, 1045)


def test_a_neutral_template_has_no_languages():
    assert SummaryInformation(properties={PID_TEMPLATE: "Intel;"}).languages == ()


def test_a_template_with_no_semicolon():
    entry = SummaryInformation(properties={PID_TEMPLATE: "Intel"})
    assert entry.platform == "Intel"
    assert entry.languages == ()


def test_no_template_at_all():
    entry = SummaryInformation(properties={})
    assert entry.platform is None
    assert entry.languages == ()
    assert not entry.is_x64


def test_a_missing_product_name_reads_as_none():
    assert SummaryInformation(properties={}).product_name is None


def test_an_empty_subject_and_title_read_as_none():
    entry = SummaryInformation(properties={2: "", 3: ""})
    assert entry.product_name is None


def test_the_source_flags_decode():
    loose = SummaryInformation(properties={15: SOURCE_UNCOMPRESSED})
    assert not loose.is_compressed
    admin = SummaryInformation(properties={15: SOURCE_ADMIN_IMAGE})
    assert admin.is_admin_image
    patch = SummaryInformation(properties={15: SOURCE_PATCH})
    assert patch.is_patch


def test_no_source_flags_means_compressed():
    assert SummaryInformation(properties={}).is_compressed


def test_a_stream_that_is_too_short():
    with pytest.raises(MsiError, match="too short"):
        parse_summary(b"\xfe\xff")


def test_a_stream_with_no_byte_order_mark():
    with pytest.raises(MsiError, match="byte order mark"):
        parse_summary(b"MZ" + bytes(60))


def test_a_stream_that_declares_no_sections():
    raw = bytearray(read_compound_file(MSI).read(STREAM_NAME))
    struct.pack_into("<I", raw, 24, 0)
    with pytest.raises(MsiError, match="declares no sections"):
        parse_summary(bytes(raw))


def test_a_property_type_this_reader_does_not_know():
    raw = bytearray(read_compound_file(MSI).read(STREAM_NAME))
    section = struct.unpack_from("<I", raw, 28 + 16)[0]
    offset = section + struct.unpack_from("<I", raw, section + 8 + 4)[0]
    struct.pack_into("<I", raw, offset, 4242)
    with pytest.raises(MsiError, match="property type this reader does not know") as caught:
        parse_summary(bytes(raw))
    assert caught.value.context["type"] == 4242


def test_a_zero_filetime_reads_as_absent():
    raw = bytearray(read_compound_file(MSI).read(STREAM_NAME))
    section = struct.unpack_from("<I", raw, 28 + 16)[0]
    count = struct.unpack_from("<I", raw, section + 4)[0]
    for index in range(count):
        base = section + 8 + index * 8
        if struct.unpack_from("<I", raw, base)[0] == PID_CREATED:
            offset = section + struct.unpack_from("<I", raw, base + 4)[0]
            struct.pack_into("<Q", raw, offset + 4, 0)
            break
    assert parse_summary(bytes(raw)).created_at is None


def test_a_compound_file_with_no_summary_stream(tmp_path):
    source = read_compound_file(MSI)
    raw = bytearray(source.raw)
    entry = source.find(STREAM_NAME)
    assert entry is not None
    directory = struct.unpack_from("<I", raw, 48)[0]
    base = (directory + 1) * 512 + entry.index * 128
    replacement = "zzz".encode("utf-16-le")
    raw[base : base + len(replacement)] = replacement
    struct.pack_into("<H", raw, base + 64, len(replacement) + 2)
    from stavewright.msi.cfb import CompoundFile

    with pytest.raises(MsiError, match="no summary information stream"):
        read_summary(CompoundFile(bytes(raw)))
