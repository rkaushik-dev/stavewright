from __future__ import annotations

import struct
from pathlib import Path

import pytest

from stavewright.msi.cfb import (
    DIFAT_IN_HEADER,
    ENDOFCHAIN,
    HEADER_SIZE,
    SIGNATURE,
    CompoundFile,
    CompoundFileError,
    EntryType,
    is_compound_file,
    read_compound_file,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MSI = REPO_ROOT / "msi-quattro" / "msi-Jet600.msi"
SETUP_EXE = REPO_ROOT / "msi-quattro" / "setup.exe"
SUMMARY = "\x05SummaryInformation"


@pytest.fixture(scope="module")
def shipped() -> CompoundFile:
    return read_compound_file(MSI)


def test_the_shipped_msi_is_a_compound_file():
    assert is_compound_file(MSI)


def test_setup_exe_is_not():
    assert not is_compound_file(SETUP_EXE)


def test_a_missing_file_is_not_one(tmp_path):
    assert not is_compound_file(tmp_path / "gone")


def test_a_directory_is_not_one(tmp_path):
    assert not is_compound_file(tmp_path)


def test_the_sector_sizes_are_the_usual_ones(shipped):
    assert shipped.sector_size == 512
    assert shipped.mini_sector_size == 64
    assert shipped.mini_cutoff == 4096


def test_the_directory_has_a_root_first(shipped):
    assert shipped.entries[0].kind is EntryType.ROOT


def test_the_msi_holds_streams(shipped):
    assert len(shipped) > 30
    assert len(shipped.names) == len(shipped)


def test_the_summary_information_stream_is_there(shipped):
    assert SUMMARY in shipped
    assert shipped.find(SUMMARY) is not None


def test_a_stream_that_is_not_there(shipped):
    assert "NotAStream" not in shipped
    assert shipped.find("NotAStream") is None


def test_reading_the_summary_information(shipped):
    data = shipped.read(SUMMARY)
    assert len(data) > 0
    # A property set stream starts with a two byte byte-order mark of FFFE.
    assert data[:2] == b"\xfe\xff"


def test_reading_a_stream_gives_exactly_its_declared_size(shipped):
    for entry in shipped.entries:
        if entry.is_stream:
            assert len(shipped.read_entry(entry)) == entry.size


def test_the_streams_are_not_all_the_same_size(shipped):
    sizes = {entry.size for entry in shipped.entries if entry.is_stream}
    assert len(sizes) > 5


def test_at_least_one_stream_lives_in_the_mini_stream(shipped):
    small = [e for e in shipped.entries if e.is_stream and 0 < e.size < shipped.mini_cutoff]
    assert small
    assert len(shipped.read_entry(small[0])) == small[0].size


def test_at_least_one_stream_is_a_full_sector_chain(shipped):
    big = [e for e in shipped.entries if e.is_stream and e.size >= shipped.mini_cutoff]
    assert big
    assert len(shipped.read_entry(big[0])) == big[0].size


def test_reading_a_stream_that_is_not_there_says_how_many_there_are(shipped):
    with pytest.raises(CompoundFileError, match="no such stream") as caught:
        shipped.read("NotAStream")
    assert caught.value.context["streams"] == len(shipped)


def test_the_sector_count_matches_the_file_length(shipped):
    assert shipped.sector_count == (len(shipped.raw) - HEADER_SIZE) // shipped.sector_size


def test_reading_the_same_stream_twice_gives_the_same_bytes(shipped):
    assert shipped.read(SUMMARY) == shipped.read(SUMMARY)


def test_a_file_that_is_too_short():
    with pytest.raises(CompoundFileError, match="too short"):
        CompoundFile(SIGNATURE)


def test_a_file_with_the_wrong_signature():
    with pytest.raises(CompoundFileError, match="signature") as caught:
        CompoundFile(b"MZ" + bytes(HEADER_SIZE))
    assert caught.value.context["found"].startswith("4d5a")


def test_reading_something_that_is_not_a_compound_file():
    with pytest.raises(CompoundFileError, match="signature"):
        read_compound_file(SETUP_EXE)


def test_reading_a_file_that_is_not_there(tmp_path):
    with pytest.raises(CompoundFileError, match="cannot read compound file"):
        read_compound_file(tmp_path / "gone.msi")


def damaged(header_offset: int, value: int, *, width: str = "<H") -> bytes:
    raw = bytearray(MSI.read_bytes())
    struct.pack_into(width, raw, header_offset, value)
    return bytes(raw)


def test_an_impossible_sector_shift_is_refused():
    with pytest.raises(CompoundFileError, match="sector sizes"):
        CompoundFile(damaged(30, 31))


def test_a_sector_shift_below_the_mini_one_is_refused():
    with pytest.raises(CompoundFileError, match="sector sizes"):
        CompoundFile(damaged(30, 6))


def test_a_mini_shift_of_zero_is_refused():
    with pytest.raises(CompoundFileError, match="sector sizes"):
        CompoundFile(damaged(32, 0))


def test_a_directory_start_past_the_end_of_the_file():
    with pytest.raises(CompoundFileError):
        CompoundFile(damaged(48, 0x00FFFFF0, width="<I"))


def test_a_fat_entry_that_loops_is_caught():
    raw = bytearray(MSI.read_bytes())
    # Point the first FAT sector's first entry at itself, then send the
    # directory chain through it.
    first_fat = struct.unpack_from("<I", raw, 76)[0]
    fat_offset = (first_fat + 1) * 512
    struct.pack_into("<I", raw, fat_offset, 0)
    struct.pack_into("<I", raw, 48, 0)
    with pytest.raises(CompoundFileError, match="loops back on itself"):
        CompoundFile(bytes(raw))


def test_a_directory_entry_with_an_unknown_type():
    raw = bytearray(MSI.read_bytes())
    directory = struct.unpack_from("<I", raw, 48)[0]
    base = (directory + 1) * 512
    raw[base + 128 + 66] = 9
    with pytest.raises(CompoundFileError, match="type this reader does not know"):
        CompoundFile(bytes(raw))


def test_a_directory_with_no_root():
    raw = bytearray(MSI.read_bytes())
    directory = struct.unpack_from("<I", raw, 48)[0]
    base = (directory + 1) * 512
    raw[base + 66] = int(EntryType.STREAM)
    with pytest.raises(CompoundFileError, match="no root entry"):
        CompoundFile(bytes(raw))


def test_the_header_difat_holds_the_documented_number_of_entries():
    assert DIFAT_IN_HEADER == 109


def test_end_of_chain_is_the_documented_sentinel():
    assert ENDOFCHAIN == 0xFFFFFFFE


def test_a_stream_of_zero_length_reads_as_empty(shipped):
    empty = [entry for entry in shipped.entries if entry.is_stream and entry.size == 0]
    for entry in empty:
        assert shipped.read_entry(entry) == b""


def test_storage_entries_are_not_streams(shipped):
    root = shipped.entries[0]
    assert root.is_storage
    assert not root.is_stream
