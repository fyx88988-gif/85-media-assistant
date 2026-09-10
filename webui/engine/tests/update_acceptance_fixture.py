from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import threading
import zipfile
from base64 import b64encode
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from media_assistant.install_layout import InstallLayout
from media_assistant.updater import (
    HttpUpdateTransport,
    UpdateCoordinator,
    UpdateSource,
    read_pointer,
    write_pointer,
)
from media_assistant.versioning import ProductVersion


class _QuietReleaseHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


class InstalledDeviceFixture:
    """A loopback release channel and task-owned simulated installed device."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.release_root = root / "release"
        self.release_root.mkdir(parents=True)
        self.private_key = Ed25519PrivateKey.generate()
        self.public_key_b64 = b64encode(
            self.private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        ).decode("ascii")
        handler = partial(_QuietReleaseHandler, directory=str(self.release_root))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        self.install_runs = 0

    def install(self, version: str) -> "InstalledDevice":
        self.install_runs += 1
        layout = InstallLayout.for_root(self.root / "app", self.root / "data")
        version_root = layout.versions_dir / version
        engine = version_root / "engine" / _engine_filename()
        engine.parent.mkdir(parents=True, exist_ok=True)
        engine.write_bytes(f"version={version}".encode("ascii"))
        write_pointer(layout.current_pointer, version)
        download_fixture = self.root / "user-downloads" / "keep.mp4"
        download_fixture.parent.mkdir(parents=True, exist_ok=True)
        download_fixture.write_bytes(b"keep-user-video")
        return InstalledDevice(self, layout, download_fixture)

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class InstalledDevice:
    def __init__(
        self,
        fixture: InstalledDeviceFixture,
        layout: InstallLayout,
        download_fixture: Path,
    ) -> None:
        self.fixture = fixture
        self.layout = layout
        self.download_fixture = download_fixture
        self.last_activation = None

    @property
    def installer_run_count(self) -> int:
        return self.fixture.install_runs

    def publish_valid_update(self, version: str) -> None:
        self._publish(version, f"version={version}".encode("ascii"))

    def publish_corrupt_update(self, version: str) -> None:
        # The archive and signatures are authentic; the staged engine fails its
        # self-test so activation must retain the last healthy pointer.
        self._publish(version, b"corrupt-engine")

    def check_apply_and_restart(self) -> None:
        current = read_pointer(self.layout.current_pointer)
        if current is None:
            raise AssertionError("installed device has no current version")
        coordinator = UpdateCoordinator(
            layout=self.layout,
            transport=HttpUpdateTransport(),
            sources=(
                UpdateSource(
                    manifest_url=f"{self.fixture.base_url}/update-manifest.json",
                    signature_url=f"{self.fixture.base_url}/update-manifest.sig",
                ),
            ),
            public_key_b64=self.fixture.public_key_b64,
            free_space=lambda _: 10**12,
            health_probe=self._engine_self_test,
        )
        decision = coordinator.check(ProductVersion.parse(current), *_target())
        if not decision.available:
            return
        staged = coordinator.stage(decision)
        self.last_activation = coordinator.activate(staged)

    def health(self) -> dict[str, str]:
        current = read_pointer(self.layout.current_pointer)
        if current is None:
            raise AssertionError("installed device has no healthy version")
        engine = self.layout.versions_dir / current / "engine" / _engine_filename()
        if not self._engine_self_test(engine):
            raise AssertionError("current version did not pass its self-test")
        return {"productVersion": current}

    @staticmethod
    def _engine_self_test(executable: Path) -> bool:
        try:
            return executable.read_bytes().startswith(b"version=")
        except OSError:
            return False

    def _publish(self, version: str, engine_payload: bytes) -> None:
        artifact_name = f"full-{_target()[0]}-{_target()[1]}-{version}.zip"
        artifact = self.fixture.release_root / artifact_name
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as package:
            package.writestr(f"engine/{_engine_filename()}", engine_payload)
            package.writestr("webui/index.html", f"<title>{version}</title>")
        payload = stream.getvalue()
        artifact.write_bytes(payload)
        artifact_signature = b64encode(self.fixture.private_key.sign(payload)).decode(
            "ascii"
        )
        os_name, arch = _target()
        manifest_bytes = json.dumps(
            {
                "schemaVersion": 1,
                "productVersion": version,
                "publishedAt": "2026-09-10T00:00:00Z",
                "minimumVersion": "1.0.0",
                "notesZh": "本地升级验收",
                "artifacts": [
                    {
                        "os": os_name,
                        "arch": arch,
                        "kind": "full",
                        "url": f"{self.fixture.base_url}/{artifact_name}",
                        "mirrors": [],
                        "size": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "signature": artifact_signature,
                        "restartRequired": True,
                    }
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        (self.fixture.release_root / "update-manifest.json").write_bytes(
            manifest_bytes
        )
        manifest_signature = b64encode(
            self.fixture.private_key.sign(manifest_bytes)
        )
        (self.fixture.release_root / "update-manifest.sig").write_bytes(
            manifest_signature
        )


def _engine_filename() -> str:
    suffix = ".exe" if os.name == "nt" else ""
    return f"85数字多媒体下载助手引擎{suffix}"


def _target() -> tuple[str, str]:
    if platform.system() == "Darwin":
        machine = platform.machine().lower()
        return "macos", "arm64" if machine in {"arm64", "aarch64"} else "x64"
    return "windows", "x64"
