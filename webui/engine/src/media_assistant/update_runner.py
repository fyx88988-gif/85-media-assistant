import argparse
import json
import os
import platform
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, Literal, Protocol
from uuid import uuid4

from .install_layout import InstallLayout
from .update_manifest import ReleaseArtifact
from .update_security import UpdateSecurityError
from .updater import (
    ActivationResult,
    HttpUpdateTransport,
    InsufficientUpdateSpaceError,
    StagedArtifact,
    StagedUpdate,
    UpdateCoordinator,
    UpdateOfflineError,
    UpdateSource,
    read_pointer,
)
from .versioning import ProductVersion


UpdateState = Literal[
    "current",
    "checking",
    "available",
    "staged",
    "restart-required",
    "offline",
    "security-error",
    "rollback-complete",
    "error",
]
VALID_UPDATE_STATES = {
    "current",
    "checking",
    "available",
    "staged",
    "restart-required",
    "offline",
    "security-error",
    "rollback-complete",
    "error",
}


class UpdateConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class UpdateStatus:
    state: UpdateState
    current_version: str = "0.1.0"
    available_version: str | None = None
    progress: int | None = None
    checked_at: str | None = None
    message: str = ""


class RunnerCoordinator(Protocol):
    def check_and_stage(self) -> object: ...

    def apply_staged(
        self,
        path: Path,
        wait_pid: int | None,
    ) -> ActivationResult: ...

    def rollback(self) -> object: ...


class UpdateRunLock:
    """Cross-platform advisory lock preventing concurrent updater runs."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: BinaryIO | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle is None:
            return
        self._handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None


class UpdateStatusStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> UpdateStatus:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return UpdateStatus(state="current", message="当前已是最新版本。")
        except (OSError, ValueError, TypeError) as exc:
            raise ValueError("更新状态格式无效。") from exc
        if not isinstance(payload, dict) or payload.get("state") not in VALID_UPDATE_STATES:
            raise ValueError("更新状态格式无效。")
        try:
            return UpdateStatus(
                state=payload["state"],
                current_version=str(payload.get("currentVersion", "0.1.0")),
                available_version=payload.get("availableVersion"),
                progress=payload.get("progress"),
                checked_at=payload.get("checkedAt"),
                message=str(payload.get("message", "")),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("更新状态格式无效。") from exc

    def write(self, status: UpdateStatus) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        payload = {
            "state": status.state,
            "currentVersion": status.current_version,
            "availableVersion": status.available_version,
            "progress": status.progress,
            "checkedAt": status.checked_at,
            "message": status.message,
        }
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)


class CoordinatorWorkflow:
    def __init__(
        self,
        coordinator: UpdateCoordinator,
        layout: InstallLayout,
        current_version: str,
    ) -> None:
        self.coordinator = coordinator
        self.layout = layout
        self.current_version = current_version

    def check_and_stage(self):
        os_name = "windows" if os.name == "nt" else "macos"
        machine = platform.machine().casefold()
        arch = "arm64" if machine in {"arm64", "aarch64"} else "x64"
        current = read_pointer(self.layout.current_pointer) or self.current_version
        decision = self.coordinator.check(ProductVersion.parse(current), os_name, arch)
        if not decision.available:
            return decision, None
        staged = self.coordinator.stage(decision)
        write_staged_descriptor(self.layout.updates_dir / "staged.json", staged)
        return decision, staged

    def apply_staged(
        self,
        path: Path,
        wait_pid: int | None,
    ) -> ActivationResult:
        staged = read_staged_descriptor(path, self.layout.updates_dir)
        if wait_pid is not None:
            wait_for_process_exit(wait_pid)
        return self.coordinator.activate(staged)

    def rollback(self):
        return self.coordinator.rollback()


def _application_root() -> Path:
    if getattr(sys, "frozen", False):
        executable_dir = Path(sys.executable).resolve().parent
        return executable_dir.parent if executable_dir.name.casefold() == "launcher" else executable_dir
    return Path(__file__).resolve().parents[4]


def build_coordinator(
    data_root: Path,
    install_root: Path | None = None,
) -> CoordinatorWorkflow:
    config_path = data_root / "settings" / "update-channel.json"
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        public_key = str(payload["publicKey"])
        primary = UpdateSource(
            manifest_url=str(payload["manifestUrl"]),
            signature_url=str(payload["manifestSignatureUrl"]),
        )
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise UpdateConfigurationError("自动更新配置不可用。") from exc

    sources = [primary]
    for mirror in payload.get("mirrors", []):
        try:
            sources.append(
                UpdateSource(
                    manifest_url=str(mirror["manifestUrl"]),
                    signature_url=str(mirror["manifestSignatureUrl"]),
                )
            )
        except (TypeError, KeyError) as exc:
            raise UpdateConfigurationError("自动更新镜像配置不可用。") from exc
    layout = InstallLayout.for_root(install_root or _application_root(), data_root)
    coordinator = UpdateCoordinator(
        layout=layout,
        transport=HttpUpdateTransport(),
        sources=tuple(sources),
        public_key_b64=public_key,
    )
    return CoordinatorWorkflow(coordinator, layout, "0.1.0")


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def write_staged_descriptor(path: Path, staged: StagedUpdate) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": staged.version,
        "artifacts": [
            {
                "artifact": item.artifact.model_dump(mode="json", by_alias=True),
                "archive": str(item.archive),
                "extractedDir": str(item.extracted_dir),
                "executable": str(item.executable) if item.executable else None,
            }
            for item in staged.artifacts
        ],
    }
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_staged_descriptor(path: Path, updates_dir: Path) -> StagedUpdate:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        version = str(payload["version"])
        ProductVersion.parse(version)
        artifact_payloads = payload["artifacts"]
        if not isinstance(artifact_payloads, list) or not artifact_payloads:
            raise ValueError
        safe_root = updates_dir.resolve()
        staged: list[StagedArtifact] = []
        for item in artifact_payloads:
            archive = Path(item["archive"]).resolve()
            extracted_dir = Path(item["extractedDir"]).resolve()
            executable_value = item.get("executable")
            executable = Path(executable_value).resolve() if executable_value else None
            for candidate in (archive, extracted_dir, executable):
                if candidate is not None and not candidate.is_relative_to(safe_root):
                    raise ValueError
            staged.append(
                StagedArtifact(
                    artifact=ReleaseArtifact.model_validate(item["artifact"]),
                    archive=archive,
                    extracted_dir=extracted_dir,
                    executable=executable,
                )
            )
        return StagedUpdate(version=version, artifacts=tuple(staged))
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise UpdateConfigurationError("暂存更新描述不可用。") from exc


def wait_for_process_exit(pid: int, timeout: float = 30.0) -> None:
    if pid <= 0 or pid == os.getpid():
        raise UpdateConfigurationError("等待的进程编号无效。")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except (OSError, ProcessLookupError):
            return
        time.sleep(0.1)
    raise UpdateConfigurationError("应用退出超时，更新将在下次启动时重试。")


def run(argv: list[str] | None = None, *, data_root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--rollback", action="store_true")
    mode.add_argument("--apply", metavar="STAGED_FILE")
    parser.add_argument("--wait-pid", type=int)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--install-root", type=Path)
    try:
        arguments = parser.parse_args(argv)
    except SystemExit:
        return 2
    root = data_root or arguments.data_root
    if root is None:
        return 2
    store = UpdateStatusStore(root / "updates" / "status.json")
    update_lock = UpdateRunLock(root / "updates" / "updater.lock")
    if not update_lock.acquire():
        return 0
    try:
        coordinator = (
            build_coordinator(root, arguments.install_root)
            if arguments.install_root is not None
            else build_coordinator(root)
        )
        current = store.read().current_version
        if arguments.rollback:
            result = coordinator.rollback()
            state: UpdateState = "rollback-complete" if result.rolled_back else "error"
            store.write(
                UpdateStatus(
                    state=state,
                    current_version=current,
                    checked_at=_now(),
                    message="已恢复上一版本。" if result.rolled_back else "没有可恢复的上一版本。",
                )
            )
            return 0 if result.rolled_back else 4
        if arguments.apply:
            descriptor = Path(arguments.apply)
            result = coordinator.apply_staged(descriptor, arguments.wait_pid)
            try:
                target_version = str(
                    json.loads(descriptor.read_text(encoding="utf-8"))["version"]
                )
                ProductVersion.parse(target_version)
            except (OSError, KeyError, TypeError, ValueError) as exc:
                raise UpdateConfigurationError("暂存更新描述不可用。") from exc
            if result.activated:
                store.write(
                    UpdateStatus(
                        state="current",
                        current_version=target_version,
                        checked_at=_now(),
                        message="更新已完成。",
                    )
                )
                return 0
            store.write(
                UpdateStatus(
                    state="error",
                    current_version=current,
                    checked_at=_now(),
                    message=(
                        "新版本启动检查失败，已恢复上一版本。"
                        if result.rolled_back
                        else result.deferred_reason or "更新应用失败。"
                    ),
                )
            )
            return 4

        store.write(
            UpdateStatus(
                state="checking",
                current_version=current,
                checked_at=_now(),
                message="正在检查更新。",
            )
        )
        decision, staged = coordinator.check_and_stage()
        if not decision.available:
            store.write(
                UpdateStatus(
                    state="current",
                    current_version=current,
                    checked_at=_now(),
                    message=decision.message,
                )
            )
            return 0
        restart_required = any(
            item.artifact.restart_required for item in staged.artifacts
        )
        store.write(
            UpdateStatus(
                state="restart-required" if restart_required else "staged",
                current_version=current,
                available_version=decision.target_version,
                progress=100,
                checked_at=_now(),
                message=(
                    "更新已准备完成，将在下次启动时应用。"
                    if restart_required
                    else "更新已准备完成。"
                ),
            )
        )
        return 0
    except UpdateOfflineError:
        store.write(
            UpdateStatus(
                state="offline",
                current_version="0.1.0",
                checked_at=_now(),
                message="暂时无法检查更新，正在使用当前版本。",
            )
        )
        return 0
    except UpdateSecurityError:
        store.write(
            UpdateStatus(
                state="security-error",
                current_version="0.1.0",
                checked_at=_now(),
                message="更新未通过安全校验，已继续使用当前版本。",
            )
        )
        return 3
    except (UpdateConfigurationError, InsufficientUpdateSpaceError, ValueError):
        store.write(
            UpdateStatus(
                state="error",
                current_version="0.1.0",
                checked_at=_now(),
                message="自动更新暂时不可用，已继续使用当前版本。",
            )
        )
        return 2
    finally:
        update_lock.release()


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
