import asyncio
from pathlib import Path
from typing import Sequence

from media_assistant.douyin_session import WindowsDouyinSessionRefresher
from media_assistant.processes import ProcessResult


class CookieWritingRunner:
    def __init__(self) -> None:
        self.argv: list[str] = []
        self.timeout = 0.0

    async def run(self, argv: Sequence[str], timeout: float) -> ProcessResult:
        self.argv = list(argv)
        self.timeout = timeout
        output_index = self.argv.index("-CookieOutput") + 1
        Path(self.argv[output_index]).write_text(
            "# Netscape HTTP Cookie File\n.douyin.com\tTRUE\t/\tTRUE\t0\tttwid\t" + "8" * 120,
            encoding="utf-8",
        )
        if "-MediaOutput" in self.argv:
            media_index = self.argv.index("-MediaOutput") + 1
            Path(self.argv[media_index]).write_text(
                '{"Kind":"Video","Title":"公开作品","Videos":["https://cdn.example/video.mp4"]}',
                encoding="utf-8",
            )
        return ProcessResult(0, "", "")


class FailOnceRunner(CookieWritingRunner):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[list[str]] = []

    async def run(self, argv: Sequence[str], timeout: float) -> ProcessResult:
        self.calls.append(list(argv))
        if len(self.calls) == 1:
            return ProcessResult(1, "", "browser profile is locked")
        return await super().run(argv, timeout)


class TimeoutOnceRunner(CookieWritingRunner):
    def __init__(self) -> None:
        super().__init__()
        self.call_count = 0

    async def run(self, argv: Sequence[str], timeout: float) -> ProcessResult:
        self.call_count += 1
        if self.call_count == 1:
            raise TimeoutError
        return await super().run(argv, timeout)


def test_windows_refresher_uses_bundled_helper_and_isolated_session_root(tmp_path: Path) -> None:
    helper = tmp_path / "Refresh-DouyinSession.ps1"
    module = tmp_path / "VideoDownloader.Douyin.psm1"
    helper.write_text("# helper", encoding="utf-8")
    module.write_text("# module", encoding="utf-8")
    runner = CookieWritingRunner()
    refresher = WindowsDouyinSessionRefresher(
        helper_script=helper,
        module_path=module,
        session_root=tmp_path / "session",
        runner=runner,
    )

    result = asyncio.run(refresher.refresh("https://v.douyin.com/example/"))

    assert result.cookie_file == tmp_path / "session" / "douyin-cookies.txt"
    assert result.media["Title"] == "公开作品"
    assert result.media["Videos"] == ["https://cdn.example/video.mp4"]
    assert runner.argv[runner.argv.index("-WindowStyle") + 1] == "Hidden"
    assert runner.argv[runner.argv.index("-ModulePath") + 1] == str(module)
    assert runner.argv[runner.argv.index("-Url") + 1] == "https://v.douyin.com/example/"
    assert runner.timeout == 70.0


def test_windows_refresher_retries_with_a_new_isolated_attempt_directory(tmp_path: Path) -> None:
    helper = tmp_path / "Refresh-DouyinSession.ps1"
    module = tmp_path / "VideoDownloader.Douyin.psm1"
    helper.write_text("# helper", encoding="utf-8")
    module.write_text("# module", encoding="utf-8")
    runner = FailOnceRunner()
    session_root = tmp_path / "session"
    refresher = WindowsDouyinSessionRefresher(
        helper_script=helper,
        module_path=module,
        session_root=session_root,
        runner=runner,
    )

    result = asyncio.run(refresher.refresh("https://v.douyin.com/example/"))

    assert len(runner.calls) == 2
    attempt_roots = [
        Path(call[call.index("-SessionRoot") + 1])
        for call in runner.calls
    ]
    assert attempt_roots[0] != attempt_roots[1]
    assert all(root.parent == session_root / "attempts" for root in attempt_roots)
    assert result.cookie_file == session_root / "douyin-cookies.txt"
    assert result.cookie_file.is_file()
    assert result.media["Videos"] == ["https://cdn.example/video.mp4"]


def test_windows_refresher_retries_after_the_first_attempt_times_out(tmp_path: Path) -> None:
    helper = tmp_path / "Refresh-DouyinSession.ps1"
    module = tmp_path / "VideoDownloader.Douyin.psm1"
    helper.write_text("# helper", encoding="utf-8")
    module.write_text("# module", encoding="utf-8")
    runner = TimeoutOnceRunner()
    refresher = WindowsDouyinSessionRefresher(
        helper_script=helper,
        module_path=module,
        session_root=tmp_path / "session",
        runner=runner,
    )

    result = asyncio.run(refresher.refresh("https://v.douyin.com/example/"))

    assert runner.call_count == 2
    assert result.media["Title"] == "公开作品"
