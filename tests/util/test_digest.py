from __future__ import annotations

import hashlib
from pathlib import PurePosixPath

import pytest

from v4t_setup.util.digest import (
    Digest,
    digest_bytes,
    digest_file,
    digest_many,
    digest_tree,
    iter_tree,
)

EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def test_digest_renders_with_its_algorithm_tag():
    d = Digest("sha256", EMPTY_SHA256)
    assert str(d) == f"sha256:{EMPTY_SHA256}"


def test_short_is_twelve_characters():
    d = Digest("sha256", EMPTY_SHA256)
    assert d.short == EMPTY_SHA256[:12]
    assert len(d.short) == 12


def test_parse_is_the_inverse_of_str():
    original = digest_bytes(b"pcdc_top.rbf")
    assert Digest.parse(str(original)) == original


def test_parse_rejects_an_untagged_hash():
    with pytest.raises(ValueError, match="missing its algorithm tag"):
        Digest.parse(EMPTY_SHA256)


def test_rejects_weak_algorithms():
    with pytest.raises(ValueError, match="unsupported digest algorithm"):
        Digest("md5", "d41d8cd98f00b204e9800998ecf8427e")
    with pytest.raises(ValueError, match="unsupported digest algorithm"):
        digest_bytes(b"x", algorithm="sha1")


def test_rejects_wrong_length():
    with pytest.raises(ValueError, match="must be 64 hex characters"):
        Digest("sha256", "abcd")


def test_rejects_upper_case_hex():
    with pytest.raises(ValueError, match="lower case"):
        Digest("sha256", EMPTY_SHA256.upper())


def test_rejects_non_hex():
    with pytest.raises(ValueError, match="not hexadecimal"):
        Digest("sha256", "z" * 64)


@pytest.mark.parametrize("algorithm", ["sha256", "sha384", "sha512"])
def test_supported_algorithms_round_trip(algorithm):
    d = digest_bytes(b"S-Jet 600", algorithm=algorithm)
    assert d.algorithm == algorithm
    assert d == Digest.parse(str(d))


def test_digest_bytes_matches_hashlib():
    payload = b"FILE_TEMPLATE_VERSION_02"
    assert digest_bytes(payload).hexdigest == hashlib.sha256(payload).hexdigest()


def test_digest_file_matches_digest_bytes(tmp_path):
    blob = bytes(range(256)) * 8192
    target = tmp_path / "pcdc_top.rbf"
    target.write_bytes(blob)
    assert digest_file(target) == digest_bytes(blob)


def test_digests_are_orderable_for_stable_manifests():
    a = Digest("sha256", "0" * 64)
    b = Digest("sha256", "1" * 64)
    assert sorted([b, a]) == [a, b]


def _driver_package(root):
    pkg = root / "ftdimfr"
    (pkg / "i386").mkdir(parents=True)
    (pkg / "ftdibus.inf").write_bytes(b"[Version]\r\nSignature=$Windows NT$\r\n")
    (pkg / "ftdibus.cat").write_bytes(b"\x30\x82catalog")
    (pkg / "i386" / "ftdibus.sys").write_bytes(b"MZ\x90\x00driver")
    (pkg / "i386" / "ftd2xx.dll").write_bytes(b"MZ\x90\x00library")
    return pkg


def test_iter_tree_yields_sorted_relative_posix_paths(tmp_path):
    pkg = _driver_package(tmp_path)
    relatives = [rel for rel, _ in iter_tree(pkg)]
    assert relatives == [
        PurePosixPath("ftdibus.cat"),
        PurePosixPath("ftdibus.inf"),
        PurePosixPath("i386/ftd2xx.dll"),
        PurePosixPath("i386/ftdibus.sys"),
    ]


def test_digest_tree_is_stable_across_walks(tmp_path):
    pkg = _driver_package(tmp_path)
    assert digest_tree(pkg) == digest_tree(pkg)


def test_digest_tree_notices_a_renamed_file(tmp_path):
    pkg = _driver_package(tmp_path)
    before = digest_tree(pkg)
    (pkg / "i386" / "ftdibus.sys").rename(pkg / "i386" / "ftdibus.sys.bak")
    assert digest_tree(pkg) != before


def test_digest_tree_notices_a_content_change(tmp_path):
    pkg = _driver_package(tmp_path)
    before = digest_tree(pkg)
    (pkg / "ftdibus.inf").write_bytes(b"[Version]\r\nSignature=$Windows NT$\r\n; edited\r\n")
    assert digest_tree(pkg) != before


def test_digest_tree_of_an_empty_directory_is_the_empty_hash(tmp_path):
    (tmp_path / "empty").mkdir()
    assert digest_tree(tmp_path / "empty").hexdigest == EMPTY_SHA256


def test_digest_many_keeps_caller_order(tmp_path):
    first = tmp_path / "b.dll"
    second = tmp_path / "a.dll"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    result = digest_many([first, second])
    assert list(result) == [first, second]
    assert result[first] == digest_bytes(b"one")
    assert result[second] == digest_bytes(b"two")
