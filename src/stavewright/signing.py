"""Detached signatures over a manifest.

A commissioning bundle travels on a USB stick to a factory floor. It passes
through people, and it sits on a bench PC that has no network and no way to ask
anybody whether what it is holding is genuine. The signature is the only thing
standing between a terminal and whatever happened to be on that stick.

Ed25519, because the keys and signatures are short enough to print on a build
sheet and there is nothing to get wrong in the parameters.

The signature is detached. Embedding it would mean the manifest has to be
canonicalised with a hole in it, and every scheme that does that has had at
least one bug where the hole moved.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from stavewright import clock
from stavewright.errors import SignatureError
from stavewright.model.manifest import Manifest
from stavewright.util.fsops import atomic_write_text

ALGORITHM = "ed25519"
KEY_ID_LENGTH = 16
SIGNATURE_SUFFIX = ".sig"


def key_id_for(public_key: Ed25519PublicKey) -> str:
    """A short stable identifier for a key.

    The first sixteen hex characters of the SHA-256 of the raw public key. It
    goes on the build sheet beside the fingerprint so a bench operator can tell
    which key a bundle was signed with without a network.
    """
    raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return hashlib.sha256(raw).hexdigest()[:KEY_ID_LENGTH]


def generate_key() -> Ed25519PrivateKey:
    """A fresh signing key."""
    return Ed25519PrivateKey.generate()


def private_key_to_pem(key: Ed25519PrivateKey, password: bytes | None = None) -> bytes:
    """Serialise a private key, encrypted when a password is given."""
    encryption: serialization.KeySerializationEncryption
    encryption = (
        serialization.BestAvailableEncryption(password)
        if password
        else serialization.NoEncryption()
    )
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=encryption,
    )


def public_key_to_pem(key: Ed25519PublicKey) -> bytes:
    """Serialise a public key."""
    return key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def load_private_key(pem: bytes, password: bytes | None = None) -> Ed25519PrivateKey:
    """Read a private key.

    :raises SignatureError: when the PEM will not load or is not Ed25519.
    """
    try:
        key = serialization.load_pem_private_key(pem, password=password)
    except (ValueError, TypeError) as exc:
        raise SignatureError(
            "cannot load signing key",
            context={"detail": str(exc)},
        ) from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise SignatureError(
            "signing key is not Ed25519",
            context={"type": type(key).__name__},
        )
    return key


def load_public_key(pem: bytes) -> Ed25519PublicKey:
    """Read a public key.

    :raises SignatureError: when the PEM will not load or is not Ed25519.
    """
    try:
        key = serialization.load_pem_public_key(pem)
    except (ValueError, TypeError) as exc:
        raise SignatureError(
            "cannot load verification key",
            context={"detail": str(exc)},
        ) from exc
    if not isinstance(key, Ed25519PublicKey):
        raise SignatureError(
            "verification key is not Ed25519",
            context={"type": type(key).__name__},
        )
    return key


@dataclass(frozen=True, slots=True)
class Signature:
    """A detached signature and enough context to find the right key."""

    key_id: str
    value: str
    algorithm: str = ALGORITHM
    signed_at: datetime | None = None
    manifest_digest: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "algorithm": self.algorithm,
            "key_id": self.key_id,
            "value": self.value,
        }
        if self.signed_at is not None:
            payload["signed_at"] = self.signed_at.isoformat()
        if self.manifest_digest:
            payload["manifest_digest"] = self.manifest_digest
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Signature:
        """Rebuild a signature from its serialised form.

        :raises SignatureError: on a missing field or an algorithm this build
            does not implement.
        """
        for required in ("algorithm", "key_id", "value"):
            if required not in payload:
                raise SignatureError(
                    "signature is missing a field",
                    context={"field": required},
                )
        if payload["algorithm"] != ALGORITHM:
            raise SignatureError(
                "signature uses an algorithm this build does not implement",
                context={"algorithm": payload["algorithm"], "expected": ALGORITHM},
            )
        stamp = payload.get("signed_at")
        return cls(
            key_id=payload["key_id"],
            value=payload["value"],
            algorithm=payload["algorithm"],
            signed_at=datetime.fromisoformat(stamp) if stamp else None,
            manifest_digest=payload.get("manifest_digest", ""),
        )

    def dumps(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, indent=2) + "\n"

    def write(self, path: Path) -> None:
        atomic_write_text(path, self.dumps())

    @property
    def raw(self) -> bytes:
        """The signature bytes.

        :raises SignatureError: when the value is not valid base64.
        """
        try:
            return base64.b64decode(self.value, validate=True)
        except (ValueError, TypeError) as exc:
            raise SignatureError(
                "signature value is not base64",
                context={"detail": str(exc)},
            ) from exc


def sign_manifest(manifest: Manifest, key: Ed25519PrivateKey) -> Signature:
    """Sign the canonical form of *manifest*."""
    payload = manifest.canonical().encode("utf-8")
    return Signature(
        key_id=key_id_for(key.public_key()),
        value=base64.b64encode(key.sign(payload)).decode("ascii"),
        signed_at=clock.now(),
        manifest_digest=str(manifest.digest()),
    )


def verify_manifest(
    manifest: Manifest,
    signature: Signature,
    public_key: Ed25519PublicKey,
) -> bool:
    """True when *signature* is a valid signature over *manifest* by *public_key*."""
    if key_id_for(public_key) != signature.key_id:
        return False
    try:
        public_key.verify(signature.raw, manifest.canonical().encode("utf-8"))
    except InvalidSignature:
        return False
    return True


def require_valid(
    manifest: Manifest,
    signature: Signature,
    public_key: Ed25519PublicKey,
) -> None:
    """Verify, or raise saying which way it failed.

    Key mismatch and signature mismatch are reported separately because they
    mean different things on a bench. The first is usually the wrong build
    sheet and the second is a bundle that has been altered.

    :raises SignatureError: when verification fails.
    """
    expected = key_id_for(public_key)
    if expected != signature.key_id:
        raise SignatureError(
            "signature was made by a different key",
            context={"signature_key": signature.key_id, "supplied_key": expected},
        )
    if not verify_manifest(manifest, signature, public_key):
        raise SignatureError(
            "manifest does not match its signature",
            context={"key_id": signature.key_id, "digest": str(manifest.digest())},
        )


class KeyRing:
    """The public keys a bench is willing to accept."""

    def __init__(self, keys: dict[str, Ed25519PublicKey] | None = None) -> None:
        self._keys: dict[str, Ed25519PublicKey] = dict(keys or {})

    def add(self, public_key: Ed25519PublicKey) -> str:
        """Trust *public_key*. Returns its key id."""
        identifier = key_id_for(public_key)
        self._keys[identifier] = public_key
        return identifier

    def add_pem(self, pem: bytes) -> str:
        """Trust a key read from PEM. Returns its key id."""
        return self.add(load_public_key(pem))

    def __contains__(self, key_id: str) -> bool:
        return key_id in self._keys

    def __len__(self) -> int:
        return len(self._keys)

    @property
    def key_ids(self) -> list[str]:
        return sorted(self._keys)

    def get(self, key_id: str) -> Ed25519PublicKey | None:
        return self._keys.get(key_id)

    def verify(self, manifest: Manifest, signature: Signature) -> None:
        """Verify against whichever trusted key made the signature.

        :raises SignatureError: when no trusted key matches, or the one that
            does fails to verify.
        """
        public_key = self._keys.get(signature.key_id)
        if public_key is None:
            raise SignatureError(
                "signature was made by a key this bench does not trust",
                context={"key_id": signature.key_id, "trusted": self.key_ids},
            )
        require_valid(manifest, signature, public_key)


def signature_path_for(manifest_path: Path) -> Path:
    """Where the detached signature for *manifest_path* lives."""
    return manifest_path.with_name(manifest_path.name + SIGNATURE_SUFFIX)


def read_signature(path: Path) -> Signature:
    """Read a detached signature from disk.

    :raises SignatureError: when the file cannot be read or does not parse.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SignatureError(
            "cannot read signature",
            context={"path": str(path), "detail": str(exc)},
        ) from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SignatureError(
            "signature is not valid JSON",
            context={"path": str(path), "detail": str(exc)},
        ) from exc
    if not isinstance(payload, dict):
        raise SignatureError("signature is not a JSON object", context={"path": str(path)})
    return Signature.from_dict(payload)


__all__ = [
    "ALGORITHM",
    "KEY_ID_LENGTH",
    "SIGNATURE_SUFFIX",
    "KeyRing",
    "Signature",
    "generate_key",
    "key_id_for",
    "load_private_key",
    "load_public_key",
    "private_key_to_pem",
    "public_key_to_pem",
    "read_signature",
    "require_valid",
    "sign_manifest",
    "signature_path_for",
    "verify_manifest",
]
