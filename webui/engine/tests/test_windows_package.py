import json
import os
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows package layout")


@pytest.fixture
def built_tree() -> Path:
    repository_root = Path(__file__).resolve().parents[3]
    return repository_root / ".build" / "local-webui" / "windows-staging"


def test_windows_build_manifest_contains_every_runtime_component(
    built_tree: Path,
) -> None:
    pointer = json.loads((built_tree / "current.json").read_text(encoding="utf-8"))
    version = pointer["version"]
    required = {
        "launcher/85数字多媒体下载助手.exe",
        "launcher/85数字多媒体下载助手更新器.exe",
        f"versions/{version}/engine/85数字多媒体下载助手引擎.exe",
        f"versions/{version}/webui/index.html",
        "components/yt-dlp/yt-dlp.exe",
        "components/ffmpeg/ffmpeg.exe",
        "components/ffmpeg/ffprobe.exe",
        "components/deno/deno.exe",
    }
    actual = {
        path.relative_to(built_tree).as_posix()
        for path in built_tree.rglob("*")
        if path.is_file()
    }

    assert required <= actual
    assert pointer == {"version": version}
