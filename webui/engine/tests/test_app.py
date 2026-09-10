from pathlib import Path

from fastapi.testclient import TestClient

from media_assistant.app import create_app
from media_assistant.config import AppConfig
from media_assistant.local_session import LocalSessionAuthority


def make_client(
    tmp_path: Path,
    *,
    session_authority: LocalSessionAuthority | None = None,
    update_check_scheduler=None,
) -> TestClient:
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "assets").mkdir()
    (static_dir / "index.html").write_text("<main>85 WebUI</main>", encoding="utf-8")
    (static_dir / "assets" / "app.js").write_text("window.APP85=true", encoding="utf-8")
    app = create_app(
        AppConfig(
            host="127.0.0.1",
            port=8585,
            session_token="test-token",
            static_dir=static_dir,
            data_root=tmp_path / "data",
            product_version="1.2.0",
            session_authority=session_authority,
        ),
        update_check_scheduler=update_check_scheduler,
    )
    return TestClient(app)


def test_health_is_available_without_token(tmp_path: Path) -> None:
    response = make_client(tmp_path).get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "product": "85数字多媒体下载助手",
        "productId": "com.85digital.media-assistant",
        "productVersion": "1.2.0",
        "apiVersion": "v1",
    }


def test_state_change_requires_session_token(tmp_path: Path) -> None:
    response = make_client(tmp_path).post("/api/v1/test-write")

    assert response.status_code == 401
    assert response.json()["detail"] == "本地会话无效，请重新启动应用。"


def test_state_change_accepts_session_token(tmp_path: Path) -> None:
    response = make_client(tmp_path).post(
        "/api/v1/test-write",
        headers={"X-85-Session": "test-token"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_state_change_accepts_only_a_token_signed_by_this_install(tmp_path: Path) -> None:
    authority = LocalSessionAuthority.load_or_create(tmp_path / "local-session.key")
    client = make_client(tmp_path, session_authority=authority)

    accepted = client.post(
        "/api/v1/test-write",
        headers={"X-85-Session": authority.issue()},
    )
    rejected = client.post(
        "/api/v1/test-write",
        headers={"X-85-Session": "test-token"},
    )

    assert accepted.status_code == 200
    assert rejected.status_code == 401


def test_root_serves_embedded_webui(tmp_path: Path) -> None:
    response = make_client(tmp_path).get("/")

    assert response.status_code == 200
    assert "85 WebUI" in response.text


def test_opening_the_fixed_local_url_bootstraps_a_browser_session(tmp_path: Path) -> None:
    authority = LocalSessionAuthority.load_or_create(tmp_path / "local-session.key")
    client = make_client(tmp_path, session_authority=authority)

    page = client.get("/")
    accepted = client.post("/api/v1/test-write")

    assert "85_local_session=" in page.headers["set-cookie"]
    assert "HttpOnly" in page.headers["set-cookie"]
    assert "SameSite=strict" in page.headers["set-cookie"]
    assert accepted.status_code == 200
    assert accepted.json() == {"ok": True}


def test_root_serves_bundled_frontend_assets(tmp_path: Path) -> None:
    response = make_client(tmp_path).get("/assets/app.js")

    assert response.status_code == 200
    assert "APP85" in response.text


def test_extract_input_endpoint_returns_typed_links(tmp_path: Path) -> None:
    response = make_client(tmp_path).post(
        "/api/v1/input/extract",
        headers={"X-85-Session": "test-token"},
        json={"text": "抖音 https://v.douyin.com/abc123/"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "links": [
            {
                "url": "https://v.douyin.com/abc123/",
                "platform": "douyin",
                "original_index": 3,
            }
        ]
    }


def test_version_endpoint_reports_persisted_offline_state(tmp_path: Path) -> None:
    status_path = tmp_path / "data" / "updates" / "status.json"
    status_path.parent.mkdir(parents=True)
    status_path.write_text(
        '{"state":"offline","currentVersion":"1.2.0",'
        '"message":"暂时无法检查更新，正在使用当前版本。"}',
        encoding="utf-8",
    )

    response = make_client(tmp_path).get("/api/v1/version")

    assert response.status_code == 200
    assert response.json()["state"] == "offline"
    assert response.json()["currentVersion"] == "1.2.0"


def test_manual_update_check_is_guarded_and_scheduled_once(tmp_path: Path) -> None:
    calls: list[bool] = []
    client = make_client(tmp_path, update_check_scheduler=lambda: calls.append(True))

    unauthorized = client.post("/api/v1/version/check")
    accepted = client.post(
        "/api/v1/version/check",
        headers={"X-85-Session": "test-token"},
    )

    assert unauthorized.status_code == 401
    assert accepted.status_code == 202
    assert accepted.json()["state"] == "checking"
    assert calls == [True]


def test_manual_update_check_does_not_spawn_a_duplicate(tmp_path: Path) -> None:
    status_path = tmp_path / "data" / "updates" / "status.json"
    status_path.parent.mkdir(parents=True)
    status_path.write_text(
        '{"state":"checking","currentVersion":"1.2.0",'
        '"message":"正在检查更新。"}',
        encoding="utf-8",
    )
    calls: list[bool] = []
    client = make_client(tmp_path, update_check_scheduler=lambda: calls.append(True))

    response = client.post(
        "/api/v1/version/check",
        headers={"X-85-Session": "test-token"},
    )

    assert response.status_code == 202
    assert response.json()["state"] == "checking"
    assert calls == []
