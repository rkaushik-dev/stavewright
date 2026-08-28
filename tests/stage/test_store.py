from __future__ import annotations

import pytest

from stavewright.errors import StageError
from stavewright.stage.store import PREFIX_LENGTH, ObjectStore
from stavewright.util.digest import Digest, digest_bytes


@pytest.fixture
def store(tmp_path):
    return ObjectStore(tmp_path / "_staging")


def test_an_object_is_filed_under_its_digest(store):
    digest = store.put_bytes(b"MZ\x90\x00")
    assert digest == digest_bytes(b"MZ\x90\x00")
    assert store.has(digest)


def test_the_layout_splits_the_digest(store):
    digest = store.put_bytes(b"MZ")
    path = store.path_for(digest)
    assert path.parent.name == digest.hexdigest[:PREFIX_LENGTH]
    assert path.name == digest.hexdigest[PREFIX_LENGTH:]


def test_storing_the_same_bytes_twice_keeps_one_object(store):
    first = store.put_bytes(b"MZ")
    second = store.put_bytes(b"MZ")
    assert first == second
    assert store.stats().objects == 1


def test_the_ocx_pair_dedupes_when_the_bytes_match(store):
    # Both AIP rows point at a C1Sizer.ocx. When they are the same build, the
    # store holds one object and both steps verify against the same digest.
    wow = store.put_bytes(b"MZ-c1sizer")
    native = store.put_bytes(b"MZ-c1sizer")
    assert wow == native
    assert store.stats().objects == 1


def test_the_ocx_pair_does_not_dedupe_when_the_bytes_differ(store):
    wow = store.put_bytes(b"MZ-c1sizer-x86")
    native = store.put_bytes(b"MZ-c1sizer-x64")
    assert wow != native
    assert store.stats().objects == 2


def test_reading_an_object_back(store):
    digest = store.put_bytes(b"MZ\x90\x00")
    assert store.get(digest) == b"MZ\x90\x00"


def test_reading_something_that_is_not_there(store):
    with pytest.raises(StageError, match="not in the store"):
        store.get(digest_bytes(b"never stored"))


def test_storing_a_file(tmp_path, store):
    source = tmp_path / "V4T.exe"
    source.write_bytes(b"MZ\x90\x00")
    digest = store.put_file(source)
    assert store.get(digest) == b"MZ\x90\x00"


def test_storing_a_file_that_is_not_there(tmp_path, store):
    with pytest.raises(StageError, match="cannot read payload file"):
        store.put_file(tmp_path / "missing.exe")


def test_an_empty_object_is_storable(store):
    digest = store.put_bytes(b"")
    assert store.has(digest)
    assert store.get(digest) == b""


def test_a_digest_from_another_algorithm_is_refused(store):
    other = digest_bytes(b"MZ", algorithm="sha512")
    with pytest.raises(StageError, match="does not match the store"):
        store.path_for(other)


def test_a_store_can_be_opened_on_another_algorithm(tmp_path):
    store = ObjectStore(tmp_path / "_staging", algorithm="sha512")
    digest = store.put_bytes(b"MZ")
    assert digest.algorithm == "sha512"
    assert store.get(digest) == b"MZ"


def test_verify_passes_for_an_untouched_object(store):
    digest = store.put_bytes(b"MZ")
    assert store.verify(digest)


def test_verify_fails_for_an_object_that_is_not_there(store):
    assert not store.verify(digest_bytes(b"never stored"))


def test_verify_catches_an_edited_object(store):
    digest = store.put_bytes(b"MZ")
    store.path_for(digest).write_bytes(b"edited on the bench")
    assert not store.verify(digest)


def test_verify_all_reports_only_the_bad_ones(store):
    good = store.put_bytes(b"good")
    bad = store.put_bytes(b"bad")
    store.path_for(bad).write_bytes(b"rotted")
    assert store.verify_all() == [bad]
    assert good not in store.verify_all()


def test_verify_all_is_empty_for_a_clean_store(store):
    store.put_bytes(b"one")
    store.put_bytes(b"two")
    assert store.verify_all() == []


def test_removing_an_object(store):
    digest = store.put_bytes(b"MZ")
    assert store.remove(digest)
    assert not store.has(digest)
    assert not store.remove(digest)


def test_iterating_an_empty_store(store):
    assert list(store.iter_digests()) == []
    assert store.stats().objects == 0
    assert store.stats().bytes == 0


def test_iterating_lists_everything(store):
    digests = {store.put_bytes(bytes([n]) * 32) for n in range(10)}
    assert set(store.iter_digests()) == digests


def test_iteration_is_sorted(store):
    for n in range(10):
        store.put_bytes(bytes([n]) * 32)
    found = [d.hexdigest for d in store.iter_digests()]
    assert found == sorted(found)


def test_stats_count_bytes(store):
    store.put_bytes(b"1234567890")
    store.put_bytes(b"12345")
    stats = store.stats()
    assert stats.objects == 2
    assert stats.bytes == 15


def test_stats_describe_themselves(store):
    store.put_bytes(b"x" * (2 * 1024 * 1024))
    assert store.stats().describe() == "1 objects, 2.0 MB"


def test_pruning_keeps_what_it_is_told_to(store):
    keep = store.put_bytes(b"keep this")
    drop = store.put_bytes(b"drop this")
    removed = store.prune({keep})
    assert removed == [drop]
    assert store.has(keep)
    assert not store.has(drop)


def test_pruning_nothing_removes_everything(store):
    store.put_bytes(b"one")
    store.put_bytes(b"two")
    assert len(store.prune(set())) == 2
    assert store.stats().objects == 0


def test_pruning_an_empty_store(store):
    assert store.prune(set()) == []


def test_a_leftover_temporary_is_not_listed_as_an_object(store):
    digest = store.put_bytes(b"MZ")
    path = store.path_for(digest)
    path.with_name(f"{path.name}.tmp999").write_bytes(b"half written")
    assert list(store.iter_digests()) == [digest]


def test_no_temporary_is_left_behind_after_a_write(store):
    digest = store.put_bytes(b"MZ")
    directory = store.path_for(digest).parent
    assert [p.name for p in directory.iterdir()] == [digest.hexdigest[PREFIX_LENGTH:]]


def test_incoming_objects_are_written_outside_the_object_tree(store):
    # A sha512 object name is 126 characters. Appending a temporary suffix to
    # that pushed the path over the Windows limit on a deep staging directory,
    # so incoming writes go somewhere with a short name instead.
    store.put_bytes(b"MZ")
    assert store.incoming_root.is_dir()
    assert list(store.incoming_root.iterdir()) == []


def test_an_incoming_path_is_never_longer_than_its_object_path(tmp_path):
    # The regression this guards: the old code named the temporary after the
    # object plus a suffix, so writing was always closer to the path limit than
    # the finished object was. Now it is always further from it.
    store = ObjectStore(tmp_path / "_staging", algorithm="sha512")
    digest = store.put_bytes(b"MZ\x90\x00")
    incoming = store.incoming_root / "99999-0"
    assert len(str(incoming)) < len(str(store.path_for(digest)))


def test_a_sha512_object_writes_without_a_suffix(tmp_path):
    store = ObjectStore(tmp_path / "_staging", algorithm="sha512")
    digest = store.put_bytes(b"MZ\x90\x00")
    assert store.get(digest) == b"MZ\x90\x00"
    assert len(digest.hexdigest) == 128


def test_the_store_creates_its_own_directories(tmp_path):
    store = ObjectStore(tmp_path / "deep" / "nested" / "_staging")
    store.put_bytes(b"MZ")
    assert store.objects_root.is_dir()


def test_a_parsed_digest_addresses_the_same_object(store):
    digest = store.put_bytes(b"MZ")
    assert store.get(Digest.parse(str(digest))) == b"MZ"
