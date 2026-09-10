from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from media_assistant.update_manifest import UpdateManifest


ArtifactKind = Literal["webui", "component", "engine", "full"]
TargetOs = Literal["windows", "macos"]
TargetArch = Literal["x64", "arm64"]


@dataclass(frozen=True, slots=True)
class ArtifactSpec:
    path: Path
    os_name: TargetOs
    arch: TargetArch
    kind: ArtifactKind
    url: str
    mirrors: tuple[str, ...] = ()
    restart_required: bool = True
    component_name: str | None = None
    minimum_engine_version: str | None = None
    maximum_engine_version: str | None = None

    def target_key(self) -> tuple[str, str, str, str]:
        return self.os_name, self.arch, self.kind, self.component_name or ""


@dataclass(frozen=True, slots=True)
class ReleaseSpec:
    product_version: str
    minimum_version: str
    notes_zh: str
    published_at: datetime
    artifacts: tuple[ArtifactSpec, ...]


@dataclass(frozen=True, slots=True)
class GeneratedManifest:
    json_bytes: bytes
    signature: str
    public_key: str


def _decode_private_key(signing_key: str) -> Ed25519PrivateKey:
    if not signing_key:
        raise ValueError("缺少 MEDIA_ASSISTANT_RELEASE_PRIVATE_KEY。")
    try:
        raw_key = base64.b64decode(signing_key, validate=True)
        return Ed25519PrivateKey.from_private_bytes(raw_key)
    except (ValueError, TypeError) as exc:
        raise ValueError("发布私钥必须是 Base64 编码的 32 字节 Ed25519 私钥。") from exc


def _sign(private_key: Ed25519PrivateKey, payload: bytes) -> str:
    return base64.b64encode(private_key.sign(payload)).decode("ascii")


def generate_manifest(
    spec: ReleaseSpec,
    *,
    signing_key: str,
) -> GeneratedManifest:
    private_key = _decode_private_key(signing_key)
    ordered = tuple(sorted(spec.artifacts, key=ArtifactSpec.target_key))
    keys = [artifact.target_key() for artifact in ordered]
    if len(set(keys)) != len(keys):
        raise ValueError("发布文件包含重复的系统、架构和组件目标。")
    if not ordered:
        raise ValueError("发布文件不能为空。")

    artifacts: list[dict[str, object]] = []
    for artifact in ordered:
        if not artifact.path.is_file():
            raise FileNotFoundError(f"发布文件不存在：{artifact.path}")
        payload = artifact.path.read_bytes()
        item: dict[str, object] = {
            "arch": artifact.arch,
            "kind": artifact.kind,
            "mirrors": list(artifact.mirrors),
            "os": artifact.os_name,
            "restartRequired": artifact.restart_required,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "signature": _sign(private_key, payload),
            "size": len(payload),
            "url": artifact.url,
        }
        if artifact.component_name is not None:
            item["componentName"] = artifact.component_name
        if artifact.minimum_engine_version is not None:
            item["minimumEngineVersion"] = artifact.minimum_engine_version
        if artifact.maximum_engine_version is not None:
            item["maximumEngineVersion"] = artifact.maximum_engine_version
        artifacts.append(item)

    published_at = spec.published_at.isoformat().replace("+00:00", "Z")
    document = {
        "artifacts": artifacts,
        "minimumVersion": spec.minimum_version,
        "notesZh": spec.notes_zh,
        "productVersion": spec.product_version,
        "publishedAt": published_at,
        "schemaVersion": 1,
    }
    json_bytes = (
        json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    # Validate the exact bytes before signing so a malformed release cannot be emitted.
    UpdateManifest.model_validate_json(json_bytes)
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return GeneratedManifest(
        json_bytes=json_bytes,
        signature=_sign(private_key, json_bytes),
        public_key=base64.b64encode(public_key).decode("ascii"),
    )


def load_release_spec(path: Path) -> ReleaseSpec:
    raw = json.loads(path.read_text(encoding="utf-8"))
    base = path.resolve().parent
    artifacts = tuple(
        ArtifactSpec(
            path=(base / item["path"]).resolve(),
            os_name=item["os"],
            arch=item["arch"],
            kind=item["kind"],
            url=item["url"],
            mirrors=tuple(item.get("mirrors", ())),
            restart_required=item.get("restartRequired", True),
            component_name=item.get("componentName"),
            minimum_engine_version=item.get("minimumEngineVersion"),
            maximum_engine_version=item.get("maximumEngineVersion"),
        )
        for item in raw["artifacts"]
    )
    return ReleaseSpec(
        product_version=raw["productVersion"],
        minimum_version=raw["minimumVersion"],
        notes_zh=raw["notesZh"],
        published_at=datetime.fromisoformat(raw["publishedAt"].replace("Z", "+00:00")),
        artifacts=artifacts,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成并签名自动更新清单")
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    generated = generate_manifest(
        load_release_spec(args.spec),
        signing_key=os.environ.get("MEDIA_ASSISTANT_RELEASE_PRIVATE_KEY", ""),
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = args.output_dir / "update-manifest.json"
    signature = args.output_dir / "update-manifest.sig"
    public_key = args.output_dir / "update-public-key.txt"
    manifest.write_bytes(generated.json_bytes)
    signature.write_text(generated.signature + "\n", encoding="ascii")
    public_key.write_text(generated.public_key + "\n", encoding="ascii")
    print(manifest)
    print(signature)
    print(public_key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
