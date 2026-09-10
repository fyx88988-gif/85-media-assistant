import asyncio
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Event:
    id: int
    kind: str
    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "payload": self.payload}


class EventBus:
    def __init__(self, max_events: int = 1_000) -> None:
        if max_events < 1:
            raise ValueError("事件缓冲区至少保留一条事件。")
        self._events: deque[Event] = deque(maxlen=max_events)
        self._next_id = 1
        self._condition = asyncio.Condition()

    async def publish(self, kind: str, payload: dict[str, Any]) -> Event:
        async with self._condition:
            event = Event(self._next_id, kind, payload)
            self._next_id += 1
            self._events.append(event)
            self._condition.notify_all()
            return event

    def events_after(self, event_id: int) -> list[Event]:
        return [event for event in self._events if event.id > event_id]

    async def subscribe(self, event_id: int = 0) -> AsyncIterator[Event]:
        cursor = event_id
        while True:
            available = self.events_after(cursor)
            if not available:
                async with self._condition:
                    await self._condition.wait_for(lambda: bool(self.events_after(cursor)))
                continue
            for event in available:
                cursor = event.id
                yield event

