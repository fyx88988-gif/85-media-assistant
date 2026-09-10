import plistlib
from pathlib import Path

from media_assistant.bootstrap import (
    install_root_for_executable,
    render_macos_launch_agent,
)


def test_macos_launcher_uses_the_app_resources_as_install_root() -> None:
    executable = Path(
        "/Applications/85数字多媒体下载助手.app/Contents/MacOS/85数字多媒体下载助手"
    )

    assert install_root_for_executable(executable, platform_name="darwin") == Path(
        "/Applications/85数字多媒体下载助手.app/Contents/Resources"
    )


def test_launch_agent_starts_the_gui_launcher_without_a_terminal() -> None:
    template = """<?xml version="1.0" encoding="UTF-8"?>
<plist><dict>
<key>Label</key><string>__LABEL__</string>
<key>ProgramArguments</key><array><string>__LAUNCHER_PATH__</string></array>
<key>RunAtLoad</key><true/>
</dict></plist>
"""

    rendered = render_macos_launch_agent(
        template,
        Path("/Applications/85数字多媒体下载助手.app/Contents/MacOS/85数字多媒体下载助手"),
    )

    assert "com.85digital.media-assistant" in rendered
    assert "__LAUNCHER_PATH__" not in rendered
    assert "RunAtLoad" in rendered


def test_macos_build_script_bundles_all_runtime_parts() -> None:
    repository_root = Path(__file__).resolve().parents[3]
    script = (repository_root / "build" / "Build-LocalWebUI-macOS.sh").read_text(
        encoding="utf-8"
    )

    for required in (
        "run_launcher.py",
        "run_updater.py",
        "ffmpeg",
        "ffprobe",
        "yt-dlp",
        "deno",
        "current.json",
        "Contents/Resources/versions",
    ):
        assert required in script


def test_macos_launcher_runs_as_a_background_accessory() -> None:
    repository_root = Path(__file__).resolve().parents[3]
    script = (repository_root / "build" / "Build-LocalWebUI-macOS.sh").read_text(
        encoding="utf-8"
    )

    assert "<key>LSUIElement</key><true/>" in script
    assert "<string>--background</string>" in (
        repository_root
        / "build"
        / "installer"
        / "macos"
        / "com.85digital.media-assistant.plist"
    ).read_text(encoding="utf-8")


def test_macos_launch_agent_keeps_the_background_supervisor_alive() -> None:
    repository_root = Path(__file__).resolve().parents[3]
    template = (
        repository_root
        / "build"
        / "installer"
        / "macos"
        / "com.85digital.media-assistant.plist"
    ).read_text(encoding="utf-8")
    rendered = render_macos_launch_agent(
        template,
        Path("/Applications/85数字多媒体下载助手.app/Contents/MacOS/85数字多媒体下载助手"),
    )
    payload = plistlib.loads(rendered.encode("utf-8"))

    assert payload["KeepAlive"] is True
    assert payload["RunAtLoad"] is True
    assert payload["ProgramArguments"][-1] == "--background"


def test_macos_bundle_registers_the_shared_browser_launch_protocol() -> None:
    repository_root = Path(__file__).resolve().parents[3]
    script = (repository_root / "build" / "Build-LocalWebUI-macOS.sh").read_text(
        encoding="utf-8"
    )

    assert "CFBundleURLTypes" in script
    assert "mediaassistant85" in script


def test_macos_build_scripts_use_unix_line_endings() -> None:
    repository_root = Path(__file__).resolve().parents[3]
    scripts = (
        repository_root / "build" / "Build-LocalWebUI-macOS.sh",
        repository_root / "build" / "installer" / "macos" / "build-dmg.sh",
    )

    for script in scripts:
        assert b"\r\n" not in script.read_bytes(), f"{script} must use LF line endings"
