import asyncio
from pathlib import Path

from media_assistant.douyin_session import (
    ChromiumDouyinSessionRefresher,
    build_chromium_arguments,
    convert_douyin_detail_response,
    resolve_chromium_browser_path,
)


def test_macos_chrome_path_uses_the_installed_google_chrome(tmp_path: Path) -> None:
    chrome = tmp_path / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome"
    chrome.parent.mkdir(parents=True)
    chrome.write_bytes(b"chrome")

    resolved = resolve_chromium_browser_path([chrome])

    assert resolved == chrome.resolve()


def test_chromium_arguments_use_an_isolated_headless_profile(tmp_path: Path) -> None:
    profile = tmp_path / "isolated-profile"

    arguments = build_chromium_arguments(
        profile_dir=profile,
        port=8516,
        url="https://v.douyin.com/example/",
    )

    assert f"--user-data-dir={profile}" in arguments
    assert "--remote-debugging-port=8516" in arguments
    assert "--remote-allow-origins=*" in arguments
    assert "--headless=new" in arguments
    assert arguments[-1] == "https://v.douyin.com/example/"


def test_chromium_refresher_persists_anonymous_cookies_and_media(tmp_path: Path) -> None:
    chrome = tmp_path / "Google Chrome"
    chrome.write_bytes(b"chrome")
    captured: list[tuple[Path, str, Path, float]] = []

    def capture(browser: Path, url: str, profile: Path, timeout: float):
        captured.append((browser, url, profile, timeout))
        return (
            [
                {
                    "domain": ".douyin.com",
                    "path": "/",
                    "secure": True,
                    "expires": 0,
                    "name": "ttwid",
                    "value": "8" * 120,
                }
            ],
            {
                "Kind": "Video",
                "Title": "Mac Chrome 读取到的公开作品",
                "Videos": ["https://cdn.example/video.mp4"],
            },
        )

    session_root = tmp_path / "session"
    refresher = ChromiumDouyinSessionRefresher(
        session_root=session_root,
        browser_candidates=[chrome],
        capture=capture,
    )

    result = asyncio.run(refresher.refresh("https://v.douyin.com/example/"))

    assert result.media["Title"] == "Mac Chrome 读取到的公开作品"
    assert result.cookie_file == session_root / "douyin-cookies.txt"
    cookie_text = result.cookie_file.read_text(encoding="utf-8")
    assert "# Netscape HTTP Cookie File" in cookie_text
    assert "ttwid" in cookie_text
    assert len(captured) == 1
    browser, url, profile, timeout = captured[0]
    assert browser == chrome.resolve()
    assert url == "https://v.douyin.com/example/"
    assert profile.parent.parent == session_root / "attempts"
    assert profile.name == "chrome-profile"
    assert timeout == 70.0


def test_douyin_detail_response_maps_public_video_formats() -> None:
    payload = {
        "aweme_detail": {
            "desc": "公开作品说明",
            "author": {"nickname": "公开作者"},
            "create_time": 1_700_000_000,
            "video": {
                "origin_cover": {"url_list": ["https://cdn.example/cover.jpg"]},
                "bit_rate": [
                    {
                        "bit_rate": 2_000_000,
                        "is_h265": 0,
                        "gear_name": "1080p",
                        "play_addr": {
                            "width": 1080,
                            "height": 1920,
                            "url_list": ["https://cdn.example/video.mp4"],
                        },
                    }
                ],
            },
        }
    }

    media = convert_douyin_detail_response(
        payload,
        page_url="https://www.douyin.com/video/123",
        page_title="页面标题",
    )

    assert media["Title"] == "页面标题"
    assert media["Caption"] == "公开作品说明"
    assert media["Author"] == "公开作者"
    assert media["Images"] == ["https://cdn.example/cover.jpg"]
    assert media["Videos"] == ["https://cdn.example/video.mp4"]
    assert media["VideoOptions"][0]["Width"] == 1080
    assert media["VideoOptions"][0]["Height"] == 1920
