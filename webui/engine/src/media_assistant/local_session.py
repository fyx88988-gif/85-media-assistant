import base64
import hashlib
import hmac
import os
import time
from dataclasses import dataclass
from pathlib import Path


_SECRET_BYTES = 32
_NONCE_BYTES = 16
_TIMESTAMP_BYTES = 8


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding, altchars=b"-_", validate=True)


@dataclass(frozen=True, slots=True)
class LocalSessionAuthority:
    """Issue expiring browser tokens from a secret stored only on this device."""

    secret: bytes
    ttl_seconds: int = 12 * 60 * 60

    def __post_init__(self) -> None:
        if len(self.secret) != _SECRET_BYTES:
            raise ValueError("本地会话密钥格式无效。")
        if self.ttl_seconds <= 0:
            raise ValueError("本地会话有效期必须大于零。")

    @classmethod
    def load_or_create(
        cls,
        secret_path: Path,
        *,
        ttl_seconds: int = 12 * 60 * 60,
    ) -> "LocalSessionAuthority":
        secret_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(
                secret_path,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_BINARY", 0),
                0o600,
            )
        except FileExistsError:
            secret = secret_path.read_bytes()
        else:
            secret = os.urandom(_SECRET_BYTES)
            try:
                os.write(descriptor, secret)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            try:
                secret_path.chmod(0o600)
            except OSError:
                pass
        return cls(secret=secret, ttl_seconds=ttl_seconds)

    def issue(self, *, now: int | None = None) -> str:
        issued_at = int(time.time()) if now is None else int(now)
        payload = issued_at.to_bytes(_TIMESTAMP_BYTES, "big") + os.urandom(
            _NONCE_BYTES
        )
        signature = hmac.new(self.secret, payload, hashlib.sha256).digest()
        return f"{_encode(payload)}.{_encode(signature)}"

    def validate(self, token: str, *, now: int | None = None) -> bool:
        try:
            payload_text, signature_text = token.split(".", 1)
            payload = _decode(payload_text)
            signature = _decode(signature_text)
        except (ValueError, TypeError):
            return False
        if len(payload) != _TIMESTAMP_BYTES + _NONCE_BYTES:
            return False
        checked_at = int(time.time()) if now is None else int(now)
        issued_at = int.from_bytes(payload[:_TIMESTAMP_BYTES], "big")
        if issued_at > checked_at or checked_at - issued_at > self.ttl_seconds:
            return False
        expected = hmac.new(self.secret, payload, hashlib.sha256).digest()
        return hmac.compare_digest(signature, expected)
