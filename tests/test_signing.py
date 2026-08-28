from __future__ import annotations

import base64
import json
from pathlib import PurePosixPath

import pytest

from stavewright.errors import SignatureError
from stavewright.model.manifest import Manifest
from stavewright.model.payload import PayloadItem, PayloadKind
from stavewright.signing import (
    KEY_ID_LENGTH,
    KeyRing,
    Signature,
    generate_key,
    key_id_for,
    load_private_key,
    load_public_key,
    private_key_to_pem,
    public_key_to_pem,
    read_signature,
    require_valid,
    sign_manifest,
    signature_path_for,
    verify_manifest,
)


@pytest.fixture(scope="module")
def key():
    return generate_key()


@pytest.fixture(scope="module")
def other_key():
    return generate_key()


def _manifest(name: str = "V4T.exe") -> Manifest:
    return Manifest(
        items=[
            PayloadItem(
                item_id="file.v4t",
                kind=PayloadKind.FILE,
                name=name,
                directory="APPDIR",
                source=PurePosixPath("payload/V4T.exe"),
            )
        ]
    )


def test_a_key_id_is_sixteen_hex_characters(key):
    identifier = key_id_for(key.public_key())
    assert len(identifier) == KEY_ID_LENGTH
    assert all(character in "0123456789abcdef" for character in identifier)


def test_the_same_key_always_has_the_same_id(key):
    assert key_id_for(key.public_key()) == key_id_for(key.public_key())


def test_different_keys_have_different_ids(key, other_key):
    assert key_id_for(key.public_key()) != key_id_for(other_key.public_key())


def test_a_signature_verifies(key):
    manifest = _manifest()
    signature = sign_manifest(manifest, key)
    assert verify_manifest(manifest, signature, key.public_key())


def test_a_signature_records_the_key_and_the_digest(key):
    manifest = _manifest()
    signature = sign_manifest(manifest, key)
    assert signature.key_id == key_id_for(key.public_key())
    assert signature.manifest_digest == str(manifest.digest())
    assert signature.signed_at is not None
    assert signature.signed_at.tzinfo is not None


def test_an_altered_manifest_does_not_verify(key):
    signature = sign_manifest(_manifest(), key)
    assert not verify_manifest(_manifest("V4T-patched.exe"), signature, key.public_key())


def test_the_wrong_key_does_not_verify(key, other_key):
    manifest = _manifest()
    signature = sign_manifest(manifest, key)
    assert not verify_manifest(manifest, signature, other_key.public_key())


def test_a_signature_from_another_key_with_the_id_forced_still_fails(key, other_key):
    manifest = _manifest()
    forged = Signature(
        key_id=key_id_for(key.public_key()),
        value=sign_manifest(manifest, other_key).value,
    )
    assert not verify_manifest(manifest, forged, key.public_key())


def test_require_valid_passes_a_good_signature(key):
    manifest = _manifest()
    require_valid(manifest, sign_manifest(manifest, key), key.public_key())


def test_require_valid_names_a_key_mismatch(key, other_key):
    manifest = _manifest()
    signature = sign_manifest(manifest, key)
    with pytest.raises(SignatureError, match="made by a different key") as excinfo:
        require_valid(manifest, signature, other_key.public_key())
    assert excinfo.value.context["signature_key"] == signature.key_id


def test_require_valid_names_a_content_mismatch(key):
    signature = sign_manifest(_manifest(), key)
    with pytest.raises(SignatureError, match="does not match its signature"):
        require_valid(_manifest("tampered.exe"), signature, key.public_key())


def test_private_key_round_trips_through_pem(key):
    pem = private_key_to_pem(key)
    reloaded = load_private_key(pem)
    assert key_id_for(reloaded.public_key()) == key_id_for(key.public_key())


def test_an_encrypted_private_key_round_trips(key):
    pem = private_key_to_pem(key, password=b"bench-secret")
    reloaded = load_private_key(pem, password=b"bench-secret")
    assert key_id_for(reloaded.public_key()) == key_id_for(key.public_key())


def test_the_wrong_password_is_refused(key):
    pem = private_key_to_pem(key, password=b"bench-secret")
    with pytest.raises(SignatureError, match="cannot load signing key"):
        load_private_key(pem, password=b"wrong")


def test_an_encrypted_key_is_not_readable_without_a_password(key):
    pem = private_key_to_pem(key, password=b"bench-secret")
    with pytest.raises(SignatureError, match="cannot load signing key"):
        load_private_key(pem)


def test_public_key_round_trips_through_pem(key):
    reloaded = load_public_key(public_key_to_pem(key.public_key()))
    assert key_id_for(reloaded) == key_id_for(key.public_key())


def test_a_public_key_pem_is_not_a_private_key(key):
    with pytest.raises(SignatureError, match="cannot load signing key"):
        load_private_key(public_key_to_pem(key.public_key()))


def test_rubbish_is_not_a_key():
    with pytest.raises(SignatureError, match="cannot load verification key"):
        load_public_key(b"-----BEGIN NOTHING-----")


def test_signature_round_trips_through_a_dict(key):
    signature = sign_manifest(_manifest(), key)
    assert Signature.from_dict(signature.to_dict()) == signature


def test_signature_round_trips_through_json(key):
    signature = sign_manifest(_manifest(), key)
    assert Signature.from_dict(json.loads(signature.dumps())) == signature


@pytest.mark.parametrize("field", ["algorithm", "key_id", "value"])
def test_a_signature_missing_a_field_is_refused(key, field):
    payload = sign_manifest(_manifest(), key).to_dict()
    del payload[field]
    with pytest.raises(SignatureError, match="missing a field") as excinfo:
        Signature.from_dict(payload)
    assert excinfo.value.context["field"] == field


def test_an_unknown_algorithm_is_refused(key):
    payload = sign_manifest(_manifest(), key).to_dict()
    payload["algorithm"] = "rsa-pkcs1"
    with pytest.raises(SignatureError, match="does not implement"):
        Signature.from_dict(payload)


def test_a_value_that_is_not_base64_is_refused():
    signature = Signature(key_id="0" * 16, value="not base64!!")
    with pytest.raises(SignatureError, match="not base64"):
        _ = signature.raw


def test_the_raw_bytes_come_back(key):
    signature = sign_manifest(_manifest(), key)
    assert signature.raw == base64.b64decode(signature.value)


def test_an_empty_key_ring():
    ring = KeyRing()
    assert len(ring) == 0
    assert ring.key_ids == []
    assert ring.get("nope") is None


def test_adding_a_key_returns_its_id(key):
    ring = KeyRing()
    identifier = ring.add(key.public_key())
    assert identifier == key_id_for(key.public_key())
    assert identifier in ring
    assert len(ring) == 1


def test_adding_a_key_from_pem(key):
    ring = KeyRing()
    identifier = ring.add_pem(public_key_to_pem(key.public_key()))
    assert identifier in ring


def test_adding_the_same_key_twice_keeps_one_entry(key):
    ring = KeyRing()
    ring.add(key.public_key())
    ring.add(key.public_key())
    assert len(ring) == 1


def test_a_ring_verifies_a_trusted_signature(key):
    ring = KeyRing()
    ring.add(key.public_key())
    manifest = _manifest()
    ring.verify(manifest, sign_manifest(manifest, key))


def test_a_ring_refuses_an_untrusted_key(key, other_key):
    ring = KeyRing()
    ring.add(key.public_key())
    manifest = _manifest()
    with pytest.raises(SignatureError, match="does not trust") as excinfo:
        ring.verify(manifest, sign_manifest(manifest, other_key))
    assert excinfo.value.context["trusted"] == ring.key_ids


def test_a_ring_still_checks_the_content(key):
    ring = KeyRing()
    ring.add(key.public_key())
    signature = sign_manifest(_manifest(), key)
    with pytest.raises(SignatureError, match="does not match its signature"):
        ring.verify(_manifest("tampered.exe"), signature)


def test_a_ring_holding_several_keys_picks_the_right_one(key, other_key):
    ring = KeyRing()
    ring.add(key.public_key())
    ring.add(other_key.public_key())
    manifest = _manifest()
    ring.verify(manifest, sign_manifest(manifest, key))
    ring.verify(manifest, sign_manifest(manifest, other_key))


def test_the_signature_path_sits_beside_the_manifest(tmp_path):
    assert signature_path_for(tmp_path / "manifest.json") == tmp_path / "manifest.json.sig"


def test_write_and_read_a_signature(tmp_path, key):
    manifest = _manifest()
    manifest_path = tmp_path / "manifest.json"
    manifest.write(manifest_path)
    signature = sign_manifest(manifest, key)
    signature.write(signature_path_for(manifest_path))
    assert read_signature(signature_path_for(manifest_path)) == signature


def test_read_reports_a_missing_signature(tmp_path):
    with pytest.raises(SignatureError, match="cannot read signature"):
        read_signature(tmp_path / "nope.sig")


def test_read_reports_a_signature_that_is_not_json(tmp_path):
    path = tmp_path / "manifest.json.sig"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(SignatureError, match="not valid JSON"):
        read_signature(path)


def test_read_reports_a_signature_that_is_not_an_object(tmp_path):
    path = tmp_path / "manifest.json.sig"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(SignatureError, match="not a JSON object"):
        read_signature(path)


def test_the_whole_bench_flow(tmp_path, key):
    # Build a bundle, sign it, ship it, and verify it the way a bench would.
    manifest = _manifest()
    manifest_path = tmp_path / "bundle" / "manifest.json"
    manifest.write(manifest_path)
    sign_manifest(manifest, key).write(signature_path_for(manifest_path))

    from stavewright.model.manifest import read_manifest

    ring = KeyRing()
    ring.add_pem(public_key_to_pem(key.public_key()))
    ring.verify(read_manifest(manifest_path), read_signature(signature_path_for(manifest_path)))


def test_a_tampered_bundle_fails_the_bench_flow(tmp_path, key):
    manifest = _manifest()
    manifest_path = tmp_path / "bundle" / "manifest.json"
    manifest.write(manifest_path)
    sign_manifest(manifest, key).write(signature_path_for(manifest_path))

    _manifest("swapped.exe").write(manifest_path)

    from stavewright.model.manifest import read_manifest

    ring = KeyRing()
    ring.add(key.public_key())
    with pytest.raises(SignatureError, match="does not match its signature"):
        ring.verify(read_manifest(manifest_path), read_signature(signature_path_for(manifest_path)))
