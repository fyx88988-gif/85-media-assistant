from collections.abc import AsyncIterator
from pathlib import Path
from time import time
from typing import Literal, Protocol
from urllib.parse import urlencode, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict
from starlette.responses import Response, StreamingResponse

from .models import MediaFormat, WorkItem


class PreviewDescriptor(BaseModel):
    model_config = ConfigDict(frozen=True)

    mode: Literal["direct", "cover"]
    url: str | None = None
    poster: str | None = None
    width: int | None = None
    height: int | None = None
    rotation: int = 0
    reason: str | None = None


class PreviewResolver:
    _browser_containers = {"mp4", "webm", "mov", "m4v"}

    def resolve(self, item: WorkItem) -> PreviewDescriptor:
        width, height = _effective_size(item.width, item.height, item.rotation)
        playable = self.playable_format(item)
        if playable is not None:
            format_width, format_height = _effective_size(
                playable.width or item.width,
                playable.height or item.height,
                item.rotation,
            )
            return PreviewDescriptor(
                mode="direct",
                url=(
                    f"/api/v1/items/{item.id}/media?"
                    f"{urlencode({'format_id': playable.format_id})}"
                ),
                poster=item.thumbnail_url,
                width=format_width,
                height=format_height,
                rotation=item.rotation % 360,
            )
        return PreviewDescriptor(
            mode="cover",
            poster=item.thumbnail_url,
            width=width,
            height=height,
            rotation=item.rotation % 360,
            reason="当前作品没有可安全播放的预览地址。",
        )

    def playable_format(
        self,
        item: WorkItem,
        format_id: str | None = None,
    ) -> MediaFormat | None:
        return next(
            (
                value
                for value in item.formats
                if (format_id is None or value.format_id == format_id)
                and self._is_safe_browser_stream(value)
            ),
            None,
        )

    def _is_safe_browser_stream(self, value: MediaFormat) -> bool:
        if not value.preview_url or not value.has_video or not value.has_audio:
            return False
        if (value.extension or "").lower() not in self._browser_containers:
            return False
        parsed = urlsplit(value.preview_url)
        return parsed.scheme == "https" and bool(parsed.hostname)


class PreviewStreamer(Protocol):
    async def response(
        self,
        media_format: MediaFormat,
        range_header: str | None,
    ) -> Response: ...


class PreviewStreamError(RuntimeError):
    pass


class HttpPreviewStreamer:
    _blocked_request_headers = {
        "authorization",
        "connection",
        "content-length",
        "cookie",
        "host",
        "proxy-authorization",
        "range",
        "transfer-encoding",
    }
    _forward_response_headers = {
        "accept-ranges",
        "cache-control",
        "content-length",
        "content-range",
        "content-type",
        "etag",
        "last-modified",
    }

    def __init__(
        self,
        cookie_file: Path | None = None,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.cookie_file = cookie_file
        self.timeout = timeout
        self.transport = transport

    async def response(
        self,
        media_format: MediaFormat,
        range_header: str | None,
    ) -> Response:
        if not media_format.preview_url:
            raise PreviewStreamError("作品没有可用的媒体地址。")

        headers = {
            name: value
            for name, value in media_format.request_headers.items()
            if name.lower() not in self._blocked_request_headers
        }
        headers["Accept-Encoding"] = "identity"
        if range_header:
            headers["Range"] = range_header
        cookie_header = _cookie_header(self.cookie_file, media_format.preview_url)
        if cookie_header:
            headers["Cookie"] = cookie_header

        client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=self.timeout,
            transport=self.transport,
        )
        try:
            request = client.build_request("GET", media_format.preview_url, headers=headers)
            upstream = await client.send(request, stream=True)
            if upstream.status_code >= 400:
                raise PreviewStreamError(
                    f"平台媒体服务器返回状态 {upstream.status_code}。"
                )
        except (httpx.HTTPError, PreviewStreamError) as exc:
            await client.aclose()
            if isinstance(exc, PreviewStreamError):
                raise
            raise PreviewStreamError("无法连接平台媒体服务器。") from exc

        response_headers = {
            name: value
            for name, value in upstream.headers.items()
            if name.lower() in self._forward_response_headers
        }

        async def chunks() -> AsyncIterator[bytes]:
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()

        return StreamingResponse(
            chunks(),
            status_code=upstream.status_code,
            headers=response_headers,
            media_type=upstream.headers.get("content-type", "video/mp4"),
        )


def _cookie_header(cookie_file: Path | None, url: str) -> str | None:
    if cookie_file is None or not cookie_file.is_file():
        return None
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").lower()
    request_path = parsed.path or "/"
    secure_request = parsed.scheme == "https"
    pairs: list[str] = []
    try:
        lines = cookie_file.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return None
    for line in lines:
        if not line or (line.startswith("#") and not line.startswith("#HttpOnly_")):
            continue
        fields = line.removeprefix("#HttpOnly_").split("\t")
        if len(fields) != 7:
            continue
        domain, _, path, secure, expires, name, value = fields
        domain = domain.lstrip(".").lower()
        if hostname != domain and not hostname.endswith(f".{domain}"):
            continue
        if path and not request_path.startswith(path):
            continue
        if secure.upper() == "TRUE" and not secure_request:
            continue
        try:
            if int(expires) > 0 and int(expires) <= int(time()):
                continue
        except ValueError:
            pass
        pairs.append(f"{name}={value}")
    return "; ".join(pairs) or None


def _effective_size(
    width: int | None,
    height: int | None,
    rotation: int,
) -> tuple[int | None, int | None]:
    if rotation % 180 == 90:
        return height, width
    return width, height
