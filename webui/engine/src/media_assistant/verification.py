from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict


class VerifiedOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: Path
    valid: bool
    size: int
    width: int | None = None
    height: int | None = None
    duration: float | None = None


class OutputVerifier(Protocol):
    async def verify(self, path: Path) -> VerifiedOutput: ...


class BasicOutputVerifier:
    """Phase-one file verification; FFprobe enrichment is added by packaging."""

    async def verify(self, path: Path) -> VerifiedOutput:
        if not path.is_file():
            return VerifiedOutput(path=path, valid=False, size=0)
        size = path.stat().st_size
        return VerifiedOutput(path=path, valid=size > 0, size=size)

