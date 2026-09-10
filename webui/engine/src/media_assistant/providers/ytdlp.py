import json
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Protocol

from ..input_links import detect_platform
from ..models import (
    MediaFormat,
    MediaKind,
    RecognitionFailure,
    WorkItem,
    WorkStatus,
)
from ..processes import ProcessRunner


class SessionRefresh(Protocol):
    cookie_file: Path
    media: dict[str, Any]


class SessionRefresher(Protocol):
    async def refresh(self, url: str) -> SessionRefresh: ...


class YtDlpProvider:
    def __init__(
        self,
        binary: Path,
        runner: ProcessRunner,
        timeout: float = 45.0,
        session_refresher: SessionRefresher | None = None,
    ) -> None:
        self.binary = binary
        self.runner = runner
        self.timeout = timeout
        self.session_refresher = session_refresher

    def can_handle(self, url: str) -> bool:
        return url.startswith(("https://", "http://"))

    async def recognize(self, url: str) -> WorkItem:
        argv = [
            str(self.binary),
            "--dump-single-json",
            "--no-playlist",
            "--no-warnings",
            "--skip-download",
            url,
        ]
        try:
            result = await self.runner.run(argv, self.timeout)
        except TimeoutError:
            return self._failure(url, "timeout", "读取超时，请检查网络后重试。")
        except OSError:
            return self._failure(url, "engine_unavailable", "下载引擎暂时不可用。")

        if (
            result.returncode != 0
            and detect_platform(url).value == "douyin"
            and "fresh cookies" in result.stderr.lower()
            and self.session_refresher is not None
        ):
            try:
                refreshed = await self.session_refresher.refresh(url)
                return self._map_douyin_browser_media(url, refreshed.media)
            except TimeoutError:
                return self._failure(
                    url,
                    "session_refresh_timeout",
                    "抖音页面读取超时，已自动重试仍未成功。",
                )
            except (OSError, RuntimeError):
                return self._failure(
                    url,
                    "session_refresh_failed",
                    "抖音临时访问会话更新失败，请稍后重试。",
                )

        if result.returncode != 0:
            return self._process_failure(url, result.stderr)

        try:
            payload = json.loads(result.stdout)
            if not isinstance(payload, dict):
                raise TypeError("recognition payload is not an object")
            return self._map_item(url, payload)
        except (JSONDecodeError, TypeError, ValueError, KeyError):
            return self._failure(url, "invalid_response", "平台返回了无法识别的数据，请稍后重试。")

    def _map_douyin_browser_media(self, url: str, payload: dict[str, Any]) -> WorkItem:
        canonical_url = _clean_text(payload.get("Url")) or url
        request_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36"
            ),
            "Referer": canonical_url,
        }
        formats: list[MediaFormat] = []
        options = payload.get("VideoOptions")
        if isinstance(options, list):
            for index, option in enumerate(options):
                if not isinstance(option, dict):
                    continue
                sources = option.get("BackupUrls")
                direct_url = _first_text(sources) or _clean_text(option.get("Url"))
                if not direct_url:
                    continue
                is_h265 = bool(option.get("IsH265"))
                formats.append(
                    MediaFormat(
                        format_id=f"douyin-browser-{index}",
                        note=_clean_text(option.get("GearName")) or "浏览器原始媒体",
                        extension="mp4",
                        width=_positive_int(option.get("Width")),
                        height=_positive_int(option.get("Height")),
                        video_codec="h265" if is_h265 else "h264",
                        audio_codec="aac",
                        has_video=True,
                        has_audio=True,
                        preview_url=direct_url,
                        request_headers=request_headers,
                    )
                )
        if not formats:
            for index, direct_url in enumerate(_text_values(payload.get("Videos"))):
                formats.append(
                    MediaFormat(
                        format_id=f"douyin-browser-{index}",
                        note="浏览器原始媒体",
                        extension="mp4",
                        has_video=True,
                        has_audio=True,
                        preview_url=direct_url,
                        request_headers=request_headers,
                    )
                )
        if not formats:
            raise RuntimeError("独立浏览器没有读取到可用的抖音视频地址。")
        primary = formats[0]
        images = _text_values(payload.get("Images"))
        return WorkItem(
            source_url=url,
            platform=detect_platform(url),
            status=WorkStatus.READY,
            title=_clean_text(payload.get("Title")) or "抖音公开作品",
            author=_clean_text(payload.get("Author")),
            description=_clean_text(payload.get("Caption")),
            thumbnail_url=images[0] if images else None,
            media_kind=MediaKind.VIDEO,
            width=primary.width,
            height=primary.height,
            formats=formats,
        )

    def _map_item(self, url: str, payload: dict[str, Any]) -> WorkItem:
        formats = [self._map_format(value) for value in payload.get("formats", []) if isinstance(value, dict)]
        media_kind = MediaKind.VIDEO if any(value.has_video for value in formats) else MediaKind.AUDIO
        width = _positive_int(payload.get("width"))
        height = _positive_int(payload.get("height"))
        if width is None or height is None:
            visual = next((value for value in formats if value.has_video), None)
            if visual is not None:
                width = width or visual.width
                height = height or visual.height
        return WorkItem(
            source_url=url,
            platform=detect_platform(url),
            status=WorkStatus.READY,
            title=_clean_text(payload.get("title")) or "未命名作品",
            author=_clean_text(payload.get("uploader") or payload.get("channel")),
            description=_clean_text(payload.get("description")),
            thumbnail_url=_clean_text(payload.get("thumbnail")),
            media_kind=media_kind,
            width=width,
            height=height,
            duration=_positive_float(payload.get("duration")),
            formats=formats,
        )

    def _map_format(self, payload: dict[str, Any]) -> MediaFormat:
        video_codec = _clean_text(payload.get("vcodec"))
        audio_codec = _clean_text(payload.get("acodec"))
        has_video = bool(video_codec and video_codec != "none")
        has_audio = bool(audio_codec and audio_codec != "none")
        preview_url = _clean_text(payload.get("url")) if has_video and has_audio else None
        request_headers = {
            str(name): str(value)
            for name, value in (payload.get("http_headers") or {}).items()
            if isinstance(name, str) and isinstance(value, str)
        }
        return MediaFormat(
            format_id=str(payload.get("format_id") or "unknown"),
            note=_clean_text(payload.get("format_note")),
            extension=_clean_text(payload.get("ext")),
            width=_positive_int(payload.get("width")),
            height=_positive_int(payload.get("height")),
            fps=_positive_float(payload.get("fps")),
            video_codec=video_codec,
            audio_codec=audio_codec,
            file_size=_positive_int(payload.get("filesize") or payload.get("filesize_approx")),
            has_video=has_video,
            has_audio=has_audio,
            preview_url=preview_url,
            request_headers=request_headers,
        )

    def _process_failure(self, url: str, stderr: str) -> WorkItem:
        lowered = stderr.lower()
        if any(marker in lowered for marker in ("not a bot", "captcha", "verify", "verification")):
            return self._failure(url, "verification_required", "平台要求完成安全验证后才能读取该作品。")
        if any(marker in lowered for marker in ("login required", "authentication required", "sign in required")):
            return self._failure(url, "login_required", "该作品仅允许登录用户读取。")
        if "fresh cookies" in lowered:
            return self._failure(url, "session_expired", "平台临时访问会话已失效，请重新识别。")
        if any(marker in lowered for marker in ("unsupported url", "no suitable extractor")):
            return self._failure(url, "unsupported", "当前下载引擎暂不支持该链接。", retryable=False)
        if any(marker in lowered for marker in ("network", "timed out", "connection", "dns")):
            return self._failure(url, "network_unavailable", "当前网络无法访问该平台，请检查网络后重试。")
        return self._failure(url, "recognition_failed", "作品识别失败，请重新尝试。")

    def _failure(
        self,
        url: str,
        code: str,
        message: str,
        retryable: bool = True,
    ) -> WorkItem:
        return WorkItem(
            source_url=url,
            platform=detect_platform(url),
            status=WorkStatus.FAILED,
            title="识别失败",
            failure=RecognitionFailure(code=code, message=message, retryable=retryable),
        )


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _text_values(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [cleaned for item in value if (cleaned := _clean_text(item))]


def _first_text(value: Any) -> str | None:
    values = _text_values(value)
    return values[0] if values else None


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
