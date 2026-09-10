from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from .versioning import ProductVersion


ArtifactKind = Literal["webui", "component", "engine", "full"]
TargetOs = Literal["windows", "macos"]
TargetArch = Literal["x64", "arm64"]


class ReleaseArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    os: TargetOs
    arch: TargetArch
    kind: ArtifactKind
    url: HttpUrl
    mirrors: tuple[HttpUrl, ...] = ()
    size: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    signature: str = Field(min_length=1)
    restart_required: bool = Field(alias="restartRequired")
    component_name: str | None = Field(default=None, alias="componentName")
    minimum_engine_version: str | None = Field(
        default=None,
        alias="minimumEngineVersion",
    )
    maximum_engine_version: str | None = Field(
        default=None,
        alias="maximumEngineVersion",
    )

    @model_validator(mode="after")
    def validate_component_contract(self) -> "ReleaseArtifact":
        if (self.os, self.arch) not in {
            ("windows", "x64"),
            ("macos", "arm64"),
            ("macos", "x64"),
        }:
            raise ValueError("更新清单包含不支持的系统与架构组合。")
        component_values = (
            self.component_name,
            self.minimum_engine_version,
            self.maximum_engine_version,
        )
        if self.kind == "component":
            if any(value is None or not value.strip() for value in component_values):
                raise ValueError(
                    "componentName、minimumEngineVersion 和 maximumEngineVersion 不能为空。"
                )
            minimum = ProductVersion.parse(self.minimum_engine_version or "")
            maximum = ProductVersion.parse(self.maximum_engine_version or "")
            if minimum > maximum:
                raise ValueError("组件兼容版本范围无效。")
        elif any(value is not None for value in component_values):
            raise ValueError("只有组件更新可以声明 componentName 和引擎兼容范围。")
        return self


class UpdateManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal[1] = Field(alias="schemaVersion")
    product_version: str = Field(alias="productVersion")
    published_at: datetime = Field(alias="publishedAt")
    minimum_version: str = Field(alias="minimumVersion")
    notes_zh: str = Field(alias="notesZh", min_length=1)
    artifacts: tuple[ReleaseArtifact, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_versions_and_unique_targets(self) -> "UpdateManifest":
        ProductVersion.parse(self.product_version)
        ProductVersion.parse(self.minimum_version)
        seen: set[tuple[str, str, str, str | None]] = set()
        for artifact in self.artifacts:
            key = (
                artifact.os,
                artifact.arch,
                artifact.kind,
                artifact.component_name,
            )
            if key in seen:
                raise ValueError("更新清单包含重复的系统、架构和组件目标。")
            seen.add(key)
        return self


def artifact_install_order(artifact: ReleaseArtifact) -> tuple[int, str]:
    priority = {"full": 0, "engine": 0, "webui": 1, "component": 2}
    return priority[artifact.kind], artifact.component_name or ""


def select_artifacts(
    manifest: UpdateManifest,
    os_name: str,
    arch: str,
) -> tuple[ReleaseArtifact, ...]:
    matches = tuple(
        artifact
        for artifact in manifest.artifacts
        if artifact.os == os_name and artifact.arch == arch
    )
    if not matches:
        raise ValueError("更新清单没有匹配当前设备的文件。")
    return tuple(sorted(matches, key=artifact_install_order))
