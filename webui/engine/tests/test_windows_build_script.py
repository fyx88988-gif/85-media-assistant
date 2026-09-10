from pathlib import Path


def test_windows_build_script_falls_back_to_ci_python() -> None:
    repository_root = Path(__file__).resolve().parents[3]
    script = (repository_root / "build" / "Build-LocalWebUI.ps1").read_text(
        encoding="utf-8"
    )

    assert "Get-Command python.exe" in script
    assert "未找到可用的 Python" in script
