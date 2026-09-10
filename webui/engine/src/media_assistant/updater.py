import json
import os
import shutil
import stat
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol
from uuid import uuid4

import httpx

from .install_layout import InstallLayout
from .update_manifest import ReleaseArtifact, select_artifacts
from .update_security import (
    UpdateSecurityError,
    sha256_file,
    verify_ed25519,
    verify_signed_manifest,
)
from .versioning import ProductVersion


class UpdateOfflineError(RuntimeError):
    pass


class InsufficientUpdateSpaceError(RuntimeError):
    pass


class UpdateTransport(Protocol):
    def fetch_bytes(self, url: str) -> bytes: ...

    def download(
        self,
        urls: list[str],
        destination: Path,
        expected_size: int,
        resume_from: int = 0,
    ) -> Path: ...


class HttpUpdateTransport:
    """Fetch update metadata and archives without using browser credentials."""

    def __init__(self, client: httpx.Client | None = None) -> None:
        self.client = client or httpx.Client(
            timeout=30.0,
            follow_redirects=True,
            trust_env=False,
        )

    def fetch_bytes(self, url: str) -> bytes:
        try:
            response = self.client.get(url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise UpdateOfflineError("更新源暂时不可用。") from exc
        return response.content

    def download(
        self,
        urls: list[str],
        destination: Path,
        expected_size: int,
        resume_from: int = 0,
    ) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        last_error: httpx.HTTPError | None = None
        for url in urls:
            headers = {"Range": f"bytes={resume_from}-"} if resume_from else {}
            try:
                with self.client.stream("GET", url, headers=headers) as response:
                    response.raise_for_status()
                    append = resume_from > 0 and self._valid_range_response(
                        response,
                        resume_from,
                        expected_size,
                    )
                    mode = "ab" if append else "wb"
                    with destination.open(mode) as output:
                        for chunk in response.iter_bytes(1024 * 1024):
                            output.write(chunk)
                return destination
            except httpx.HTTPError as exc:
                last_error = exc
                continue
        raise UpdateOfflineError("更新文件暂时无法下载。") from last_error

    @staticmethod
    def _valid_range_response(
        response: httpx.Response,
        resume_from: int,
        expected_size: int,
    ) -> bool:
        if response.status_code != 206:
            return False
        expected_prefix = f"bytes {resume_from}-"
        content_range = response.headers.get("Content-Range", "")
        return content_range.startswith(expected_prefix) and content_range.endswith(
            f"/{expected_size}"
        )


@dataclass(frozen=True, slots=True)
class UpdateSource:
    manifest_url: str
    signature_url: str


@dataclass(frozen=True, slots=True)
class UpdateDecision:
    available: bool
    current_version: str
    target_version: str
    artifacts: tuple[ReleaseArtifact, ...] = ()
    notes_zh: str = ""
    message: str = ""


@dataclass(frozen=True, slots=True)
class StagedArtifact:
    artifact: ReleaseArtifact
    archive: Path
    extracted_dir: Path
    executable: Path | None


@dataclass(frozen=True, slots=True)
class StagedUpdate:
    version: str
    artifacts: tuple[StagedArtifact, ...]


@dataclass(frozen=True, slots=True)
class ActivationResult:
    activated: bool
    rolled_back: bool = False
    deferred_reason: str | None = None


def artifact_partial_path(
    layout: InstallLayout,
    version: str,
    artifact: ReleaseArtifact,
) -> Path:
    label = artifact.component_name or artifact.kind
    safe_label = "".join(
        character if character.isalnum() or character in {"-", "_"} else "-"
        for character in label
    )
    return layout.updates_dir / f"{version}-{safe_label}.zip.partial"


def write_pointer(path: Path, version: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    payload = json.dumps(
        {"version": version},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_pointer(path: Path) -> str | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    version = payload.get("version") if isinstance(payload, dict) else None
    if not isinstance(version, str):
        return None
    try:
        ProductVersion.parse(version)
    except ValueError:
        return None
    return version


class UpdateCoordinator:
    def __init__(
        self,
        *,
        layout: InstallLayout,
        transport: UpdateTransport,
        sources: tuple[UpdateSource, ...],
        public_key_b64: str,
        free_space: Callable[[Path], int] | None = None,
        health_probe: Callable[[Path], bool] | None = None,
    ) -> None:
        if not sources:
            raise ValueError("至少需要一个更新源。")
        self.layout = layout
        self.transport = transport
        self.sources = sources
        self.public_key_b64 = public_key_b64
        self.free_space = free_space or (lambda path: shutil.disk_usage(path).free)
        self.health_probe = health_probe or (lambda _: True)

    def check(
        self,
        current: ProductVersion,
        os_name: str,
        arch: str,
    ) -> UpdateDecision:
        manifest = None
        last_offline: UpdateOfflineError | None = None
        for source in self.sources:
            try:
                manifest_bytes = self.transport.fetch_bytes(source.manifest_url)
                signature = self.transport.fetch_bytes(source.signature_url)
            except UpdateOfflineError as exc:
                last_offline = exc
                continue
            manifest = verify_signed_manifest(
                manifest_bytes,
                signature.decode("ascii").strip(),
                self.public_key_b64,
            )
            break
        if manifest is None:
            raise last_offline or UpdateOfflineError("更新源暂时不可用。")

        target = ProductVersion.parse(manifest.product_version)
        if target <= current:
            return UpdateDecision(
                available=False,
                current_version=str_version(current),
                target_version=manifest.product_version,
                message="当前版本已经是最新版本。",
            )

        artifacts = select_artifacts(manifest, os_name, arch)
        full = tuple(item for item in artifacts if item.kind == "full")
        if full:
            artifacts = (full[0],)
        else:
            effective_engine = (
                target
                if any(item.kind == "engine" for item in artifacts)
                else current
            )
            for artifact in artifacts:
                if artifact.kind != "component":
                    continue
                minimum = ProductVersion.parse(artifact.minimum_engine_version or "")
                maximum = ProductVersion.parse(artifact.maximum_engine_version or "")
                if not minimum <= effective_engine <= maximum:
                    return UpdateDecision(
                        available=False,
                        current_version=str_version(current),
                        target_version=manifest.product_version,
                        message="组件与本地引擎不兼容，需要完整版本更新。",
                    )

        return UpdateDecision(
            available=True,
            current_version=str_version(current),
            target_version=manifest.product_version,
            artifacts=artifacts,
            notes_zh=manifest.notes_zh,
            message="发现可用更新。",
        )

    def stage(self, decision: UpdateDecision) -> StagedUpdate:
        if not decision.available or not decision.artifacts:
            raise ValueError("当前没有可暂存的更新。")
        self.layout.updates_dir.mkdir(parents=True, exist_ok=True)
        required_space = (
            sum(artifact.size * 2 for artifact in decision.artifacts)
            + 256 * 1024 * 1024
        )
        if self.free_space(self.layout.updates_dir) < required_space:
            raise InsufficientUpdateSpaceError("更新暂存空间的磁盘空间不足。")

        staged: list[StagedArtifact] = []
        for artifact in decision.artifacts:
            staged.append(self._stage_artifact(decision.target_version, artifact))
        return StagedUpdate(version=decision.target_version, artifacts=tuple(staged))

    def _stage_artifact(
        self,
        version: str,
        artifact: ReleaseArtifact,
    ) -> StagedArtifact:
        partial = artifact_partial_path(self.layout, version, artifact)
        if partial.exists() and partial.stat().st_size >= artifact.size:
            if partial.stat().st_size > artifact.size:
                partial.unlink()
        resume_from = partial.stat().st_size if partial.exists() else 0
        if resume_from < artifact.size:
            urls = [str(artifact.url), *(str(url) for url in artifact.mirrors)]
            self.transport.download(
                urls,
                partial,
                artifact.size,
                resume_from=resume_from,
            )

        archive: Path | None = None
        extracted_dir: Path | None = None
        try:
            if partial.stat().st_size != artifact.size:
                raise UpdateSecurityError("更新文件大小与清单不一致。")
            if sha256_file(partial) != artifact.sha256:
                raise UpdateSecurityError("更新文件完整性校验失败。")
            verify_ed25519(
                self.public_key_b64,
                partial.read_bytes(),
                artifact.signature,
            )
            archive = partial.with_suffix("")
            os.replace(partial, archive)
            extracted_dir = Path(
                tempfile.mkdtemp(
                    prefix=f"staged-{version}-{artifact.kind}-",
                    dir=self.layout.updates_dir,
                )
            )
            self._extract_safe(archive, extracted_dir)
            self._validate_component_group(artifact, extracted_dir)
        except (OSError, zipfile.BadZipFile, UpdateSecurityError) as exc:
            partial.unlink(missing_ok=True)
            if archive is not None:
                archive.unlink(missing_ok=True)
            if extracted_dir is not None:
                shutil.rmtree(extracted_dir, ignore_errors=True)
            if isinstance(exc, UpdateSecurityError):
                raise
            raise UpdateSecurityError("更新压缩包无法安全解包。") from exc

        if archive is None or extracted_dir is None:
            raise UpdateSecurityError("更新压缩包无法安全解包。")
        executable = self._find_engine_executable(extracted_dir, artifact)
        return StagedArtifact(
            artifact=artifact,
            archive=archive,
            extracted_dir=extracted_dir,
            executable=executable,
        )

    def _extract_safe(self, archive: Path, destination: Path) -> None:
        root = destination.resolve()
        with zipfile.ZipFile(archive) as package:
            for info in package.infolist():
                member = PurePosixPath(info.filename)
                mode = (info.external_attr >> 16) & 0o170000
                if (
                    member.is_absolute()
                    or ".." in member.parts
                    or mode == stat.S_IFLNK
                ):
                    raise UpdateSecurityError("更新压缩包路径不安全。")
                target = (destination / Path(*member.parts)).resolve()
                if not target.is_relative_to(root):
                    raise UpdateSecurityError("更新压缩包路径不安全。")
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with package.open(info) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)

    @staticmethod
    def _validate_component_group(
        artifact: ReleaseArtifact,
        extracted_dir: Path,
    ) -> None:
        if artifact.kind != "component" or artifact.component_name != "ffmpeg":
            return
        names = {path.name.lower() for path in extracted_dir.rglob("*") if path.is_file()}
        required = {"ffmpeg.exe", "ffprobe.exe"} if os.name == "nt" else {"ffmpeg", "ffprobe"}
        if not required <= names:
            raise UpdateSecurityError("FFmpeg 和 FFprobe 必须作为同一组件组更新。")

    @staticmethod
    def _find_engine_executable(
        extracted_dir: Path,
        artifact: ReleaseArtifact,
    ) -> Path | None:
        if artifact.kind not in {"engine", "full"}:
            return None
        suffix = ".exe" if os.name == "nt" else ""
        preferred = extracted_dir / "engine" / f"85数字多媒体下载助手引擎{suffix}"
        if preferred.is_file():
            return preferred
        engine_dir = extracted_dir / "engine"
        return next((path for path in engine_dir.rglob("*") if path.is_file()), None)

    def activate(
        self,
        staged: StagedUpdate,
        active_downloads: int = 0,
    ) -> ActivationResult:
        engine_update = any(
            item.artifact.kind in {"engine", "full"} for item in staged.artifacts
        )
        if engine_update and active_downloads > 0:
            return ActivationResult(
                activated=False,
                deferred_reason="正在下载视频，将在任务结束后更新。",
            )

        version_root = self.layout.versions_dir / staged.version
        for item in staged.artifacts:
            if item.artifact.kind == "component":
                self._activate_component(item, staged.version)
            else:
                shutil.copytree(item.extracted_dir, version_root, dirs_exist_ok=True)

        engine_artifact = next(
            (
                item
                for item in staged.artifacts
                if item.artifact.kind in {"engine", "full"}
            ),
            None,
        )
        if engine_artifact is not None:
            executable = engine_artifact.executable or version_root
            if not self.health_probe(executable):
                return ActivationResult(activated=False, rolled_back=True)

        current = read_pointer(self.layout.current_pointer)
        if current is not None:
            write_pointer(self.layout.previous_pointer, current)
        if any(item.artifact.kind != "component" for item in staged.artifacts):
            write_pointer(self.layout.current_pointer, staged.version)
        self._remove_unreferenced_versions()
        return ActivationResult(activated=True)

    def _activate_component(self, staged: StagedArtifact, version: str) -> None:
        component_name = staged.artifact.component_name
        if component_name is None:
            raise UpdateSecurityError("组件更新缺少组件名称。")
        component_root = self.layout.components_dir / component_name
        version_root = component_root / "versions" / version
        shutil.copytree(staged.extracted_dir, version_root, dirs_exist_ok=True)
        current_pointer = component_root / "current.json"
        previous_pointer = component_root / "previous.json"
        current = read_pointer(current_pointer)
        if current is not None:
            write_pointer(previous_pointer, current)
        write_pointer(current_pointer, version)

    def rollback(self) -> ActivationResult:
        current = read_pointer(self.layout.current_pointer)
        previous = read_pointer(self.layout.previous_pointer)
        if current is None or previous is None:
            return ActivationResult(activated=False)
        write_pointer(self.layout.current_pointer, previous)
        write_pointer(self.layout.previous_pointer, current)
        return ActivationResult(activated=True, rolled_back=True)

    def _remove_unreferenced_versions(self) -> None:
        keep = {
            value
            for value in (
                read_pointer(self.layout.current_pointer),
                read_pointer(self.layout.previous_pointer),
            )
            if value is not None
        }
        if not self.layout.versions_dir.is_dir():
            return
        for path in self.layout.versions_dir.iterdir():
            if path.is_dir() and path.name not in keep:
                shutil.rmtree(path)


def str_version(version: ProductVersion) -> str:
    return f"{version.major}.{version.minor}.{version.patch}"
