from collections.abc import Iterable

from .models import WorkItem
from .providers.base import MediaProvider


class RecognitionService:
    def __init__(self, providers: Iterable[MediaProvider]) -> None:
        self.providers = tuple(providers)

    async def recognize(self, url: str) -> WorkItem:
        provider = next((value for value in self.providers if value.can_handle(url)), None)
        if provider is None:
            raise ValueError("没有可处理该链接的本地识别器。")
        return await provider.recognize(url)
