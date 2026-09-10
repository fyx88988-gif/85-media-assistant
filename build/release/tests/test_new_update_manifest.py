import base64
from datetime import UTC, datetime
from pathlib import Path

import pytest

from build.release.new_update_manifest import (
    ArtifactSpec,
    ReleaseSpec,
    generate_manifest,
)
from media_assistant.update_manifest import UpdateManifest
from media_assistant.update_security import verify_signed_manifest


TEST_PRIVATE_KEY = base64.b64encode(bytes(range(32))).decode("ascii")


def release_fixture(tmp_path: Path) -> ReleaseSpec:
    windows = tmp_path / "windows.zip"
    windows.write_bytes(b"windows-release")
    macos = tmp_path / "macos.zip"
    macos.write_bytes(b"macos-release")
    return ReleaseSpec(
        product_version="1.2.0",
        minimum_version="1.0.0",
        notes_zh="统一安装与自动更新。",
        published_at=datetime(2026, 9, 10, tzinfo=UTC),
        artifacts=(
            ArtifactSpec(
                path=windows,
                os_name="windows",
                arch="x64",
                kind="full",
                url="https://example.invalid/windows.zip",
            ),
            ArtifactSpec(
                path=macos,
                os_name="macos",
                arch="arm64",
                kind="full",
                url="https://example.invalid/macos.zip",
            ),
        ),
    )


def test_release_manifest_is_deterministic_for_the_same_artifacts(
    tmp_path: Path,
) -> None:
    first = generate_manifest(release_fixture(tmp_path), signing_key=TEST_PRIVATE_KEY)
    second = generate_manifest(release_fixture(tmp_path), signing_key=TEST_PRIVATE_KEY)

    assert first.json_bytes == second.json_bytes
    assert first.signature == second.signature
    assert first.json_bytes.endswith(b"\n")


def test_generated_manifest_and_artifacts_have_valid_signatures(tmp_path: Path) -> None:
    generated = generate_manifest(release_fixture(tmp_path), signing_key=TEST_PRIVATE_KEY)

    manifest = verify_signed_manifest(
        generated.json_bytes,
        generated.signature,
        generated.public_key,
    )

    assert isinstance(manifest, UpdateManifest)
    assert manifest.product_version == "1.2.0"
    assert [artifact.os for artifact in manifest.artifacts] == ["macos", "windows"]


def test_release_manifest_rejects_duplicate_targets(tmp_path: Path) -> None:
    fixture = release_fixture(tmp_path)
    duplicate = ReleaseSpec(
        product_version=fixture.product_version,
        minimum_version=fixture.minimum_version,
        notes_zh=fixture.notes_zh,
        published_at=fixture.published_at,
        artifacts=(fixture.artifacts[0], fixture.artifacts[0]),
    )

    with pytest.raises(ValueError, match="重复"):
        generate_manifest(duplicate, signing_key=TEST_PRIVATE_KEY)

