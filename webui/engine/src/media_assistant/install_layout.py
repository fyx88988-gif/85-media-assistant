import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class InstallLayout:
    """Paths that keep replaceable program files separate from user data."""

    install_root: Path
    data_root: Path
    launcher_dir: Path
    launcher_executable: Path
    updater_executable: Path
    versions_dir: Path
    current_pointer: Path
    previous_pointer: Path
    components_dir: Path
    updates_dir: Path
    settings_dir: Path
    logs_dir: Path

    @classmethod
    def for_root(cls, install_root: Path, data_root: Path) -> "InstallLayout":
        executable_suffix = ".exe" if os.name == "nt" else ""
        launcher_dir = install_root / "launcher"
        return cls(
            install_root=install_root,
            data_root=data_root,
            launcher_dir=launcher_dir,
            launcher_executable=(
                launcher_dir / f"85数字多媒体下载助手{executable_suffix}"
            ),
            updater_executable=(
                launcher_dir / f"85数字多媒体下载助手更新器{executable_suffix}"
            ),
            versions_dir=install_root / "versions",
            current_pointer=install_root / "current.json",
            previous_pointer=install_root / "previous.json",
            components_dir=install_root / "components",
            updates_dir=data_root / "updates",
            settings_dir=data_root / "settings",
            logs_dir=data_root / "logs",
        )

