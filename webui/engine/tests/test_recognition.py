import asyncio
from pathlib import Path
from typing import Sequence

from media_assistant.models import MediaKind, WorkStatus
from media_assistant.processes import ProcessResult
from media_assistant.providers.ytdlp import YtDlpProvider


class FakeRunner:
    def __init__(self, *, stdout: str, stderr: str, returncode: int) -> None:
        self.result = ProcessResult(returncode, stdout, stderr)
        self.argv: list[str] = []
        self.timeout = 0.0

    async def run(self, argv: Sequence[str], timeout: float) -> ProcessResult:
        self.argv = list(argv)
        self.timeout = timeout
        return self.result


class SequencedRunner:
    def __init__(self, results: list[ProcessResult]) -> None:
        self.results = list(results)
        self.calls: list[list[str]] = []

    async def run(self, argv: Sequence[str], timeout: float) -> ProcessResult:
        self.calls.append(list(argv))
        return self.results.pop(0)


class FakeSessionRefresher:
    def __init__(self, cookie_file: Path, media: dict[str, object]) -> None:
        self.cookie_file = cookie_file
        self.media = media
        self.urls: list[str] = []

    async def refresh(self, url: str):
        self.urls.append(url)
        return type(
            "BrowserSession",
            (),
            {"cookie_file": self.cookie_file, "media": self.media},
        )()


class FailingSessionRefresher:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def refresh(self, url: str):
        raise self.error


def test_ytdlp_provider_maps_video_metadata() -> None:
    fixture = Path(__file__).parent / "fixtures" / "ytdlp_video.json"
    runner = FakeRunner(
        stdout=fixture.read_text(encoding="utf-8"),
        stderr="",
        returncode=0,
    )
    provider = YtDlpProvider(Path("tools") / "yt-dlp", runner)

    item = asyncio.run(provider.recognize("https://example.com/watch/85"))

    assert item.status == WorkStatus.READY
    assert item.title == "测试视频"
    assert (item.width, item.height) == (1080, 1920)
    assert item.media_kind == MediaKind.VIDEO
    assert item.formats[0].format_id == "137"
    assert item.formats[0].has_video is True
    assert item.formats[0].has_audio is False
    assert item.formats[1].request_headers == {
        "User-Agent": "85-test-agent",
        "Referer": "https://example.com/watch/85",
    }
    assert "request_headers" not in item.formats[1].model_dump()
    assert runner.argv[:3] == [
        str(Path("tools") / "yt-dlp"),
        "--dump-single-json",
        "--no-playlist",
    ]
    assert runner.argv[-1] == "https://example.com/watch/85"


def test_ytdlp_provider_maps_verification_failure_to_safe_message() -> None:
    runner = FakeRunner(
        stdout="",
        stderr="ERROR: Sign in to confirm you're not a bot",
        returncode=1,
    )
    provider = YtDlpProvider(Path("yt-dlp"), runner)

    item = asyncio.run(provider.recognize("https://example.com/watch/85"))

    assert item.status == WorkStatus.FAILED
    assert item.failure is not None
    assert item.failure.code == "verification_required"
    assert item.failure.message == "平台要求完成安全验证后才能读取该作品。"
    assert "Sign in" not in item.failure.message


def test_ytdlp_provider_rejects_invalid_json_without_exposing_payload() -> None:
    runner = FakeRunner(stdout="<html>blocked</html>", stderr="", returncode=0)
    provider = YtDlpProvider(Path("yt-dlp"), runner)

    item = asyncio.run(provider.recognize("https://example.com/watch/85"))

    assert item.status == WorkStatus.FAILED
    assert item.failure is not None
    assert item.failure.code == "invalid_response"
    assert "<html>" not in item.failure.message


def test_douyin_fresh_cookie_failure_uses_media_already_read_by_isolated_browser(tmp_path: Path) -> None:
    runner = SequencedRunner([
        ProcessResult(1, "", "ERROR: Fresh cookies (not necessarily logged in) are needed"),
    ])
    cookie_file = tmp_path / "douyin.cookies.txt"
    refresher = FakeSessionRefresher(
        cookie_file,
        {
            "Url": "https://www.douyin.com/video/7679752019771103375",
            "Title": "浏览器读取到的公开作品",
            "Caption": "公开作品说明",
            "Author": "公开作者",
            "Kind": "Video",
            "Images": ["https://cdn.example/cover.jpg"],
            "Videos": ["https://cdn.example/video.mp4"],
        },
    )
    provider = YtDlpProvider(Path("yt-dlp"), runner, session_refresher=refresher)

    item = asyncio.run(provider.recognize("https://v.douyin.com/example/"))

    assert item.status == WorkStatus.READY
    assert item.title == "浏览器读取到的公开作品"
    assert item.description == "公开作品说明"
    assert item.thumbnail_url == "https://cdn.example/cover.jpg"
    assert item.formats[0].preview_url == "https://cdn.example/video.mp4"
    assert refresher.urls == ["https://v.douyin.com/example/"]
    assert len(runner.calls) == 1


def test_douyin_session_timeout_has_a_specific_retry_message() -> None:
    runner = FakeRunner(
        stdout="",
        stderr="ERROR: Fresh cookies (not necessarily logged in) are needed",
        returncode=1,
    )
    provider = YtDlpProvider(
        Path("yt-dlp"),
        runner,
        session_refresher=FailingSessionRefresher(TimeoutError()),
    )

    item = asyncio.run(provider.recognize("https://v.douyin.com/example/"))

    assert item.failure is not None
    assert item.failure.code == "session_refresh_timeout"
    assert item.failure.message == "抖音页面读取超时，已自动重试仍未成功。"


def test_generic_cookie_advice_is_not_misreported_as_login_required() -> None:
    runner = FakeRunner(
        stdout="",
        stderr="ERROR: request blocked; try passing cookies for more information",
        returncode=1,
    )
    provider = YtDlpProvider(Path("yt-dlp"), runner)

    item = asyncio.run(provider.recognize("https://example.com/watch/85"))

    assert item.failure is not None
    assert item.failure.code == "recognition_failed"
    assert "登录" not in item.failure.message
