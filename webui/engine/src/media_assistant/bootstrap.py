import argparse
import html
import os
import subprocess
import sys
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import PRODUCT_ID
from .install_layout import InstallLayout
from .launcher import AppInstanceLock, build_local_url, probe_existing_instance
from .processes import start_detached_process
from .updater import read_pointer


def default_data_root() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "85Digital" / "MediaAssistant" / "data"
    return Path.home() / "Library" / "Application Support" / "85Digital" / "MediaAssistant"


def install_root_for_executable(
    executable: Path,
    *,
    platform_name: str = sys.platform,
) -> Path:
    executable_dir = executable.parent
    if platform_name == "darwin" and executable_dir.name == "MacOS":
        return executable_dir.parent / "Resources"
    executable_dir = executable.resolve().parent
    if executable_dir.name.casefold() == "launcher":
        return executable_dir.parent
    return executable_dir


def installed_layout() -> InstallLayout:
    install_root = install_root_for_executable(Path(sys.executable))
    return InstallLayout.for_root(install_root, default_data_root())


def install_default_update_channel(layout: InstallLayout) -> Path:
    bundled = layout.install_root / "config" / "update-channel.json"
    destination = layout.settings_dir / "update-channel.json"
    if destination.is_file() or not bundled.is_file():
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_bytes(bundled.read_bytes())
    os.replace(temporary, destination)
    return destination


def render_macos_launch_agent(template: str, launcher_path: Path) -> str:
    return (
        template.replace("__LABEL__", "com.85digital.media-assistant")
        .replace("__LAUNCHER_PATH__", html.escape(str(launcher_path), quote=True))
    )


def register_macos_launch_agent(
    template_path: Path,
    launcher_path: Path,
    *,
    home: Path | None = None,
) -> Path:
    """Install an idempotent per-user login helper for the local engine."""

    user_home = home or Path.home()
    agents_dir = user_home / "Library" / "LaunchAgents"
    destination = agents_dir / "com.85digital.media-assistant.plist"
    rendered = render_macos_launch_agent(
        template_path.read_text(encoding="utf-8"),
        launcher_path,
    )
    agents_dir.mkdir(parents=True, exist_ok=True)
    current = destination.read_text(encoding="utf-8") if destination.is_file() else ""
    if current != rendered:
        temporary = destination.with_suffix(".plist.tmp")
        temporary.write_text(rendered, encoding="utf-8")
        os.replace(temporary, destination)
        domain = f"gui/{os.getuid()}"
        subprocess.run(
            ["launchctl", "bootout", domain, str(destination)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ["launchctl", "bootstrap", domain, str(destination)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    return destination


def current_engine_command(layout: InstallLayout) -> list[str]:
    version = read_pointer(layout.current_pointer)
    if version is None:
        raise FileNotFoundError("当前版本指针无效，请修复安装。")
    suffix = ".exe" if os.name == "nt" else ""
    executable = (
        layout.versions_dir
        / version
        / "engine"
        / f"85数字多媒体下载助手引擎{suffix}"
    )
    if not executable.is_file():
        raise FileNotFoundError("当前版本不完整，请修复安装。")
    return [
        str(executable),
        "--install-root",
        str(layout.install_root),
        "--data-root",
        str(layout.data_root),
        "--no-browser",
    ]


def pending_update_command(layout: InstallLayout) -> list[str] | None:
    staged = layout.updates_dir / "staged.json"
    if not layout.updater_executable.is_file() or not staged.is_file():
        return None
    return [
        str(layout.updater_executable),
        "--apply",
        str(staged),
        "--data-root",
        str(layout.data_root),
        "--install-root",
        str(layout.install_root),
    ]


def apply_pending_update(layout: InstallLayout) -> None:
    command = pending_update_command(layout)
    if command is None:
        return
    kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "check": False,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    result = subprocess.run(command, **kwargs)
    if result.returncode == 0:
        (layout.updates_dir / "staged.json").unlink(missing_ok=True)


def wait_until_ready(timeout: float = 12.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if probe_existing_instance("http://127.0.0.1:8515", PRODUCT_ID):
            return True
        time.sleep(0.15)
    return False


def ensure_engine_running(
    layout: InstallLayout,
    *,
    probe: Callable[[], bool] | None = None,
    starter: Callable[[list[str]], Any] = start_detached_process,
    waiter: Callable[[], bool] = wait_until_ready,
) -> bool:
    """Ensure the selected local engine is healthy, starting it when needed."""

    health_check = probe or (
        lambda: probe_existing_instance("http://127.0.0.1:8515", PRODUCT_ID)
    )
    if health_check():
        return True
    starter(current_engine_command(layout))
    return waiter()


def supervise_engine(
    layout: InstallLayout,
    *,
    probe: Callable[[], bool] | None = None,
    starter: Callable[[list[str]], Any] = start_detached_process,
    waiter: Callable[[], bool] = wait_until_ready,
    sleeper: Callable[[float], None] = time.sleep,
    poll_interval: float = 2.0,
    max_cycles: int | None = None,
) -> None:
    """Keep the local engine available for the current login session."""

    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        ensure_engine_running(
            layout,
            probe=probe,
            starter=starter,
            waiter=waiter,
        )
        cycles += 1
        if max_cycles is None or cycles < max_cycles:
            sleeper(poll_interval)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="启动 85数字多媒体下载助手")
    parser.add_argument(
        "--background",
        action="store_true",
        help="只启动本地引擎，不自动打开浏览器。",
    )
    parser.add_argument("launch_target", nargs="?", default="")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    layout = installed_layout()
    install_default_update_channel(layout)
    apply_pending_update(layout)
    if sys.platform == "darwin":
        template = (
            layout.install_root
            / "templates"
            / "com.85digital.media-assistant.plist"
        )
        if template.is_file():
            register_macos_launch_agent(
                template,
                Path(sys.executable).resolve(),
            )
    if args.background:
        supervisor_lock = AppInstanceLock(
            layout.data_root / "background-supervisor.lock"
        )
        if not supervisor_lock.acquire():
            return
        try:
            supervise_engine(layout)
        finally:
            supervisor_lock.release()
        return
    url = build_local_url()
    if probe_existing_instance("http://127.0.0.1:8515", PRODUCT_ID):
        if not args.background:
            webbrowser.open(url)
        return
    if not ensure_engine_running(layout):
        raise RuntimeError("本地引擎启动失败，请从设置中修复安装。")
    if not args.background:
        webbrowser.open(url)


if __name__ == "__main__":
    main()
