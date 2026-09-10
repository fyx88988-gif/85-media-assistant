import asyncio
import os
import signal
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str


class ProcessRunner(Protocol):
    async def run(self, argv: Sequence[str], timeout: float) -> ProcessResult: ...


class AsyncProcessRunner:
    """Run a child process without invoking a command shell."""

    async def run(self, argv: Sequence[str], timeout: float) -> ProcessResult:
        if not argv:
            raise ValueError("进程参数不能为空。")
        kwargs: dict[str, object] = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **kwargs,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        return ProcessResult(
            returncode=process.returncode or 0,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
        )


class AsyncRunningProcess:
    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self._process = process

    async def wait(self) -> ProcessResult:
        stdout, stderr = await self._process.communicate()
        return ProcessResult(
            returncode=self._process.returncode or 0,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
        )

    async def terminate_tree(self) -> None:
        if self._process.returncode is not None:
            return
        if os.name == "nt":
            killer = await asyncio.create_subprocess_exec(
                "taskkill", "/PID", str(self._process.pid), "/T", "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            await killer.wait()
        else:
            os.killpg(self._process.pid, signal.SIGTERM)
        await self._process.wait()


class AsyncDownloadRunner:
    async def start(self, argv: Sequence[str]) -> AsyncRunningProcess:
        if not argv:
            raise ValueError("进程参数不能为空。")
        kwargs: dict[str, object] = {}
        if os.name == "nt":
            kwargs["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            )
        else:
            kwargs["start_new_session"] = True
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **kwargs,
        )
        return AsyncRunningProcess(process)


def start_detached_process(argv: Sequence[str]) -> subprocess.Popen[bytes]:
    """Start a background helper without a shell or visible terminal."""

    if not argv:
        raise ValueError("进程参数不能为空。")
    kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
        )
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(list(argv), **kwargs)


def require_binary(path: Path) -> Path:
    if not path.is_file():
        raise FileNotFoundError(f"缺少本地组件：{path.name}")
    return path
