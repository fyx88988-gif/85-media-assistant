import pytest
from pydantic import ValidationError

from media_assistant.update_manifest import UpdateManifest, select_artifacts


MANIFEST = '''{
  "schemaVersion": 1,
  "productVersion": "1.2.0",
  "publishedAt": "2026-09-10T00:00:00Z",
  "minimumVersion": "0.1.0",
  "notesZh": "稳定本地入口",
  "artifacts": [{
    "os": "windows", "arch": "x64", "kind": "engine",
    "url": "https://example.invalid/app-1.2.0-win-x64.zip",
    "mirrors": [], "size": 123,
    "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "signature": "c2ln", "restartRequired": true
  }]
}'''.encode("utf-8")


def test_manifest_selects_exact_operating_system_and_architecture() -> None:
    manifest = UpdateManifest.model_validate_json(MANIFEST)

    artifacts = select_artifacts(manifest, "windows", "x64")

    assert len(artifacts) == 1
    assert str(artifacts[0].url).endswith("win-x64.zip")


def test_manifest_rejects_unknown_fields() -> None:
    invalid = MANIFEST.replace(b'"schemaVersion": 1,', b'"schemaVersion": 1, "extra": true,')

    with pytest.raises(ValidationError):
        UpdateManifest.model_validate_json(invalid)


def test_component_requires_name_and_engine_compatibility_range() -> None:
    invalid = MANIFEST.replace(b'"kind": "engine"', b'"kind": "component"')

    with pytest.raises(ValidationError, match="componentName"):
        UpdateManifest.model_validate_json(invalid)


def test_selector_returns_a_stable_install_order() -> None:
    manifest = UpdateManifest.model_validate(
        {
            "schemaVersion": 1,
            "productVersion": "1.2.0",
            "publishedAt": "2026-09-10T00:00:00Z",
            "minimumVersion": "0.1.0",
            "notesZh": "组件更新",
            "artifacts": [
                _artifact("component", component_name="yt-dlp"),
                _artifact("webui"),
                _artifact("engine"),
                _artifact("component", component_name="ffmpeg"),
            ],
        }
    )

    artifacts = select_artifacts(manifest, "windows", "x64")

    assert [(item.kind, item.component_name) for item in artifacts] == [
        ("engine", None),
        ("webui", None),
        ("component", "ffmpeg"),
        ("component", "yt-dlp"),
    ]


def test_manifest_rejects_duplicate_component_target() -> None:
    artifact = _artifact("component", component_name="yt-dlp")

    with pytest.raises(ValidationError, match="重复"):
        UpdateManifest.model_validate(
            {
                "schemaVersion": 1,
                "productVersion": "1.2.0",
                "publishedAt": "2026-09-10T00:00:00Z",
                "minimumVersion": "0.1.0",
                "notesZh": "重复组件",
                "artifacts": [artifact, artifact],
            }
        )


def test_manifest_rejects_unsupported_windows_arm64_target() -> None:
    unsupported = _artifact("engine")
    unsupported["arch"] = "arm64"

    with pytest.raises(ValidationError, match="系统与架构组合"):
        UpdateManifest.model_validate(
            {
                "schemaVersion": 1,
                "productVersion": "1.2.0",
                "publishedAt": "2026-09-10T00:00:00Z",
                "minimumVersion": "0.1.0",
                "notesZh": "不支持的目标",
                "artifacts": [unsupported],
            }
        )


def _artifact(kind: str, *, component_name: str | None = None) -> dict[str, object]:
    value: dict[str, object] = {
        "os": "windows",
        "arch": "x64",
        "kind": kind,
        "url": f"https://example.invalid/{kind}-{component_name or 'app'}.zip",
        "mirrors": [],
        "size": 123,
        "sha256": "a" * 64,
        "signature": "c2ln",
        "restartRequired": kind in {"engine", "full"},
    }
    if kind == "component":
        value.update(
            componentName=component_name,
            minimumEngineVersion="0.1.0",
            maximumEngineVersion="2.0.0",
        )
    return value
