import asyncio
import os
import subprocess

import pytest

from media_assistant.processes import (
    AsyncDownloadRunner,
    AsyncProcessRunner,
    start_detached_process,
)


class FakeProcess:
    returncode = 0
    pid = 1234

    async def communicate(self):
        return b"", b""


@pytest.mark.skipif(os.name != "nt", reason="Windows console behavior")
def test_process_runner_hides_windows_console(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*argv, **kwargs):
        captured.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    asyncio.run(AsyncProcessRunner().run(["powershell.exe", "-NoProfile"], 1.0))

    assert int(captured["creationflags"]) & subprocess.CREATE_NO_WINDOW


@pytest.mark.skipif(os.name != "nt", reason="Windows console behavior")
def test_download_runner_hides_windows_console_and_keeps_process_group(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_create_subprocess_exec(*argv, **kwargs):
        captured.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    asyncio.run(AsyncDownloadRunner().start(["yt-dlp.exe", "https://example.com/video"]))

    flags = int(captured["creationflags"])
    assert flags & subprocess.CREATE_NO_WINDOW
    assert flags & subprocess.CREATE_NEW_PROCESS_GROUP


@pytest.mark.skipif(os.name != "nt", reason="Windows console behavior")
def test_detached_updater_never_opens_a_console(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeDetachedProcess:
        pass

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        captured.update(kwargs)
        return FakeDetachedProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    start_detached_process(["updater.exe", "--check"])

    flags = int(captured["creationflags"])
    assert flags & subprocess.CREATE_NO_WINDOW
    assert flags & subprocess.DETACHED_PROCESS
    assert captured["stdin"] is subprocess.DEVNULL
    assert captured["stdout"] is subprocess.DEVNULL
    assert captured["stderr"] is subprocess.DEVNULL
