import secrets
from collections.abc import Callable
from typing import Annotated

from fastapi import Cookie, Header, HTTPException

from .config import AppConfig


def build_session_guard(config: AppConfig) -> Callable[..., None]:
    """Create a FastAPI dependency bound to one launcher session."""

    def require_session(
        x_85_session: Annotated[str | None, Header(alias="X-85-Session")] = None,
        local_session: Annotated[
            str | None,
            Cookie(alias="85_local_session"),
        ] = None,
    ) -> None:
        candidate = x_85_session or local_session
        if config.session_authority is None:
            valid = candidate is not None and secrets.compare_digest(
                candidate,
                config.session_token,
            )
        else:
            valid = candidate is not None and config.session_authority.validate(
                candidate
            )
        if not valid:
            raise HTTPException(
                status_code=401,
                detail="本地会话无效，请重新启动应用。",
            )

    return require_session
