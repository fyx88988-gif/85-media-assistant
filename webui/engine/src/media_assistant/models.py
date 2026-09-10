from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class Platform(StrEnum):
    DOUYIN = "douyin"
    KUAISHOU = "kuaishou"
    XIAOHONGSHU = "xiaohongshu"
    YOUTUBE = "youtube"
    INSTAGRAM = "instagram"
    X = "x"
    TIKTOK = "tiktok"
    PINTEREST = "pinterest"
    BILIBILI = "bilibili"
    WEIBO = "weibo"
    VIMEO = "vimeo"
    FACEBOOK = "facebook"
    TWITCH = "twitch"
    DAILYMOTION = "dailymotion"
    OTHER = "other"


class WorkStatus(StrEnum):
    QUEUED = "queued"
    RECOGNIZING = "recognizing"
    READY = "ready"
    FAILED = "failed"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class MediaKind(StrEnum):
    VIDEO = "video"
    GALLERY = "gallery"
    AUDIO = "audio"
    UNKNOWN = "unknown"


class RecognitionFailure(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    message: str
    retryable: bool = True


class MediaFormat(BaseModel):
    model_config = ConfigDict(frozen=True)

    format_id: str
    note: str | None = None
    extension: str | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    file_size: int | None = None
    has_video: bool
    has_audio: bool
    preview_url: str | None = None
    request_headers: dict[str, str] = Field(default_factory=dict, exclude=True, repr=False)


class WorkItem(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    source_url: HttpUrl
    platform: Platform
    status: WorkStatus
    title: str
    author: str | None = None
    description: str | None = None
    thumbnail_url: str | None = None
    media_kind: MediaKind = MediaKind.UNKNOWN
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    rotation: int = 0
    formats: list[MediaFormat] = Field(default_factory=list)
    failure: RecognitionFailure | None = None


class ExtractedLink(BaseModel):
    model_config = ConfigDict(frozen=True)

    url: HttpUrl
    platform: Platform
    original_index: int


class ExtractLinksRequest(BaseModel):
    text: str


class ExtractLinksResponse(BaseModel):
    links: list[ExtractedLink]
