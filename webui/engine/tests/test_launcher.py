import os
import socket
from pathlib import Path

import httpx
import pytest

import media_assistant.launcher as launcher
from media_assistant.install_layout import InstallLayout
from media_assistant.launcher import (
    PRODUCT_ID,
    PortConflictError,
    build_local_url,
    probe_existing_instance,
    require_available_local_port,
    schedule_update_check,
    _find_component,
)


def test_launcher_uses_the_fixed_local_webui_address() -> None:
    url = build_local_url("safe token")

    assert url == "http://127.0.0.1:8515/?session=safe+token"


def test_launcher_does_not_reuse_a_foreign_service(monkeypatch) -> None:
    class ForeignResponse:
        status_code = 200

        @staticmethod
        def json() -> dict[str, str]:
            return {"status": "ok", "productId": "other-product"}

    monkeypatch.setattr(httpx.Client, "get", lambda self, url: ForeignResponse())

    assert probe_existing_instance("http://127.0.0.1:8515", PRODUCT_ID) is False


def test_launcher_reuses_only_the_expected_product(monkeypatch) -> None:
    class ProductResponse:
        status_code = 200

        @staticmethod
        def json() -> dict[str, str]:
            return {"status": "ok", "productId": PRODUCT_ID}

    monkeypatch.setattr(httpx.Client, "get", lambda self, url: ProductResponse())

    assert probe_existing_instance("http://127.0.0.1:8515", PRODUCT_ID) is True


def test_launcher_reports_a_foreign_listener_on_the_fixed_port() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = int(listener.getsockname()[1])

        with pytest.raises(PortConflictError, match=f"本地端口 {port} 已被其他程序占用"):
            require_available_local_port(port)


def test_launcher_allows_only_one_instance_for_the_same_runtime_directory(tmp_path) -> None:
    lock_type = getattr(launcher, "AppInstanceLock", None)
    assert lock_type is not None
    first = lock_type(tmp_path / "app.lock")
    second = lock_type(tmp_path / "app.lock")

    assert first.acquire() is True
    try:
        assert second.acquire() is False
    finally:
        first.release()

    assert second.acquire() is True
    second.release()


def test_launcher_schedules_one_delayed_update_check(tmp_path: Path) -> None:
    layout = InstallLayout.for_root(tmp_path / "app", tmp_path / "data")

    class FakeScheduler:
        def __init__(self) -> None:
            self.calls: list[tuple[float, list[str]]] = []

        def schedule(self, delay_seconds: float, command: list[str]) -> None:
            self.calls.append((delay_seconds, command))

    scheduler = FakeScheduler()

    schedule_update_check(layout, delay_seconds=4.0, scheduler=scheduler)

    assert scheduler.calls == [
        (4.0, [
            str(layout.updater_executable),
            "--check",
            "--data-root",
            str(layout.data_root),
            "--install-root",
            str(layout.install_root),
        ])
    ]


def test_launcher_prefers_the_active_versioned_component(tmp_path: Path) -> None:
    install_root = tmp_path / "app"
    suffix = ".exe" if os.name == "nt" else ""
    direct = install_root / "components" / "yt-dlp" / f"yt-dlp{suffix}"
    direct.parent.mkdir(parents=True)
    direct.touch()
    versioned = (
        install_root
        / "components"
        / "yt-dlp"
        / "versions"
        / "2026.9.10"
        / f"yt-dlp{suffix}"
    )
    versioned.parent.mkdir(parents=True)
    versioned.touch()
    (direct.parent / "current.json").write_text(
        '{"version":"2026.9.10"}', encoding="utf-8"
    )

    assert _find_component("yt-dlp", "UNUSED_COMPONENT_PATH", install_root) == versioned
