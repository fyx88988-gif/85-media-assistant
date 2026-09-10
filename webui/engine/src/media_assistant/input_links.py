import re
from urllib.parse import urlsplit

from pydantic import TypeAdapter, ValidationError

from .models import ExtractedLink, Platform


_HTTP_URL = re.compile(r"https?://[^\s<>\"'\]\[()]+", re.IGNORECASE)
_HTTP_URL_ADAPTER = TypeAdapter(str)
_TRAILING_PUNCTUATION = ")]}>）】」』，。；;！？!、：:"

_HOST_PLATFORMS: tuple[tuple[tuple[str, ...], Platform], ...] = (
    (("douyin.com",), Platform.DOUYIN),
    (("kuaishou.com", "chenzhongtech.com"), Platform.KUAISHOU),
    (("xiaohongshu.com", "xhslink.cn"), Platform.XIAOHONGSHU),
    (("youtube.com", "youtu.be"), Platform.YOUTUBE),
    (("instagram.com",), Platform.INSTAGRAM),
    (("x.com", "twitter.com"), Platform.X),
    (("tiktok.com",), Platform.TIKTOK),
    (("pinterest.com", "pin.it"), Platform.PINTEREST),
    (("bilibili.com", "b23.tv"), Platform.BILIBILI),
    (("weibo.com", "weibo.cn"), Platform.WEIBO),
    (("vimeo.com",), Platform.VIMEO),
    (("facebook.com", "fb.watch"), Platform.FACEBOOK),
    (("twitch.tv",), Platform.TWITCH),
    (("dailymotion.com", "dai.ly"), Platform.DAILYMOTION),
)


def _clean_url(raw_url: str) -> str:
    cleaned = raw_url.replace("\\&", "&").replace("\\_", "_")
    return cleaned.rstrip(_TRAILING_PUNCTUATION)


def _host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith(f".{domain}")


def detect_platform(url: str) -> Platform:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    for domains, platform in _HOST_PLATFORMS:
        if any(_host_matches(host, domain) for domain in domains):
            return platform
    return Platform.OTHER


def extract_links(text: str) -> list[ExtractedLink]:
    if not text:
        return []

    links: list[ExtractedLink] = []
    seen: set[str] = set()
    for match in _HTTP_URL.finditer(text):
        cleaned = _clean_url(match.group(0))
        if cleaned in seen:
            continue
        try:
            # Validation is completed by ExtractedLink's HttpUrl field. This
            # early adapter rejects control characters before model creation.
            _HTTP_URL_ADAPTER.validate_python(cleaned)
            link = ExtractedLink(
                url=cleaned,
                platform=detect_platform(cleaned),
                original_index=match.start(),
            )
        except ValidationError:
            continue
        seen.add(cleaned)
        links.append(link)
    return links
