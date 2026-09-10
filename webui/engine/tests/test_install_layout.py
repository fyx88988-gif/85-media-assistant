import os
from pathlib import Path

from media_assistant.config import AppConfig
from media_assistant.install_layout import InstallLayout


def test_install_layout_keeps_replaceable_files_away_from_user_data(
    tmp_path: Path,
) -> None:
    layout = InstallLayout.for_root(tmp_path / "app", tmp_path / "data")

    assert layout.versions_dir == tmp_path / "app" / "versions"
    assert layout.components_dir == tmp_path / "app" / "components"
    assert layout.updates_dir == tmp_path / "data" / "updates"
    assert layout.settings_dir == tmp_path / "data" / "settings"
    assert layout.versions_dir.parent != layout.settings_dir.parent


def test_install_layout_uses_platform_executable_suffix(tmp_path: Path) -> None:
    layout = InstallLayout.for_root(tmp_path / "app", tmp_path / "data")
    suffix = ".exe" if os.name == "nt" else ""

    assert layout.launcher_executable.name == f"85数字多媒体下载助手{suffix}"
    assert layout.updater_executable.name == f"85数字多媒体下载助手更新器{suffix}"


def test_app_config_defaults_to_stable_port_and_product_version(tmp_path: Path) -> None:
    config = AppConfig(
        host="127.0.0.1",
        session_token="test-token",
        static_dir=tmp_path / "static",
        data_root=tmp_path / "data",
    )

    assert config.port == 8515
    assert config.product_version == "0.1.0"
    assert config.data_root == tmp_path / "data"

