import asyncio
from collections import OrderedDict
from collections.abc import Iterable
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl

from .events import EventBus
from .input_links import detect_platform
from .models import MediaKind, WorkItem, WorkStatus


class Recognizer(Protocol):
    async def recognize(self, url: str) -> WorkItem: ...


class RecognizeItemsRequest(BaseModel):
    urls: list[HttpUrl] = Field(min_length=1, max_length=100)


class ItemsResponse(BaseModel):
    items: list[WorkItem]


class ItemService:
    def __init__(self, recognizer: Recognizer, events: EventBus) -> None:
        self._recognizer = recognizer
        self._events = events
        self._items: OrderedDict[UUID, WorkItem] = OrderedDict()
        self._queue: asyncio.Queue[UUID | None] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._run(), name="recognition-worker")

    async def stop(self) -> None:
        if self._worker is None:
            return
        await self._queue.put(None)
        await self._worker
        self._worker = None

    async def enqueue(self, urls: Iterable[str]) -> list[WorkItem]:
        created: list[WorkItem] = []
        for url in urls:
            item = WorkItem(
                source_url=url,
                platform=detect_platform(url),
                status=WorkStatus.QUEUED,
                title="等待识别作品",
                media_kind=MediaKind.UNKNOWN,
            )
            self._items[item.id] = item
            created.append(item)
            await self._events.publish("item.created", _event_item(item))
            await self._queue.put(item.id)
        return created

    def list_items(self) -> list[WorkItem]:
        return list(self._items.values())

    def get(self, item_id: UUID) -> WorkItem | None:
        return self._items.get(item_id)

    async def remove(self, item_id: UUID) -> bool:
        item = self._items.pop(item_id, None)
        if item is None:
            return False
        await self._events.publish("item.removed", {"id": str(item_id)})
        return True

    async def clear(self) -> None:
        removed = list(self._items)
        self._items.clear()
        for item_id in removed:
            await self._events.publish("item.removed", {"id": str(item_id)})

    async def _run(self) -> None:
        while True:
            item_id = await self._queue.get()
            if item_id is None:
                self._queue.task_done()
                return
            current = self._items.get(item_id)
            if current is None:
                self._queue.task_done()
                continue
            recognizing = current.model_copy(update={"status": WorkStatus.RECOGNIZING})
            self._items[item_id] = recognizing
            await self._events.publish("item.updated", _event_item(recognizing))
            try:
                recognized = await self._recognizer.recognize(str(current.source_url))
                completed = recognized.model_copy(update={"id": item_id})
            except Exception:
                completed = current.model_copy(
                    update={"status": WorkStatus.FAILED, "title": "识别失败"}
                )
            if item_id in self._items:
                self._items[item_id] = completed
                await self._events.publish("item.updated", _event_item(completed))
            self._queue.task_done()


def _event_item(item: WorkItem) -> dict[str, object]:
    return item.model_dump(mode="json")
