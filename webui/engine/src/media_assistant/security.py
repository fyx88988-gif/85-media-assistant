import secrets
from collections.abc import Callable
from typing import Annotated

from fastapi import Header, HTTPException

from .config import AppConfig


def build_session_guard(config: AppConfig) -> Callable[..., None]:
    """Create a FastAPI dependency bound to one launcher session."""

    def require_session(
        x_85_session: Annotated[str | None, Header(alias="X-85-Session")] = None,
    ) -> None:
        if config.session_authority is None:
            valid = x_85_session is not None and secrets.compare_digest(
                x_85_session,
                config.session_token,
            )
        else:
            valid = x_85_session is not None and config.session_authority.validate(
                x_85_session
            )
        if not valid:
            raise HTTPException(
                status_code=401,
                detail="本地会话无效，请重新启动应用。",
            )

    return require_session
