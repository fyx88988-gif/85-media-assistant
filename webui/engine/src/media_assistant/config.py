from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .versioning import ProductVersion

if TYPE_CHECKING:
    from .local_session import LocalSessionAuthority


PRODUCT_ID = "com.85digital.media-assistant"
PRODUCT_NAME = "85数字多媒体下载助手"


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Local-only server configuration created by the desktop launcher."""

    host: str
    port: int = 8515
    session_token: str = ""
    static_dir: Path = field(default_factory=lambda: Path("."))
    product_version: str = "0.1.0"
    data_root: Path = field(default_factory=lambda: Path("data"))
    session_authority: "LocalSessionAuthority | None" = None

    def __post_init__(self) -> None:
        if self.host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("本地引擎只能绑定到回环地址。")
        if not 0 < self.port < 65536:
            raise ValueError("本地端口必须在 1 到 65535 之间。")
        if not self.session_token:
            raise ValueError("本地会话令牌不能为空。")
        ProductVersion.parse(self.product_version)
