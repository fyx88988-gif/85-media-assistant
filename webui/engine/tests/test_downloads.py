import asyncio
import time
from pathlib import Path
from typing import Sequence

from fastapi.testclient import TestClient

from media_assistant.app import create_app
from media_assistant.config import AppConfig
from media_assistant.downloads import (
    DownloadRequest,
    DownloadService,
    DownloadStatus,
    resolve_output_dir,
)
from media_assistant.events import EventBus
from media_assistant.items import ItemService
from media_assistant.models import MediaFormat, MediaKind, Platform, WorkItem, WorkStatus
from media_assistant.processes import ProcessResult
from media_assistant.verification import VerifiedOutput


def test_relative_download_location_uses_current_users_downloads_folder(tmp_path: Path) -> None:
    assert resolve_output_dir(Path("Downloads"), home=tmp_path) == tmp_path / "Downloads"
    explicit = tmp_path / "指定位置"
    assert resolve_output_dir(explicit, home=tmp_path) == explicit


class ControllableProcess:
    def __init__(self, result: ProcessResult) -> None:
        self.result = result
        self.release = asyncio.Event()
        self.terminated = False

    async def wait(self) -> ProcessResult:
        await self.release.wait()
        return self.result

    async def terminate_tree(self) -> None:
        self.terminated = True
        self.release.set()


class ControllableRunner:
    def __init__(self, result: ProcessResult | None = None) -> None:
        self.argv: list[str] = []
        self.process = ControllableProcess(result or ProcessResult(0, "", ""))

    async def start(self, argv: Sequence[str]) -> ControllableProcess:
        self.argv = list(argv)
        return self.process


class FakeVerifier:
    def __init__(self, *, valid: bool) -> None:
        self.valid = valid

    async def verify(self, path: Path) -> VerifiedOutput:
        return VerifiedOutput(
            path=path,
            valid=self.valid,
            size=1024 if self.valid else 0,
            width=1920 if self.valid else None,
            height=1080 if self.valid else None,
        )


def make_request(tmp_path: Path) -> DownloadRequest:
    return DownloadRequest(
        source_url="https://example.com/video/85",
        format_id="137",
        output_dir=tmp_path,
        title="测试视频",
    )


def test_download_uses_selected_format_and_cancels_process(tmp_path: Path) -> None:
    async def scenario() -> None:
        runner = ControllableRunner()
        service = DownloadService(
            binary=Path("tools") / "yt-dlp",
            runner=runner,
            verifier=FakeVerifier(valid=True),
        )

        job = await service.start(make_request(tmp_path))
        assert "137+bestaudio/best" in runner.argv
        assert job.status == DownloadStatus.DOWNLOADING

        cancelled = await service.cancel(job.id)
        assert cancelled.status == DownloadStatus.CANCELLED
        assert runner.process.terminated is True

    asyncio.run(scenario())


def test_download_reuses_isolated_douyin_cookie_file(tmp_path: Path) -> None:
    async def scenario() -> None:
        cookie_file = tmp_path / "douyin.cookies.txt"
        cookie_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
        runner = ControllableRunner()
        service = DownloadService(
            binary=Path("yt-dlp"),
            runner=runner,
            verifier=FakeVerifier(valid=True),
            cookie_file=cookie_file,
        )
        request = DownloadRequest(
            source_url="https://v.douyin.com/example/",
            format_id="137",
            output_dir=tmp_path,
            title="抖音测试视频",
        )

        await service.start(request)

        cookie_index = runner.argv.index("--cookies")
        assert runner.argv[cookie_index + 1] == str(cookie_file)
        await service.cancel(next(iter(service._jobs)))

    asyncio.run(scenario())


def test_download_with_invalid_output_is_failed(tmp_path: Path) -> None:
    async def scenario() -> None:
        runner = ControllableRunner()
        service = DownloadService(
            binary=Path("yt-dlp"),
            runner=runner,
            verifier=FakeVerifier(valid=False),
        )

        job = await service.start(make_request(tmp_path))
        runner.process.release.set()
        completed = await service.wait(job.id)

        assert completed.status == DownloadStatus.FAILED
        assert completed.failure == "下载结果校验失败，没有生成有效媒体文件。"

    asyncio.run(scenario())


def test_download_success_requires_verified_output(tmp_path: Path) -> None:
    async def scenario() -> None:
        runner = ControllableRunner()
        service = DownloadService(
            binary=Path("yt-dlp"),
            runner=runner,
            verifier=FakeVerifier(valid=True),
        )

        job = await service.start(make_request(tmp_path))
        runner.process.release.set()
        completed = await service.wait(job.id)

        assert completed.status == DownloadStatus.COMPLETED
        assert completed.output is not None
        assert completed.output.valid is True

    asyncio.run(scenario())


class ReadyRecognizer:
    async def recognize(self, url: str) -> WorkItem:
        return WorkItem(
            source_url=url,
            platform=Platform.DOUYIN,
            status=WorkStatus.READY,
            title="可下载作品",
            media_kind=MediaKind.VIDEO,
            formats=[
                MediaFormat(
                    format_id="douyin-browser-0",
                    extension="mp4",
                    video_codec="h264",
                    audio_codec="aac",
                    has_video=True,
                    has_audio=True,
                    preview_url="https://cdn.example/video.mp4",
                    request_headers={
                        "User-Agent": "85-test-agent",
                        "Referer": "https://example.com/video/85",
                    },
                )
            ],
        )


def test_download_api_starts_and_cancels_selected_item(tmp_path: Path) -> None:
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("85", encoding="utf-8")
    events = EventBus()
    items = ItemService(ReadyRecognizer(), events)
    runner = ControllableRunner()
    downloads = DownloadService(
        binary=Path("yt-dlp"),
        runner=runner,
        verifier=FakeVerifier(valid=True),
    )
    app = create_app(
        AppConfig("127.0.0.1", 8585, "test-token", static_dir),
        item_service=items,
        event_bus=events,
        download_service=downloads,
    )

    with TestClient(app) as client:
        item = client.post(
            "/api/v1/items/recognize",
            headers={"X-85-Session": "test-token"},
            json={"urls": ["https://example.com/video/85"]},
        ).json()["items"][0]
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            current = client.get("/api/v1/items").json()["items"][0]
            if current["status"] == "ready":
                break
            time.sleep(0.01)

        response = client.post(
            "/api/v1/downloads",
            headers={"X-85-Session": "test-token"},
            json={
                "item_id": item["id"],
                "format_id": "douyin-browser-0",
                "output_dir": str(tmp_path / "downloads"),
            },
        )
        assert response.status_code == 202
        job = response.json()
        assert job["status"] == "downloading"
        assert "https://cdn.example/video.mp4" in runner.argv
        assert "douyin-browser-0+bestaudio/best" not in runner.argv
        assert "User-Agent:85-test-agent" in runner.argv

        cancelled = client.post(
            f"/api/v1/downloads/{job['id']}/cancel",
            headers={"X-85-Session": "test-token"},
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
