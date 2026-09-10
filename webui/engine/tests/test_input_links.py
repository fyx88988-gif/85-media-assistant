from media_assistant.input_links import extract_links
from media_assistant.models import Platform


def test_extracts_share_text_and_markdown_without_duplicates() -> None:
    text = (
        "复制后打开抖音 https://v.douyin.com/abc123/\n"
        "[作品](https://x.com/user/status/123?s=46\\&utm_source=copy)\n"
        "再次出现 https://v.douyin.com/abc123/"
    )

    result = extract_links(text)

    assert [str(item.url) for item in result] == [
        "https://v.douyin.com/abc123/",
        "https://x.com/user/status/123?s=46&utm_source=copy",
    ]
    assert [item.platform for item in result] == [Platform.DOUYIN, Platform.X]
    assert [item.original_index for item in result] == sorted(
        item.original_index for item in result
    )


def test_strips_trailing_chinese_and_markdown_punctuation() -> None:
    result = extract_links(
        "小红书 https://xhslink.cn/o/8FYpCjUSolL）。\n"
        "Pinterest [https://pin.it/2xcaIBFQ8](https://pin.it/2xcaIBFQ8)"
    )

    assert [str(item.url) for item in result] == [
        "https://xhslink.cn/o/8FYpCjUSolL",
        "https://pin.it/2xcaIBFQ8",
    ]
    assert [item.platform for item in result] == [
        Platform.XIAOHONGSHU,
        Platform.PINTEREST,
    ]


def test_maps_supported_platform_hosts() -> None:
    text = "\n".join(
        [
            "https://v.kuaishou.com/K19baEe3",
            "https://youtu.be/abc",
            "https://www.instagram.com/reel/abc/",
            "https://www.tiktok.com/@demo/video/123",
            "https://www.bilibili.com/video/BV1xx",
            "https://weibo.com/123/abc",
            "https://vimeo.com/123",
            "https://www.facebook.com/reel/123",
            "https://www.twitch.tv/videos/123",
            "https://www.dailymotion.com/video/abc",
            "https://unknown.example/video/85",
        ]
    )

    assert [item.platform for item in extract_links(text)] == [
        Platform.KUAISHOU,
        Platform.YOUTUBE,
        Platform.INSTAGRAM,
        Platform.TIKTOK,
        Platform.BILIBILI,
        Platform.WEIBO,
        Platform.VIMEO,
        Platform.FACEBOOK,
        Platform.TWITCH,
        Platform.DAILYMOTION,
        Platform.OTHER,
    ]


def test_rejects_non_http_and_empty_input() -> None:
    assert extract_links("") == []
    assert extract_links("file:///C:/secret.txt javascript:alert(1)") == []
