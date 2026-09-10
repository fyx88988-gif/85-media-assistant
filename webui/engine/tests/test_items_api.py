import asyncio
import json
import time
from pathlib import Path

from fastapi.testclient import TestClient
from fastapi.responses import Response

from media_assistant.app import create_app
from media_assistant.config import AppConfig
from media_assistant.events import EventBus
from media_assistant.items import ItemService
from media_assistant.models import (
    MediaFormat,
    MediaKind,
    Platform,
    WorkItem,
    WorkStatus,
)


class SelectiveRecognizer:
    async def recognize(self, url: str) -> WorkItem:
        await asyncio.sleep(0)
        if url.endswith("/2"):
            return WorkItem(
                source_url=url,
                platform=Platform.OTHER,
                status=WorkStatus.FAILED,
                title="识别失败",
            )
        return WorkItem(
            source_url=url,
            platform=Platform.OTHER,
            status=WorkStatus.READY,
            title=f"作品 {url.rsplit('/', 1)[-1]}",
            media_kind=MediaKind.VIDEO,
            width=1920,
            height=1080,
            formats=[
                MediaFormat(
                    format_id="18",
                    extension="mp4",
                    width=1920,
                    height=1080,
                    video_codec="h264",
                    audio_codec="aac",
                    has_video=True,
                    has_audio=True,
                    preview_url="https://cdn.example.com/video.mp4",
                )
            ],
        )


class FakePreviewStreamer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    async def response(self, media_format: MediaFormat, range_header: str | None) -> Response:
        self.calls.append((media_format.format_id, range_header))
        return Response(
            content=b"85",
            status_code=206,
            media_type="video/mp4",
            headers={"Content-Range": "bytes 0-1/2", "Accept-Ranges": "bytes"},
        )


def build_client(
    tmp_path: Path,
    *,
    preview_streamer: FakePreviewStreamer | None = None,
) -> TestClient:
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("85", encoding="utf-8")
    events = EventBus()
    items = ItemService(SelectiveRecognizer(), events)
    return TestClient(
        create_app(
            AppConfig("127.0.0.1", 8585, "test-token", static_dir),
            item_service=items,
            event_bus=events,
            preview_streamer=preview_streamer,
        )
    )


def test_recognition_queue_preserves_input_order_and_failure_isolation(tmp_path: Path) -> None:
    with build_client(tmp_path) as client:
        response = client.post(
            "/api/v1/items/recognize",
            headers={"X-85-Session": "test-token"},
            json={"urls": ["https://example.com/1", "https://example.com/2"]},
        )
        assert response.status_code == 202
        ids = [item["id"] for item in response.json()["items"]]

        deadline = time.monotonic() + 1
        snapshot = []
        while time.monotonic() < deadline:
            snapshot = client.get("/api/v1/items").json()["items"]
            if all(item["status"] in {"ready", "failed"} for item in snapshot):
                break
            time.sleep(0.01)

        assert [item["id"] for item in snapshot] == ids
        assert [item["status"] for item in snapshot] == ["ready", "failed"]


def test_deleting_one_item_keeps_its_sibling(tmp_path: Path) -> None:
    with build_client(tmp_path) as client:
        created = client.post(
            "/api/v1/items/recognize",
            headers={"X-85-Session": "test-token"},
            json={"urls": ["https://example.com/1", "https://example.com/2"]},
        ).json()["items"]

        response = client.delete(
            f"/api/v1/items/{created[0]['id']}",
            headers={"X-85-Session": "test-token"},
        )

        assert response.status_code == 204
        remaining = client.get("/api/v1/items").json()["items"]
        assert [item["id"] for item in remaining] == [created[1]["id"]]


def test_event_bus_replays_only_events_after_requested_id() -> None:
    async def scenario() -> list[dict[str, object]]:
        bus = EventBus(max_events=10)
        first = await bus.publish("item.created", {"id": "one"})
        await bus.publish("item.updated", {"id": "one", "status": "ready"})
        return [event.as_dict() for event in bus.events_after(first.id)]

    assert asyncio.run(scenario()) == [
        {
            "id": 2,
            "kind": "item.updated",
            "payload": {"id": "one", "status": "ready"},
        }
    ]


def test_item_preview_endpoint_returns_adaptive_dimensions(tmp_path: Path) -> None:
    with build_client(tmp_path) as client:
        created = client.post(
            "/api/v1/items/recognize",
            headers={"X-85-Session": "test-token"},
            json={"urls": ["https://example.com/1"]},
        ).json()["items"][0]

        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            response = client.get(f"/api/v1/items/{created['id']}/preview")
            if response.status_code == 200:
                break
            time.sleep(0.01)

        assert response.status_code == 200
        assert response.json()["mode"] == "direct"
        assert response.json()["width"] == 1920
        assert response.json()["height"] == 1080
        assert response.json()["url"].startswith(
            f"/api/v1/items/{created['id']}/media?format_id="
        )


def test_item_media_endpoint_proxies_browser_range_request(tmp_path: Path) -> None:
    streamer = FakePreviewStreamer()
    with build_client(tmp_path, preview_streamer=streamer) as client:
        created = client.post(
            "/api/v1/items/recognize",
            headers={"X-85-Session": "test-token"},
            json={"urls": ["https://example.com/1"]},
        ).json()["items"][0]

        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            item = client.get("/api/v1/items").json()["items"][0]
            if item["status"] == "ready":
                break
            time.sleep(0.01)

        response = client.get(
            f"/api/v1/items/{created['id']}/media?format_id=18",
            headers={"Range": "bytes=0-1"},
        )

        assert response.status_code == 206
        assert response.content == b"85"
        assert response.headers["content-range"] == "bytes 0-1/2"
        assert streamer.calls == [("18", "bytes=0-1")]
