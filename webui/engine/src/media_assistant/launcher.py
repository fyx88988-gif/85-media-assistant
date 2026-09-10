import argparse
import os
import socket
import sys
import threading
import webbrowser
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlencode

import uvicorn
import httpx

from .app import create_app
from .config import AppConfig, PRODUCT_ID
from .downloads import DownloadService
from .douyin_session import WindowsDouyinSessionRefresher
from .events import EventBus
from .items import ItemService
from .install_layout import InstallLayout
from .local_session import LocalSessionAuthority
from .processes import (
    AsyncDownloadRunner,
    AsyncProcessRunner,
    require_binary,
    start_detached_process,
)
from .providers.ytdlp import YtDlpProvider
from .preview import HttpPreviewStreamer
from .verification import BasicOutputVerifier
from .updater import read_pointer


LOCAL_PORT = 8515


class PortConflictError(RuntimeError):
    pass


class UpdateScheduler:
    def schedule(self, delay_seconds: float, command: list[str]) -> None:
        timer = threading.Timer(delay_seconds, start_detached_process, args=(command,))
        timer.daemon = True
        timer.start()


def schedule_update_check(
    layout: InstallLayout,
    delay_seconds: float = 4.0,
    scheduler: UpdateScheduler | None = None,
) -> None:
    command = [
        str(layout.updater_executable),
        "--check",
        "--data-root",
        str(layout.data_root),
        "--install-root",
        str(layout.install_root),
    ]
    (scheduler or UpdateScheduler()).schedule(delay_seconds, command)


class AppInstanceLock:
    """Hold an operating-system file lock for one application runtime directory."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.url_path = path.with_suffix(".url")
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

    def write_url(self, url: str) -> None:
        self.url_path.write_text(url, encoding="utf-8")

    def read_url(self) -> str | None:
        try:
            url = self.url_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return url if url.startswith("http://127.0.0.1:") else None

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


def build_local_url(session_token: str) -> str:
    return f"http://127.0.0.1:{LOCAL_PORT}/?{urlencode({'session': session_token})}"


def probe_existing_instance(
    url: str,
    product_id: str,
    timeout: float = 0.5,
) -> bool:
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            response = client.get(f"{url.rstrip('/')}/api/v1/health")
        if response.status_code != 200:
            return False
        payload = response.json()
    except (httpx.HTTPError, ValueError, TypeError):
        return False
    return payload.get("productId") == product_id


def require_available_local_port(port: int = LOCAL_PORT) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError as exc:
            raise PortConflictError(
                f"本地端口 {port} 已被其他程序占用。"
            ) from exc


def _application_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[4]


def _find_component(
    name: str,
    env_name: str,
    install_root: Path | None = None,
) -> Path:
    extension = ".exe" if os.name == "nt" else ""
    filename = f"{name}{extension}"
    configured = os.environ.get(env_name)
    root = _application_root()
    versioned_component = None
    if install_root is not None:
        component_root = install_root / "components" / name
        active_component = read_pointer(component_root / "current.json")
        if active_component is not None:
            versioned_component = (
                component_root / "versions" / active_component / filename
            )
    candidates = [
        Path(configured) if configured else None,
        versioned_component,
        install_root / "components" / name / filename if install_root else None,
        root / "runtime" / name / filename,
        root.parent.parent / "runtime" / name / filename,
        Path(sys.executable).resolve().parent / "runtime" / name / filename,
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate
    raise FileNotFoundError(f"缺少本地组件：{filename}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--install-root", type=Path)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--no-browser", action="store_true")
    arguments, _ = parser.parse_known_args(argv)
    root = _application_root()
    data_root = arguments.data_root or root / "data"
    install_root = arguments.install_root or (
        root.parent if root.name.casefold() == "launcher" else root
    )
    layout = InstallLayout.for_root(install_root, data_root)
    product_version = read_pointer(layout.current_pointer) or "0.1.0"
    authority = LocalSessionAuthority.load_or_create(
        data_root / "settings" / "local-session.key"
    )
    token = authority.issue()
    url = build_local_url(token)
    instance_lock = AppInstanceLock(data_root / "app-instance.lock")
    if not instance_lock.acquire():
        if probe_existing_instance("http://127.0.0.1:8515", PRODUCT_ID):
            webbrowser.open(url)
            return
        raise PortConflictError("本地端口 8515 已被其他程序占用。")
    if probe_existing_instance("http://127.0.0.1:8515", PRODUCT_ID):
        webbrowser.open(url)
        instance_lock.release()
        return
    require_available_local_port()
    static_dir = Path(__file__).resolve().with_name("static")
    yt_dlp = require_binary(
        _find_component("yt-dlp", "MEDIA_ASSISTANT_YTDLP", install_root)
    )
    try:
        ffmpeg = _find_component("ffmpeg", "MEDIA_ASSISTANT_FFMPEG", install_root)
        os.environ["PATH"] = f"{ffmpeg.parent}{os.pathsep}{os.environ.get('PATH', '')}"
    except FileNotFoundError:
        pass
    try:
        deno = _find_component("deno", "MEDIA_ASSISTANT_DENO", install_root)
        os.environ["PATH"] = f"{deno.parent}{os.pathsep}{os.environ.get('PATH', '')}"
    except FileNotFoundError:
        pass

    events = EventBus()
    process_runner = AsyncProcessRunner()
    douyin_runtime = install_root / "components" / "douyin"
    if getattr(sys, "frozen", False):
        helper_script = douyin_runtime / "Refresh-DouyinSession.ps1"
        douyin_module = douyin_runtime / "VideoDownloader.Douyin.psm1"
    else:
        helper_script = root / "webui" / "engine" / "scripts" / "Refresh-DouyinSession.ps1"
        douyin_module = root / "src" / "VideoDownloader.Douyin.psm1"
    douyin_session_root = data_root / "douyin-session"
    session_refresher = WindowsDouyinSessionRefresher(
        helper_script=helper_script,
        module_path=douyin_module,
        session_root=douyin_session_root,
        runner=process_runner,
    )
    provider = YtDlpProvider(yt_dlp, process_runner, session_refresher=session_refresher)
    items = ItemService(provider, events)
    downloads = DownloadService(
        binary=yt_dlp,
        runner=AsyncDownloadRunner(),
        verifier=BasicOutputVerifier(),
        cookie_file=douyin_session_root / "douyin-cookies.txt",
    )
    app = create_app(
        AppConfig(
            host="127.0.0.1",
            port=LOCAL_PORT,
            session_token=token,
            static_dir=static_dir,
            data_root=data_root,
            product_version=product_version,
            session_authority=authority,
        ),
        item_service=items,
        event_bus=events,
        download_service=downloads,
        preview_streamer=HttpPreviewStreamer(
            cookie_file=douyin_session_root / "douyin-cookies.txt"
        ),
        update_check_scheduler=(
            (lambda: schedule_update_check(layout, delay_seconds=0.0))
            if layout.updater_executable.is_file()
            else None
        ),
    )
    if not arguments.no_browser:
        threading.Timer(.8, lambda: webbrowser.open(url)).start()
    if layout.updater_executable.is_file():
        schedule_update_check(layout)
    try:
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=LOCAL_PORT,
            log_config=None,
            access_log=False,
        )
    finally:
        instance_lock.release()


if __name__ == "__main__":
    main()
