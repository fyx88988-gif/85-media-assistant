import os
from pathlib import Path

import pytest

from media_assistant.bootstrap import (
    current_engine_command,
    install_default_update_channel,
    pending_update_command,
)
from media_assistant.install_layout import InstallLayout
from media_assistant.updater import write_pointer


def test_bootstrap_starts_the_engine_selected_by_current_pointer(tmp_path: Path) -> None:
    layout = InstallLayout.for_root(tmp_path / "app", tmp_path / "data")
    write_pointer(layout.current_pointer, "1.2.0")
    suffix = ".exe" if os.name == "nt" else ""
    executable = (
        layout.versions_dir
        / "1.2.0"
        / "engine"
        / f"85数字多媒体下载助手引擎{suffix}"
    )
    executable.parent.mkdir(parents=True)
    executable.touch()

    assert current_engine_command(layout) == [
        str(executable),
        "--install-root",
        str(layout.install_root),
        "--data-root",
        str(layout.data_root),
        "--no-browser",
    ]


def test_bootstrap_refuses_a_missing_current_engine(tmp_path: Path) -> None:
    layout = InstallLayout.for_root(tmp_path / "app", tmp_path / "data")
    write_pointer(layout.current_pointer, "1.2.0")

    with pytest.raises(FileNotFoundError, match="当前版本不完整"):
        current_engine_command(layout)


def test_bootstrap_installs_bundled_update_channel_once(tmp_path: Path) -> None:
    layout = InstallLayout.for_root(tmp_path / "app", tmp_path / "data")
    bundled = layout.install_root / "config" / "update-channel.json"
    bundled.parent.mkdir(parents=True)
    bundled.write_text('{"publicKey":"bundled"}', encoding="utf-8")

    destination = install_default_update_channel(layout)
    assert destination.read_text(encoding="utf-8") == '{"publicKey":"bundled"}'

    destination.write_text('{"publicKey":"user-choice"}', encoding="utf-8")
    install_default_update_channel(layout)
    assert destination.read_text(encoding="utf-8") == '{"publicKey":"user-choice"}'


def test_bootstrap_applies_a_staged_update_before_starting_engine(tmp_path: Path) -> None:
    layout = InstallLayout.for_root(tmp_path / "app", tmp_path / "data")
    layout.updater_executable.parent.mkdir(parents=True)
    layout.updater_executable.touch()
    staged = layout.updates_dir / "staged.json"
    staged.parent.mkdir(parents=True)
    staged.write_text('{"version":"1.3.0"}', encoding="utf-8")

    assert pending_update_command(layout) == [
        str(layout.updater_executable),
        "--apply",
        str(staged),
        "--data-root",
        str(layout.data_root),
        "--install-root",
        str(layout.install_root),
    ]
