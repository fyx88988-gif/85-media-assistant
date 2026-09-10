import base64
import binascii
import hashlib
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .update_manifest import UpdateManifest


class UpdateSecurityError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _decode_base64(value: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise UpdateSecurityError("更新签名校验失败。") from exc


def verify_ed25519(
    public_key_b64: str,
    message: bytes,
    signature_b64: str,
) -> None:
    try:
        public_key = Ed25519PublicKey.from_public_bytes(
            _decode_base64(public_key_b64)
        )
        public_key.verify(_decode_base64(signature_b64), message)
    except (InvalidSignature, ValueError, UpdateSecurityError) as exc:
        raise UpdateSecurityError("更新签名校验失败。") from exc


def verify_signed_manifest(
    manifest_bytes: bytes,
    manifest_signature_b64: str,
    public_key_b64: str,
) -> UpdateManifest:
    verify_ed25519(public_key_b64, manifest_bytes, manifest_signature_b64)
    return UpdateManifest.model_validate_json(manifest_bytes)

