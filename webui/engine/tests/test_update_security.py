from base64 import b64encode
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from media_assistant.update_security import (
    UpdateSecurityError,
    sha256_file,
    verify_ed25519,
    verify_signed_manifest,
)


def test_sha256_file_uses_the_real_file_bytes(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.zip"
    artifact.write_bytes(b"abc")

    assert sha256_file(artifact) == (
        "ba7816bf8f01cfea414140de5dae2223"
        "b00361a396177a9cb410ff61f20015ad"
    )


def test_signature_verification_rejects_changed_bytes() -> None:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    signature = private_key.sign(b"original")
    public_key_b64 = b64encode(
        public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
    ).decode("ascii")
    signature_b64 = b64encode(signature).decode("ascii")

    with pytest.raises(UpdateSecurityError, match="签名校验失败"):
        verify_ed25519(public_key_b64, b"changed", signature_b64)


def test_signed_manifest_is_verified_before_it_is_parsed() -> None:
    manifest_bytes = b'''{
      "schemaVersion": 1,
      "productVersion": "1.2.0",
      "publishedAt": "2026-09-10T00:00:00Z",
      "minimumVersion": "0.1.0",
      "notesZh": "signed",
      "artifacts": [{
        "os": "windows", "arch": "x64", "kind": "full",
        "url": "https://example.invalid/full.zip", "mirrors": [],
        "size": 1,
        "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "signature": "c2ln", "restartRequired": true
      }]
    }'''
    private_key = Ed25519PrivateKey.generate()
    public_key_b64 = b64encode(
        private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    ).decode("ascii")
    signature_b64 = b64encode(private_key.sign(manifest_bytes)).decode("ascii")

    manifest = verify_signed_manifest(manifest_bytes, signature_b64, public_key_b64)

    assert manifest.product_version == "1.2.0"


@pytest.mark.parametrize("value", ["not-base64", "", "c2ln"])
def test_signature_verification_hides_invalid_crypto_details(value: str) -> None:
    with pytest.raises(UpdateSecurityError, match="签名校验失败"):
        verify_ed25519(value, b"payload", value)

