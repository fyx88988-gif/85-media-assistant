import asyncio
import re
from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from .processes import ProcessResult
from .verification import OutputVerifier, VerifiedOutput


class DownloadStatus(StrEnum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DownloadRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_url: HttpUrl
    format_id: str = Field(min_length=1)
    output_dir: Path
    title: str = Field(min_length=1)


class CreateDownloadRequest(BaseModel):
    item_id: UUID
    format_id: str = Field(min_length=1)
    output_dir: Path


class DownloadJob(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    status: DownloadStatus
    request: DownloadRequest
    progress: float = 0.0
    output: VerifiedOutput | None = None
    failure: str | None = None


class RunningProcess(Protocol):
    async def wait(self) -> ProcessResult: ...

    async def terminate_tree(self) -> None: ...


class DownloadRunner(Protocol):
    async def start(self, argv: Sequence[str]) -> RunningProcess: ...


class DownloadService:
    def __init__(
        self,
        *,
        binary: Path,
        runner: DownloadRunner,
        verifier: OutputVerifier,
        cookie_file: Path | None = None,
    ) -> None:
        self._binary = binary
        self._runner = runner
        self._verifier = verifier
        self._cookie_file = cookie_file
        self._jobs: dict[UUID, DownloadJob] = {}
        self._processes: dict[UUID, RunningProcess] = {}
        self._tasks: dict[UUID, asyncio.Task[None]] = {}

    async def start(
        self,
        request: DownloadRequest,
        *,
        direct_url: str | None = None,
        request_headers: dict[str, str] | None = None,
    ) -> DownloadJob:
        request.output_dir.mkdir(parents=True, exist_ok=True)
        output_base = request.output_dir / _safe_title(request.title)
        output_template = Path(f"{output_base}.%(ext)s")
        argv = [
            str(self._binary),
            "--no-playlist",
            "--newline",
        ]
        if direct_url:
            for name, value in (request_headers or {}).items():
                argv.extend(["--add-header", f"{name}:{value}"])
            argv.extend(["--output", str(output_base.with_suffix(".mp4")), direct_url])
        else:
            if self._cookie_file is not None and self._cookie_file.is_file():
                argv.extend(["--cookies", str(self._cookie_file)])
            argv.extend([
                "--format",
                f"{request.format_id}+bestaudio/best",
                "--merge-output-format",
                "mp4",
                "--output",
                str(output_template),
                str(request.source_url),
            ])
        process = await self._runner.start(argv)
        job = DownloadJob(status=DownloadStatus.DOWNLOADING, request=request)
        self._jobs[job.id] = job
        self._processes[job.id] = process
        self._tasks[job.id] = asyncio.create_task(
            self._finalize(job.id, output_base.with_suffix(".mp4")),
            name=f"download-{job.id}",
        )
        return job

    async def cancel(self, job_id: UUID) -> DownloadJob:
        job = self._require_job(job_id)
        if job.status not in {DownloadStatus.QUEUED, DownloadStatus.DOWNLOADING}:
            return job
        self._jobs[job_id] = job.model_copy(
            update={"status": DownloadStatus.CANCELLED, "failure": None}
        )
        process = self._processes.get(job_id)
        if process is not None:
            await process.terminate_tree()
        task = self._tasks.get(job_id)
        if task is not None:
            await task
        return self._jobs[job_id]

    async def wait(self, job_id: UUID) -> DownloadJob:
        task = self._tasks.get(job_id)
        if task is not None:
            await task
        return self._require_job(job_id)

    def list_jobs(self) -> list[DownloadJob]:
        return list(self._jobs.values())

    async def _finalize(self, job_id: UUID, expected_path: Path) -> None:
        process = self._processes[job_id]
        result = await process.wait()
        current = self._jobs[job_id]
        if current.status == DownloadStatus.CANCELLED:
            return
        if result.returncode != 0:
            self._jobs[job_id] = current.model_copy(
                update={"status": DownloadStatus.FAILED, "failure": "下载任务执行失败，请重新尝试。"}
            )
            return
        output = await self._verifier.verify(expected_path)
        if not output.valid:
            self._jobs[job_id] = current.model_copy(
                update={
                    "status": DownloadStatus.FAILED,
                    "failure": "下载结果校验失败，没有生成有效媒体文件。",
                }
            )
            return
        self._jobs[job_id] = current.model_copy(
            update={
                "status": DownloadStatus.COMPLETED,
                "progress": 100.0,
                "output": output,
                "failure": None,
            }
        )

    def _require_job(self, job_id: UUID) -> DownloadJob:
        try:
            return self._jobs[job_id]
        except KeyError as exc:
            raise KeyError("没有找到该下载任务。") from exc


_UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_title(title: str) -> str:
    cleaned = _UNSAFE_FILENAME.sub("_", title).strip().rstrip(".")
    return cleaned[:120] or "未命名作品"


def resolve_output_dir(requested: Path, *, home: Path | None = None) -> Path:
    """Map the WebUI's portable default to the current user's Downloads folder."""
    if requested.is_absolute():
        return requested
    return (home or Path.home()) / "Downloads"
