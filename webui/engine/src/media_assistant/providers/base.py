from typing import Protocol

from ..models import WorkItem


class MediaProvider(Protocol):
    def can_handle(self, url: str) -> bool: ...

    async def recognize(self, url: str) -> WorkItem: ...

