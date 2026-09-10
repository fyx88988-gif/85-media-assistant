from pathlib import Path


def test_windows_build_script_falls_back_to_ci_python() -> None:
    repository_root = Path(__file__).resolve().parents[3]
    script = (repository_root / "build" / "Build-LocalWebUI.ps1").read_text(
        encoding="utf-8"
    )

    assert "Get-Command python.exe" in script
    assert "未找到可用的 Python" in script


def test_windows_installer_has_no_desktop_icon_and_autostarts_in_background() -> None:
    repository_root = Path(__file__).resolve().parents[3]
    installer = (
        repository_root / "build" / "installer" / "windows" / "85-media-assistant.iss"
    ).read_text(encoding="utf-8")

    assert "desktopicon" not in installer
    assert "{autodesktop}" not in installer
    assert 'ValueData: """{app}\\launcher\\{#LauncherName}"" --background' in installer
