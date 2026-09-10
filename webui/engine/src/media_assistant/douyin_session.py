import json
import os
import shutil
from uuid import uuid4
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .processes import ProcessRunner


@dataclass(frozen=True, slots=True)
class DouyinSessionResult:
    cookie_file: Path
    media: dict[str, Any]


class WindowsDouyinSessionRefresher:
    """Refresh an anonymous Douyin session in an isolated headless browser profile."""

    def __init__(
        self,
        *,
        helper_script: Path,
        module_path: Path,
        session_root: Path,
        runner: ProcessRunner,
        timeout: float = 70.0,
    ) -> None:
        self.helper_script = helper_script
        self.module_path = module_path
        self.session_root = session_root
        self.runner = runner
        self.timeout = timeout

    async def refresh(self, url: str) -> DouyinSessionResult:
        if os.name != "nt":
            raise RuntimeError("当前系统暂未提供抖音匿名会话刷新组件。")
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell or not self.helper_script.is_file() or not self.module_path.is_file():
            raise RuntimeError("抖音匿名会话刷新组件不可用。")
        self.session_root.mkdir(parents=True, exist_ok=True)
        cookie_file = self.session_root / "douyin-cookies.txt"
        media_file = self.session_root / "douyin-media.json"
        attempts_root = self.session_root / "attempts"
        attempts_root.mkdir(parents=True, exist_ok=True)
        last_error = "未能生成有效的抖音匿名会话。"
        last_exception: Exception | None = None
        for _ in range(2):
            attempt_root = attempts_root / uuid4().hex
            attempt_root.mkdir(parents=True, exist_ok=True)
            attempt_cookie = attempt_root / "douyin-cookies.txt"
            attempt_media = attempt_root / "douyin-media.json"
            try:
                try:
                    result = await self.runner.run(
                        [
                            powershell,
                            "-NoProfile",
                            "-WindowStyle",
                            "Hidden",
                            "-ExecutionPolicy",
                            "Bypass",
                            "-File",
                            str(self.helper_script),
                            "-ModulePath",
                            str(self.module_path),
                            "-Url",
                            url,
                            "-SessionRoot",
                            str(attempt_root),
                            "-CookieOutput",
                            str(attempt_cookie),
                            "-MediaOutput",
                            str(attempt_media),
                        ],
                        self.timeout,
                    )
                except TimeoutError as exc:
                    last_error = "抖音页面读取超时。"
                    last_exception = exc
                    continue
                last_exception = None
                if result.returncode != 0:
                    last_error = "抖音独立读取进程未能正常完成。"
                    continue
                if not attempt_cookie.is_file() or attempt_cookie.stat().st_size < 100:
                    last_error = "抖音临时访问凭据生成失败。"
                    continue
                if not attempt_media.is_file():
                    last_error = "抖音公开媒体信息没有生成。"
                    continue
                try:
                    media = json.loads(attempt_media.read_text(encoding="utf-8-sig"))
                except (OSError, json.JSONDecodeError):
                    last_error = "未能读取抖音公开媒体信息。"
                    continue
                if not isinstance(media, dict) or not media.get("Videos"):
                    last_error = "独立浏览器没有读取到可用的抖音视频地址。"
                    continue
                os.replace(attempt_cookie, cookie_file)
                os.replace(attempt_media, media_file)
                return DouyinSessionResult(cookie_file=cookie_file, media=media)
            finally:
                shutil.rmtree(attempt_root, ignore_errors=True)
        if isinstance(last_exception, TimeoutError):
            raise last_exception
        raise RuntimeError(last_error)
