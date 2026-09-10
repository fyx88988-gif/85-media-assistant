import json
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import AppConfig, PRODUCT_ID, PRODUCT_NAME
from .downloads import (
    CreateDownloadRequest,
    DownloadJob,
    DownloadRequest,
    DownloadService,
    resolve_output_dir,
)
from .events import EventBus
from .input_links import extract_links
from .items import ItemService, ItemsResponse, RecognizeItemsRequest
from .models import ExtractLinksRequest, ExtractLinksResponse
from .preview import (
    PreviewDescriptor,
    PreviewResolver,
    PreviewStreamer,
    PreviewStreamError,
)
from .security import build_session_guard
from .update_runner import UpdateStatus, UpdateStatusStore


def create_app(
    config: AppConfig,
    *,
    item_service: ItemService | None = None,
    event_bus: EventBus | None = None,
    download_service: DownloadService | None = None,
    preview_streamer: PreviewStreamer | None = None,
    update_check_scheduler: Callable[[], None] | None = None,
) -> FastAPI:
    """Build the local API and serve the embedded WebUI entry page."""

    if (item_service is None) is not (event_bus is None):
        raise ValueError("作品服务和事件总线必须同时提供。")
    if download_service is not None and item_service is None:
        raise ValueError("下载服务必须与作品服务同时提供。")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if item_service is not None:
            await item_service.start()
        try:
            yield
        finally:
            if item_service is not None:
                await item_service.stop()

    app = FastAPI(
        title="85数字多媒体下载助手",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    require_session = build_session_guard(config)
    assets_dir = config.static_dir / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "product": PRODUCT_NAME,
            "productId": PRODUCT_ID,
            "productVersion": config.product_version,
            "apiVersion": "v1",
        }

    def update_status_payload(value: UpdateStatus) -> dict[str, object]:
        return {
            "state": value.state,
            "currentVersion": value.current_version,
            "availableVersion": value.available_version,
            "progress": value.progress,
            "checkedAt": value.checked_at,
            "message": value.message,
        }

    update_status_store = UpdateStatusStore(config.data_root / "updates" / "status.json")

    @app.get("/api/v1/version")
    def version_status() -> dict[str, object]:
        try:
            value = update_status_store.read()
        except ValueError:
            value = UpdateStatus(
                state="error",
                current_version=config.product_version,
                message="更新状态暂时不可用，不影响当前功能。",
            )
        if value.current_version == "0.1.0" and config.product_version != "0.1.0":
            value = UpdateStatus(
                state=value.state,
                current_version=config.product_version,
                available_version=value.available_version,
                progress=value.progress,
                checked_at=value.checked_at,
                message=value.message,
            )
        return update_status_payload(value)

    @app.post(
        "/api/v1/version/check",
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(require_session)],
    )
    def check_version() -> dict[str, object]:
        current = version_status()
        if current["state"] == "checking":
            return current
        value = UpdateStatus(
            state="checking",
            current_version=config.product_version,
            message="正在检查更新。",
        )
        update_status_store.write(value)
        if update_check_scheduler is not None:
            update_check_scheduler()
        return update_status_payload(value)

    @app.post("/api/v1/test-write", dependencies=[Depends(require_session)])
    def test_write() -> dict[str, bool]:
        return {"ok": True}

    @app.post(
        "/api/v1/input/extract",
        response_model=ExtractLinksResponse,
        dependencies=[Depends(require_session)],
    )
    def extract_input(request: ExtractLinksRequest) -> ExtractLinksResponse:
        return ExtractLinksResponse(links=extract_links(request.text))

    if item_service is not None and event_bus is not None:

        @app.post(
            "/api/v1/items/recognize",
            response_model=ItemsResponse,
            status_code=status.HTTP_202_ACCEPTED,
            dependencies=[Depends(require_session)],
        )
        async def recognize_items(request: RecognizeItemsRequest) -> ItemsResponse:
            items = await item_service.enqueue(str(url) for url in request.urls)
            return ItemsResponse(items=items)

        @app.get("/api/v1/items", response_model=ItemsResponse)
        def list_items() -> ItemsResponse:
            return ItemsResponse(items=item_service.list_items())

        @app.get(
            "/api/v1/items/{item_id}/preview",
            response_model=PreviewDescriptor,
        )
        def item_preview(item_id: UUID) -> PreviewDescriptor:
            item = item_service.get(item_id)
            if item is None:
                raise HTTPException(404, "没有找到该作品。")
            if item.status != "ready":
                raise HTTPException(409, "作品尚未完成识别。")
            return PreviewResolver().resolve(item)

        @app.get("/api/v1/items/{item_id}/media")
        async def item_media(
            item_id: UUID,
            format_id: str,
            range_header: Annotated[str | None, Header(alias="Range")] = None,
        ) -> Response:
            item = item_service.get(item_id)
            if item is None:
                raise HTTPException(404, "没有找到该作品。")
            media_format = PreviewResolver().playable_format(item, format_id)
            if media_format is None:
                raise HTTPException(404, "没有找到可播放的媒体格式。")
            if preview_streamer is None:
                raise HTTPException(503, "本地预览服务尚未启动。")
            try:
                return await preview_streamer.response(media_format, range_header)
            except PreviewStreamError as exc:
                raise HTTPException(502, str(exc)) from exc

        @app.delete(
            "/api/v1/items/{item_id}",
            status_code=status.HTTP_204_NO_CONTENT,
            dependencies=[Depends(require_session)],
        )
        async def delete_item(item_id: UUID) -> Response:
            if not await item_service.remove(item_id):
                raise HTTPException(404, "没有找到该作品。")
            return Response(status_code=status.HTTP_204_NO_CONTENT)

        @app.delete(
            "/api/v1/items",
            status_code=status.HTTP_204_NO_CONTENT,
            dependencies=[Depends(require_session)],
        )
        async def clear_items() -> Response:
            await item_service.clear()
            return Response(status_code=status.HTTP_204_NO_CONTENT)

        @app.get("/api/v1/events")
        def events(
            last_event_id: Annotated[int | None, Header(alias="Last-Event-ID")] = None,
        ) -> StreamingResponse:
            async def stream():
                async for event in event_bus.subscribe(last_event_id or 0):
                    payload = json.dumps(event.as_dict(), ensure_ascii=False)
                    yield f"id: {event.id}\nevent: {event.kind}\ndata: {payload}\n\n"

            return StreamingResponse(stream(), media_type="text/event-stream")

        if download_service is not None:

            @app.post(
                "/api/v1/downloads",
                response_model=DownloadJob,
                status_code=status.HTTP_202_ACCEPTED,
                dependencies=[Depends(require_session)],
            )
            async def create_download(request: CreateDownloadRequest) -> DownloadJob:
                item = item_service.get(request.item_id)
                if item is None:
                    raise HTTPException(404, "没有找到该作品。")
                if item.status != "ready":
                    raise HTTPException(409, "作品尚未完成识别。")
                selected_format = next(
                    (value for value in item.formats if value.format_id == request.format_id),
                    None,
                )
                if selected_format is None:
                    raise HTTPException(409, "所选源格式已经不可用，请重新选择。")
                job = await download_service.start(
                    DownloadRequest(
                        source_url=item.source_url,
                        format_id=request.format_id,
                        output_dir=resolve_output_dir(request.output_dir),
                        title=item.title,
                    ),
                    direct_url=(
                        selected_format.preview_url
                        if selected_format.format_id.startswith("douyin-browser-")
                        else None
                    ),
                    request_headers=(
                        selected_format.request_headers
                        if selected_format.format_id.startswith("douyin-browser-")
                        else None
                    ),
                )
                await event_bus.publish("download.updated", job.model_dump(mode="json"))
                return job

            @app.post(
                "/api/v1/downloads/{job_id}/cancel",
                response_model=DownloadJob,
                dependencies=[Depends(require_session)],
            )
            async def cancel_download(job_id: UUID) -> DownloadJob:
                try:
                    job = await download_service.cancel(job_id)
                except KeyError:
                    raise HTTPException(404, "没有找到该下载任务。") from None
                await event_bus.publish("download.updated", job.model_dump(mode="json"))
                return job

            @app.get("/api/v1/downloads")
            def list_downloads() -> dict[str, list[DownloadJob]]:
                return {"jobs": download_service.list_jobs()}

    @app.get("/", response_class=HTMLResponse)
    def root() -> str:
        return (config.static_dir / "index.html").read_text(encoding="utf-8")

    return app
