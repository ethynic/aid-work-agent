"""视频生成 API 路由单元测试（FastAPI TestClient，mock service）。

验证 7 个端点的响应契约 {success,data,error}：
- GET /scenes 返回场景列表
- POST /sessions 创建会话（含参数校验失败分支）
- GET /sessions / GET /sessions/{id} 查询
- PATCH /cards/{id}/kept 留用
- POST /cards/{id}/regenerate 重新生成
- GET /cards/{id}/download-url 下载 URL
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import video_gen as api_mod

pytestmark = [pytest.mark.unit]


def _make_app():
    """构建只含 video_gen 路由的测试 app，并 mock 掉全局 _service。"""
    app = FastAPI()
    app.include_router(api_mod.router)
    return app


@pytest.fixture
def client():
    return TestClient(_make_app())


def test_list_scenes(client):
    mock_svc = MagicMock()
    mock_svc.list_scenes.return_value = [
        {"scene_id": "product_showcase", "name": "产品展示", "description": "d1"},
        {"scene_id": "atmosphere", "name": "场景氛围", "description": "d2"},
    ]
    with patch.object(api_mod, "_service", mock_svc):
        resp = client.get("/api/video-gen/scenes")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert len(body["data"]["items"]) == 2


def test_create_session_success(client):
    mock_svc = MagicMock()
    mock_svc.create_session = AsyncMock(return_value={
        "session_id": "sess_1", "status": "generating", "cards": [], "card_count": 3,
    })
    with patch.object(api_mod, "_service", mock_svc):
        resp = client.post("/api/video-gen/sessions", json={
            "scene_id": "product_showcase",
            "product_image_fid": "file_1",
            "copywriting": "假睫毛",
            "card_count": 3,
        })
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["session_id"] == "sess_1"
    mock_svc.create_session.assert_awaited_once()
    # 验证默认值：enable_ai_label=True, duration_sec=10
    kwargs = mock_svc.create_session.call_args.kwargs
    assert kwargs["enable_ai_label"] is True
    assert kwargs["duration_sec"] == 10


def test_create_session_with_label_and_duration(client):
    """前端传 enable_ai_label=False 和 duration_sec=15 应透传给 service。"""
    mock_svc = MagicMock()
    mock_svc.create_session = AsyncMock(return_value={
        "session_id": "sess_2", "status": "generating", "cards": [], "card_count": 2,
    })
    with patch.object(api_mod, "_service", mock_svc):
        resp = client.post("/api/video-gen/sessions", json={
            "scene_id": "product_showcase",
            "product_image_fid": "file_1",
            "copywriting": "假睫毛",
            "card_count": 2,
            "enable_ai_label": False,
            "duration_sec": 15,
        })
    assert resp.json()["success"] is True
    kwargs = mock_svc.create_session.call_args.kwargs
    assert kwargs["enable_ai_label"] is False
    assert kwargs["duration_sec"] == 15


def test_create_session_invalid_duration(client):
    """duration_sec=20 应触发 ValueError（service 校验），返回 success=False。"""
    mock_svc = MagicMock()
    mock_svc.create_session = AsyncMock(side_effect=ValueError("duration_sec 必须为 5/10/15"))
    with patch.object(api_mod, "_service", mock_svc):
        resp = client.post("/api/video-gen/sessions", json={
            "scene_id": "product_showcase",
            "product_image_fid": "f",
            "copywriting": "x",
            "duration_sec": 20,
        })
    body = resp.json()
    assert body["success"] is False
    assert "duration_sec" in body["error"]


def test_create_session_value_error(client):
    mock_svc = MagicMock()
    mock_svc.create_session = AsyncMock(side_effect=ValueError("未知场景: bad"))
    with patch.object(api_mod, "_service", mock_svc):
        resp = client.post("/api/video-gen/sessions", json={
            "scene_id": "bad", "product_image_fid": "f", "copywriting": "x",
        })
    assert resp.status_code == 200
    assert resp.json()["success"] is False
    assert "未知场景" in resp.json()["error"]


def test_create_session_missing_param(client):
    mock_svc = MagicMock()
    with patch.object(api_mod, "_service", mock_svc):
        resp = client.post("/api/video-gen/sessions", json={"scene_id": "product_showcase"})
    assert resp.json()["success"] is False
    assert "缺少" in resp.json()["error"]


def test_list_sessions(client):
    mock_svc = MagicMock()
    mock_svc.list_sessions.return_value = [{"session_id": "sess_1"}]
    with patch.object(api_mod, "_service", mock_svc):
        resp = client.get("/api/video-gen/sessions?limit=5")
    assert resp.json()["success"] is True
    mock_svc.list_sessions.assert_called_once()
    assert mock_svc.list_sessions.call_args[1]["limit"] == 5


def test_get_session_found(client):
    mock_svc = MagicMock()
    mock_svc.get_session.return_value = {"session_id": "sess_1", "cards": []}
    with patch.object(api_mod, "_service", mock_svc):
        resp = client.get("/api/video-gen/sessions/sess_1")
    assert resp.json()["success"] is True


def test_get_session_not_found(client):
    mock_svc = MagicMock()
    mock_svc.get_session.return_value = None
    with patch.object(api_mod, "_service", mock_svc):
        resp = client.get("/api/video-gen/sessions/sess_x")
    assert resp.json()["success"] is False


def test_set_card_kept(client):
    mock_svc = MagicMock()
    mock_svc.set_card_kept.return_value = {"card_id": "c1", "kept": True}
    with patch.object(api_mod, "_service", mock_svc):
        resp = client.patch("/api/video-gen/cards/c1/kept", json={"kept": True})
    assert resp.json()["success"] is True
    mock_svc.set_card_kept.assert_called_once()


def test_regenerate(client):
    mock_svc = MagicMock()
    mock_svc.regenerate_card = AsyncMock(return_value={"card_id": "c2", "parent_card_id": "c1"})
    with patch.object(api_mod, "_service", mock_svc):
        resp = client.post("/api/video-gen/cards/c1/regenerate", json={"prompt_override": "new"})
    assert resp.json()["success"] is True
    assert resp.json()["data"]["card_id"] == "c2"


def test_download_url_ready(client):
    mock_svc = MagicMock()
    mock_svc.get_card_output_fid.return_value = "file_out1"
    with patch.object(api_mod, "_service", mock_svc), \
         patch.object(api_mod, "settings") as mock_settings:
        mock_settings.app.public_base_url = "https://x.cn"
        resp = client.get("/api/video-gen/cards/c1/download-url")
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["file_id"] == "file_out1"
    assert "file_out1" in body["data"]["download_url"]


def test_download_url_not_ready(client):
    mock_svc = MagicMock()
    mock_svc.get_card_output_fid.return_value = None
    with patch.object(api_mod, "_service", mock_svc):
        resp = client.get("/api/video-gen/cards/c1/download-url")
    assert resp.json()["success"] is False
