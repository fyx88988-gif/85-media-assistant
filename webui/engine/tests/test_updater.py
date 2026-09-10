import hashlib
import io
import json
import shutil
import zipfile
from base64 import b64encode
from dataclasses import replace
from pathlib import Path

import pytest
import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from media_assistant.install_layout import InstallLayout
from media_assistant.update_security import UpdateSecurityError
from media_assistant.updater import (
    InsufficientUpdateSpaceError,
    HttpUpdateTransport,
    UpdateCoordinator,
    UpdateOfflineError,
    UpdateSource,
    artifact_partial_path,
    read_pointer,
    write_pointer,
)
from media_assistant.versioning import ProductVersion


class FakeTransport:
    def __init__(self, payload_by_url: dict[str, bytes]) -> None:
        self.payload_by_url = payload_by_url
        self.failed_urls: set[str] = set()
        self.download_calls: list[list[str]] = []
        self.attempted_urls: list[str] = []
        self.resume_offsets: list[int] = []

    def fetch_bytes(self, url: str) -> bytes:
        if url in self.failed_urls or url not in self.payload_by_url:
            raise UpdateOfflineError("offline")
        return self.payload_by_url[url]

    def download(
        self,
        urls: list[str],
        destination: Path,
        expected_size: int,
        resume_from: int = 0,
    ) -> Path:
        self.download_calls.append(urls)
        self.resume_offsets.append(resume_from)
        for url in urls:
            self.attempted_urls.append(url)
            if url in self.failed_urls or url not in self.payload_by_url:
                continue
            payload = self.payload_by_url[url]
            mode = "ab" if resume_from else "wb"
            with destination.open(mode) as stream:
                stream.write(payload[resume_from:])
            return destination
        raise UpdateOfflineError("offline")


@pytest.fixture
def layout(tmp_path: Path) -> InstallLayout:
    return InstallLayout.for_root(tmp_path / "app", tmp_path / "data")


def test_equal_release_version_does_not_download(layout: InstallLayout) -> None:
    coordinator, transport = _coordinator(layout, version="1.2.0")

    decision = coordinator.check(ProductVersion.parse("1.2.0"), "windows", "x64")

    assert decision.available is False
    assert transport.download_calls == []


def test_check_rejects_a_release_older_than_the_installed_version(
    layout: InstallLayout,
) -> None:
    coordinator, _ = _coordinator(layout, version="1.1.0")

    decision = coordinator.check(ProductVersion.parse("1.2.0"), "windows", "x64")

    assert decision.available is False
    assert decision.message == "当前版本已经是最新版本。"


def test_stage_uses_mirror_after_primary_download_failure(
    layout: InstallLayout,
) -> None:
    coordinator, transport = _coordinator(layout, version="1.2.0")
    decision = coordinator.check(ProductVersion.parse("1.1.0"), "windows", "x64")
    artifact = decision.artifacts[0]
    transport.failed_urls.add(str(artifact.url))

    staged = coordinator.stage(decision)

    assert staged.artifacts[0].archive.is_file()
    assert transport.attempted_urls == [str(artifact.url), str(artifact.mirrors[0])]


def test_stage_deletes_partial_archive_when_sha256_is_wrong(
    layout: InstallLayout,
) -> None:
    coordinator, _ = _coordinator(layout, version="1.2.0")
    decision = coordinator.check(ProductVersion.parse("1.1.0"), "windows", "x64")
    bad_artifact = decision.artifacts[0].model_copy(update={"sha256": "0" * 64})
    bad_decision = replace(decision, artifacts=(bad_artifact,))

    with pytest.raises(UpdateSecurityError, match="完整性"):
        coordinator.stage(bad_decision)

    assert list(layout.updates_dir.glob("*.partial")) == []


def test_stage_resumes_from_the_exact_partial_byte_length(
    layout: InstallLayout,
) -> None:
    coordinator, transport = _coordinator(layout, version="1.2.0")
    decision = coordinator.check(ProductVersion.parse("1.1.0"), "windows", "x64")
    partial = artifact_partial_path(layout, decision.target_version, decision.artifacts[0])
    partial.parent.mkdir(parents=True, exist_ok=True)
    payload = transport.payload_by_url[str(decision.artifacts[0].url)]
    partial.write_bytes(payload[:17])

    staged = coordinator.stage(decision)

    assert transport.resume_offsets == [17]
    assert staged.artifacts[0].archive.read_bytes() == payload


def test_stage_does_not_download_when_free_space_is_insufficient(
    layout: InstallLayout,
) -> None:
    coordinator, transport = _coordinator(
        layout,
        version="1.2.0",
        free_space=lambda _: 1,
    )
    decision = coordinator.check(ProductVersion.parse("1.1.0"), "windows", "x64")

    with pytest.raises(InsufficientUpdateSpaceError, match="磁盘空间不足"):
        coordinator.stage(decision)

    assert transport.download_calls == []


def test_stage_rejects_an_archive_that_escapes_the_staging_directory(
    layout: InstallLayout,
) -> None:
    payload = _zip_bytes({"../escaped.exe": b"bad"})
    coordinator, _ = _coordinator(layout, version="1.2.0", archive=payload)
    decision = coordinator.check(ProductVersion.parse("1.1.0"), "windows", "x64")

    with pytest.raises(UpdateSecurityError, match="压缩包路径"):
        coordinator.stage(decision)

    assert not (layout.updates_dir / "escaped.exe").exists()
    assert list(layout.updates_dir.glob("*.zip")) == []


def test_http_transport_resumes_when_server_confirms_the_requested_range(
    tmp_path: Path,
) -> None:
    payload = b"complete-update"
    destination = tmp_path / "update.partial"
    destination.write_bytes(payload[:8])

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Range"] == "bytes=8-"
        return httpx.Response(
            206,
            headers={"Content-Range": f"bytes 8-{len(payload) - 1}/{len(payload)}"},
            content=payload[8:],
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    transport = HttpUpdateTransport(client=client)

    transport.download(
        ["https://updates.example/update.zip"],
        destination,
        len(payload),
        resume_from=8,
    )

    assert destination.read_bytes() == payload


def test_http_transport_restarts_when_server_ignores_range(tmp_path: Path) -> None:
    payload = b"complete-update"
    destination = tmp_path / "update.partial"
    destination.write_bytes(payload[:8])

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Range"] == "bytes=8-"
        return httpx.Response(200, content=payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    transport = HttpUpdateTransport(client=client)

    transport.download(
        ["https://updates.example/update.zip"],
        destination,
        len(payload),
        resume_from=8,
    )

    assert destination.read_bytes() == payload


def test_grouped_ffmpeg_update_requires_ffprobe(layout: InstallLayout) -> None:
    payload = _zip_bytes({"ffmpeg/ffmpeg.exe": b"ffmpeg"})
    coordinator, _ = _coordinator(
        layout,
        version="1.2.0",
        archive=payload,
        kind="component",
        component_name="ffmpeg",
    )
    decision = coordinator.check(ProductVersion.parse("1.1.0"), "windows", "x64")

    with pytest.raises(UpdateSecurityError, match="FFmpeg 和 FFprobe"):
        coordinator.stage(decision)


def test_failed_health_check_restores_previous_pointer(layout: InstallLayout) -> None:
    coordinator, _ = _coordinator(
        layout,
        version="1.2.0",
        health_probe=lambda _: False,
    )
    decision = coordinator.check(ProductVersion.parse("1.1.0"), "windows", "x64")
    staged = coordinator.stage(decision)
    write_pointer(layout.current_pointer, "1.1.0")

    result = coordinator.activate(staged)

    assert result.activated is False
    assert read_pointer(layout.current_pointer) == "1.1.0"
    assert result.rolled_back is True


def test_successful_activation_atomically_switches_version_pointer(
    layout: InstallLayout,
) -> None:
    coordinator, _ = _coordinator(layout, version="1.2.0")
    decision = coordinator.check(ProductVersion.parse("1.1.0"), "windows", "x64")
    staged = coordinator.stage(decision)
    write_pointer(layout.current_pointer, "1.1.0")

    result = coordinator.activate(staged)

    assert result.activated is True
    assert read_pointer(layout.current_pointer) == "1.2.0"
    assert read_pointer(layout.previous_pointer) == "1.1.0"


def test_engine_activation_defers_while_media_download_is_active(
    layout: InstallLayout,
) -> None:
    coordinator, _ = _coordinator(layout, version="1.2.0")
    decision = coordinator.check(ProductVersion.parse("1.1.0"), "windows", "x64")
    staged = coordinator.stage(decision)

    result = coordinator.activate(staged, active_downloads=1)

    assert result.activated is False
    assert result.deferred_reason == "正在下载视频，将在任务结束后更新。"


def test_rollback_swaps_current_and_previous_pointers(layout: InstallLayout) -> None:
    coordinator, _ = _coordinator(layout, version="1.2.0")
    write_pointer(layout.current_pointer, "1.2.0")
    write_pointer(layout.previous_pointer, "1.1.0")

    result = coordinator.rollback()

    assert result.activated is True
    assert result.rolled_back is True
    assert read_pointer(layout.current_pointer) == "1.1.0"
    assert read_pointer(layout.previous_pointer) == "1.2.0"


def _coordinator(
    layout: InstallLayout,
    *,
    version: str,
    archive: bytes | None = None,
    kind: str = "full",
    component_name: str | None = None,
    free_space=lambda _: 10**12,
    health_probe=lambda _: True,
) -> tuple[UpdateCoordinator, FakeTransport]:
    archive = archive or _zip_bytes({"engine/85-media-assistant-engine.exe": b"engine"})
    private_key = Ed25519PrivateKey.generate()
    public_key_b64 = b64encode(
        private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    ).decode("ascii")
    artifact_url = f"https://updates.example/{kind}.zip"
    mirror_url = f"https://mirror.example/{kind}.zip"
    artifact: dict[str, object] = {
        "os": "windows",
        "arch": "x64",
        "kind": kind,
        "url": artifact_url,
        "mirrors": [mirror_url],
        "size": len(archive),
        "sha256": hashlib.sha256(archive).hexdigest(),
        "signature": b64encode(private_key.sign(archive)).decode("ascii"),
        "restartRequired": kind in {"engine", "full"},
    }
    if kind == "component":
        artifact.update(
            componentName=component_name,
            minimumEngineVersion="0.1.0",
            maximumEngineVersion="2.0.0",
        )
    manifest_bytes = (
        json.dumps(
            {
                "schemaVersion": 1,
                "productVersion": version,
                "publishedAt": "2026-09-10T00:00:00Z",
                "minimumVersion": "0.1.0",
                "notesZh": "测试更新",
                "artifacts": [artifact],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    manifest_signature = b64encode(private_key.sign(manifest_bytes))
    transport = FakeTransport(
        {
            "https://updates.example/update-manifest.json": manifest_bytes,
            "https://updates.example/update-manifest.sig": manifest_signature,
            artifact_url: archive,
            mirror_url: archive,
        }
    )
    coordinator = UpdateCoordinator(
        layout=layout,
        transport=transport,
        sources=(
            UpdateSource(
                manifest_url="https://updates.example/update-manifest.json",
                signature_url="https://updates.example/update-manifest.sig",
            ),
        ),
        public_key_b64=public_key_b64,
        free_space=free_space,
        health_probe=health_probe,
    )
    return coordinator, transport


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return stream.getvalue()
