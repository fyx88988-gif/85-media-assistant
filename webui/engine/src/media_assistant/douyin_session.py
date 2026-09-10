import asyncio
import base64
from datetime import datetime
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from uuid import uuid4
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.request import ProxyHandler, build_opener

from .processes import ProcessRunner


@dataclass(frozen=True, slots=True)
class DouyinSessionResult:
    cookie_file: Path
    media: dict[str, Any]


ChromiumCapture = Callable[
    [Path, str, Path, float],
    tuple[list[dict[str, Any]], dict[str, Any]],
]


def resolve_chromium_browser_path(
    candidates: Iterable[Path] | None = None,
) -> Path:
    if candidates is None:
        home = Path.home()
        if sys.platform == "darwin":
            candidates = (
                Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                home / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
                home / "Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            )
        else:
            candidates = ()
    for candidate in candidates:
        expanded = Path(candidate).expanduser()
        if expanded.is_file():
            return expanded.resolve()
    raise RuntimeError("未找到 Google Chrome，请先安装 Chrome 后重试。")


def build_chromium_arguments(*, profile_dir: Path, port: int, url: str) -> list[str]:
    return [
        f"--user-data-dir={profile_dir}",
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-sync",
        "--disable-background-mode",
        "--disable-component-update",
        "--disable-features=Translate,MediaRouter",
        "--headless=new",
        url,
    ]


def _text_values(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            result.append(item.strip())
    return result


def _integer(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def convert_douyin_detail_response(
    payload: dict[str, Any],
    *,
    page_url: str,
    page_title: str = "",
    user_agent: str = "",
) -> dict[str, Any]:
    detail = payload.get("aweme_detail")
    if not isinstance(detail, dict):
        raise RuntimeError("抖音返回的作品详情中没有视频信息。")
    video = detail.get("video")
    if not isinstance(video, dict):
        raise RuntimeError("抖音返回的作品详情中没有视频信息。")

    options: list[dict[str, Any]] = []
    rates = video.get("bit_rate")
    if isinstance(rates, list):
        for rate in rates:
            if not isinstance(rate, dict):
                continue
            play_addr = rate.get("play_addr")
            if not isinstance(play_addr, dict):
                continue
            urls = _text_values(play_addr.get("url_list"))
            if not urls:
                continue
            options.append(
                {
                    "Url": urls[0],
                    "BackupUrls": urls,
                    "Width": _integer(play_addr.get("width")),
                    "Height": _integer(play_addr.get("height")),
                    "BitRate": _integer(rate.get("bit_rate")),
                    "IsH265": _integer(rate.get("is_h265")) == 1,
                    "GearName": str(rate.get("gear_name") or ""),
                }
            )
    if not options:
        raise RuntimeError("抖音返回的作品详情中没有可下载的视频地址。")

    options.sort(
        key=lambda option: (
            -(_integer(option.get("Width")) * _integer(option.get("Height"))),
            bool(option.get("IsH265")),
            -_integer(option.get("BitRate")),
        )
    )
    videos: list[str] = []
    seen: set[str] = set()
    for option in options:
        for source in _text_values(option.get("BackupUrls")):
            normalized = source.casefold()
            if normalized not in seen:
                seen.add(normalized)
                videos.append(source)

    images: list[str] = []
    for cover_name in ("origin_cover", "cover"):
        cover = video.get(cover_name)
        if isinstance(cover, dict):
            images = _text_values(cover.get("url_list"))[:1]
        if images:
            break

    description = str(detail.get("desc") or "").strip()
    author = detail.get("author")
    author_name = str(author.get("nickname") or "").strip() if isinstance(author, dict) else ""
    published_at = ""
    created = _integer(detail.get("create_time"))
    if created > 0:
        published_at = datetime.fromtimestamp(created).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    return {
        "Url": page_url,
        "Title": page_title.strip() or description,
        "Caption": description,
        "Author": author_name,
        "PublishedAt": published_at,
        "UserAgent": user_agent.strip(),
        "Kind": "Video",
        "Images": images,
        "Audio": [],
        "Videos": videos,
        "VideoOptions": options,
    }


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _read_local_json(url: str, timeout: float) -> Any:
    opener = build_opener(ProxyHandler({}))
    with opener.open(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _select_page_target(targets: Any) -> dict[str, Any] | None:
    if not isinstance(targets, list):
        return None
    pages = [
        target
        for target in targets
        if isinstance(target, dict)
        and target.get("type") == "page"
        and isinstance(target.get("webSocketDebuggerUrl"), str)
    ]
    return next(
        (target for target in pages if "douyin.com" in str(target.get("url", ""))),
        pages[0] if pages else None,
    )


def _cdp_send(connection: Any, identifier: int, method: str, params: dict[str, Any] | None = None) -> None:
    connection.send(json.dumps({"id": identifier, "method": method, "params": params or {}}))


def _cdp_wait_for_id(connection: Any, identifier: int, deadline: float) -> dict[str, Any]:
    while time.monotonic() < deadline:
        try:
            message = json.loads(connection.recv())
        except Exception as exc:
            if isinstance(exc, TimeoutError) or exc.__class__.__name__ == "WebSocketTimeoutException":
                continue
            raise
        if message.get("id") != identifier:
            continue
        error = message.get("error")
        if error:
            raise RuntimeError(str(error.get("message") or error))
        result = message.get("result")
        return result if isinstance(result, dict) else {}
    raise TimeoutError("等待 Chrome 调试接口响应超时。")


def _stop_browser_process(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name != "nt":
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        try:
            if os.name != "nt":
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            else:
                process.kill()
            process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _capture_chromium_session(
    browser: Path,
    url: str,
    profile_dir: Path,
    timeout: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        import websocket
    except ImportError as exc:
        raise RuntimeError("Chrome 读取组件未包含在安装包中。") from exc

    port = _free_local_port()
    profile_dir.mkdir(parents=True, exist_ok=True)
    command = [str(browser), *build_chromium_arguments(profile_dir=profile_dir, port=port, url=url)]
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=os.name != "nt",
        creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
    )
    connection = None
    deadline = time.monotonic() + timeout
    try:
        target = None
        while time.monotonic() < deadline and process.poll() is None:
            try:
                target = _select_page_target(
                    _read_local_json(f"http://127.0.0.1:{port}/json/list", 1.0)
                )
            except (OSError, ValueError):
                target = None
            if target is not None:
                break
            time.sleep(0.15)
        if target is None:
            raise RuntimeError("未能连接 Chrome 独立读取会话。")

        connection = websocket.create_connection(
            target["webSocketDebuggerUrl"],
            timeout=1.0,
            origin=f"http://127.0.0.1:{port}",
        )
        _cdp_send(connection, 1, "Network.enable")
        _cdp_wait_for_id(connection, 1, deadline)
        _cdp_send(connection, 2, "Page.enable")
        _cdp_wait_for_id(connection, 2, deadline)
        _cdp_send(connection, 3, "Page.reload", {"ignoreCache": True})
        _cdp_wait_for_id(connection, 3, deadline)

        request_id = ""
        loaded = False
        while time.monotonic() < deadline and not loaded:
            try:
                message = json.loads(connection.recv())
            except Exception as exc:
                if isinstance(exc, TimeoutError) or exc.__class__.__name__ == "WebSocketTimeoutException":
                    continue
                raise
            method = message.get("method")
            params = message.get("params") if isinstance(message.get("params"), dict) else {}
            if method == "Network.responseReceived":
                response = params.get("response") if isinstance(params.get("response"), dict) else {}
                response_url = str(response.get("url") or "")
                if "/aweme/v1/web/aweme/detail/" in response_url:
                    request_id = str(params.get("requestId") or "")
            if method == "Network.loadingFinished" and request_id and params.get("requestId") == request_id:
                loaded = True
        if not request_id or not loaded:
            raise TimeoutError("等待抖音公开作品详情超时。")

        _cdp_send(connection, 4, "Network.getResponseBody", {"requestId": request_id})
        body_result = _cdp_wait_for_id(connection, 4, deadline)
        body = str(body_result.get("body") or "")
        if body_result.get("base64Encoded"):
            body = base64.b64decode(body).decode("utf-8")
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise RuntimeError("抖音公开作品详情格式无效。")

        expression = "JSON.stringify({url:location.href,title:document.title,userAgent:navigator.userAgent})"
        _cdp_send(
            connection,
            5,
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
        )
        page_result = _cdp_wait_for_id(connection, 5, deadline)
        page_value = page_result.get("result") if isinstance(page_result.get("result"), dict) else {}
        page_data_text = str(page_value.get("value") or "{}")
        page_data = json.loads(page_data_text)
        if not isinstance(page_data, dict):
            page_data = {}
        media = convert_douyin_detail_response(
            payload,
            page_url=str(page_data.get("url") or url),
            page_title=str(page_data.get("title") or ""),
            user_agent=str(page_data.get("userAgent") or ""),
        )

        _cdp_send(connection, 6, "Storage.getCookies")
        cookie_result = _cdp_wait_for_id(connection, 6, deadline)
        cookies = cookie_result.get("cookies")
        if not isinstance(cookies, list):
            raise RuntimeError("无法从 Chrome 独立会话读取抖音访问凭据。")
        return [cookie for cookie in cookies if isinstance(cookie, dict)], media
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
        _stop_browser_process(process)


def _write_netscape_cookies(cookies: list[dict[str, Any]], path: Path) -> None:
    lines = [
        "# Netscape HTTP Cookie File",
        "# Generated by 85数字多媒体下载助手 from an isolated Chrome session.",
    ]
    for cookie in cookies:
        domain = str(cookie.get("domain") or "").strip()
        normalized = domain.lstrip(".").casefold()
        if not (
            normalized == "douyin.com"
            or normalized.endswith(".douyin.com")
            or normalized == "iesdouyin.com"
            or normalized.endswith(".iesdouyin.com")
        ):
            continue
        name = str(cookie.get("name") or "").replace("\t", "").replace("\n", "")
        value = str(cookie.get("value") or "").replace("\t", "").replace("\n", "")
        if not name or not value:
            continue
        cookie_path = str(cookie.get("path") or "/")
        include_subdomains = "TRUE" if domain.startswith(".") else "FALSE"
        secure = "TRUE" if cookie.get("secure") else "FALSE"
        expires = max(0, _integer(cookie.get("expires")))
        lines.append(
            "\t".join((domain, include_subdomains, cookie_path, secure, str(expires), name, value))
        )
    if len(lines) <= 2:
        raise RuntimeError("Chrome 独立会话中未找到可用的抖音访问凭据。")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class ChromiumDouyinSessionRefresher:
    """Refresh public Douyin metadata with an isolated Chromium profile."""

    def __init__(
        self,
        *,
        session_root: Path,
        browser_candidates: Iterable[Path] | None = None,
        capture: ChromiumCapture | None = None,
        timeout: float = 70.0,
    ) -> None:
        self.session_root = session_root
        self.browser_candidates = tuple(browser_candidates) if browser_candidates is not None else None
        self.capture = capture or _capture_chromium_session
        self.timeout = timeout

    async def refresh(self, url: str) -> DouyinSessionResult:
        browser = resolve_chromium_browser_path(self.browser_candidates)
        self.session_root.mkdir(parents=True, exist_ok=True)
        attempts_root = self.session_root / "attempts"
        attempts_root.mkdir(parents=True, exist_ok=True)
        cookie_file = self.session_root / "douyin-cookies.txt"
        media_file = self.session_root / "douyin-media.json"
        last_error: Exception | None = None
        for _ in range(2):
            attempt_root = attempts_root / uuid4().hex
            profile_dir = attempt_root / "chrome-profile"
            try:
                cookies, media = await asyncio.to_thread(
                    self.capture,
                    browser,
                    url,
                    profile_dir,
                    self.timeout,
                )
                if not isinstance(media, dict) or not media.get("Videos"):
                    raise RuntimeError("Chrome 独立会话没有读取到可用的抖音视频地址。")
                _write_netscape_cookies(cookies, cookie_file)
                media_file.write_text(
                    json.dumps(media, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                return DouyinSessionResult(cookie_file=cookie_file, media=media)
            except Exception as exc:
                last_error = exc
            finally:
                shutil.rmtree(attempt_root, ignore_errors=True)
        if isinstance(last_error, TimeoutError):
            raise last_error
        if isinstance(last_error, RuntimeError):
            raise last_error
        raise RuntimeError("未能生成有效的抖音临时访问会话。") from last_error


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
