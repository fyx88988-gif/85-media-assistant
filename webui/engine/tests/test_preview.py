from media_assistant.models import (
    MediaFormat,
    MediaKind,
    Platform,
    WorkItem,
    WorkStatus,
)
from media_assistant.preview import HttpPreviewStreamer, PreviewResolver


def make_item(
    *,
    width: int | None = 1920,
    height: int | None = 1080,
    rotation: int = 0,
    preview_url: str | None = "https://cdn.test/video.mp4",
    thumbnail_url: str | None = "https://cdn.test/cover.jpg",
) -> WorkItem:
    formats = []
    if preview_url is not None:
        formats.append(
            MediaFormat(
                format_id="18",
                extension="mp4",
                width=width,
                height=height,
                video_codec="h264",
                audio_codec="aac",
                has_video=True,
                has_audio=True,
                preview_url=preview_url,
            )
        )
    return WorkItem(
        source_url="https://example.com/video/85",
        platform=Platform.OTHER,
        status=WorkStatus.READY,
        title="预览测试",
        thumbnail_url=thumbnail_url,
        media_kind=MediaKind.VIDEO,
        width=width,
        height=height,
        rotation=rotation,
        formats=formats,
    )


def test_preview_reports_effective_portrait_ratio_after_rotation() -> None:
    item = make_item(rotation=90)

    result = PreviewResolver().resolve(item)

    assert result.mode == "direct"
    assert (result.width, result.height) == (1080, 1920)
    assert result.url == f"/api/v1/items/{item.id}/media?format_id=18"
    assert "cdn.test" not in result.url


def test_preview_without_safe_stream_is_explicit_cover_only() -> None:
    result = PreviewResolver().resolve(make_item(preview_url=None))

    assert result.mode == "cover"
    assert result.poster == "https://cdn.test/cover.jpg"
    assert result.reason == "当前作品没有可安全播放的预览地址。"


def test_preview_rejects_non_https_stream() -> None:
    result = PreviewResolver().resolve(
        make_item(preview_url="http://cdn.test/video.mp4")
    )

    assert result.mode == "cover"
    assert result.url is None


def test_preview_does_not_expose_sensitive_fields() -> None:
    dumped = PreviewResolver().resolve(make_item()).model_dump()

    assert "headers" not in dumped
    assert "cookies" not in dumped
    assert "path" not in dumped


def test_http_preview_streamer_forwards_range_and_extractor_headers() -> None:
    captured: dict[str, str] = {}

    def upstream(request: httpx.Request) -> httpx.Response:
        captured.update(request.headers)
        return httpx.Response(
            206,
            stream=httpx.ByteStream(b"85"),
            headers={
                "Content-Type": "video/mp4",
                "Content-Range": "bytes 0-1/2",
                "Accept-Ranges": "bytes",
            },
        )

    media_format = MediaFormat(
        format_id="18",
        extension="mp4",
        video_codec="h264",
        audio_codec="aac",
        has_video=True,
        has_audio=True,
        preview_url="https://cdn.test/video.mp4",
        request_headers={
            "User-Agent": "85-test-agent",
            "Referer": "https://source.test/watch/85",
        },
    )

    async def scenario() -> tuple[int, bytes, str | None]:
        streamer = HttpPreviewStreamer(transport=httpx.MockTransport(upstream))
        response = await streamer.response(media_format, "bytes=0-1")
        body = b"".join([chunk async for chunk in response.body_iterator])
        return response.status_code, body, response.headers.get("content-range")

    status_code, body, content_range = asyncio.run(scenario())

    assert status_code == 206
    assert body == b"85"
    assert content_range == "bytes 0-1/2"
    assert captured["range"] == "bytes=0-1"
    assert captured["user-agent"] == "85-test-agent"
    assert captured["referer"] == "https://source.test/watch/85"
import asyncio

import httpx
