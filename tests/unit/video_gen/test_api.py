"""视频生成 API 路由单元测试（FastAPI TestClient，mock service）。

验证 8 个端点的响应契约 {success,data,error}：
- GET /options 当前 provider 选项（resolutions/ratios/durations + 默认值 + 能力声明）
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


def _wanx_options_dict():
    """与 WanxProvider.get_options 对齐的 dict（dataclasses.asdict 输出）。"""
    return {
        "provider": "wanx",
        "resolutions": [
            {"value": "720P", "label": "720P", "price_per_sec": 0.6},
            {"value": "1080P", "label": "1080P", "price_per_sec": 1.0},
        ],
        "ratios": [
            {"value": "9:16", "label": "9:16", "price_per_sec": None},
            {"value": "16:9", "label": "16:9", "price_per_sec": None},
            {"value": "1:1",  "label": "1:1",  "price_per_sec": None},
            {"value": "4:3",  "label": "4:3",  "price_per_sec": None},
            {"value": "3:4",  "label": "3:4",  "price_per_sec": None},
        ],
        "durations": [
            {"value": "5",  "label": "5s",  "price_per_sec": None},
            {"value": "10", "label": "10s", "price_per_sec": None},
            {"value": "15", "label": "15s", "price_per_sec": None},
        ],
        "default_resolution": "720P",
        "default_ratio": "9:16",
        "default_duration": 5,
        "supports_reference_image": True,
        "supports_negative_prompt": True,
        "task_max_age_hours": 24,
    }


def _minimax_options_dict():
    """与 MiniMaxProvider.get_options 对齐的 dict。"""
    return {
        "provider": "minimax",
        "resolutions": [
            {"value": "768P", "label": "768P", "price_per_sec": 0.5},
            {"value": "2K",   "label": "2K",   "price_per_sec": 0.8},
        ],
        "ratios": [
            {"value": "9:16",     "label": "9:16",     "price_per_sec": None},
            {"value": "16:9",     "label": "16:9",     "price_per_sec": None},
            {"value": "1:1",      "label": "1:1",      "price_per_sec": None},
            {"value": "4:3",      "label": "4:3",      "price_per_sec": None},
            {"value": "3:4",      "label": "3:4",      "price_per_sec": None},
            {"value": "21:9",     "label": "21:9",     "price_per_sec": None},
            {"value": "adaptive", "label": "adaptive", "price_per_sec": None},
        ],
        "durations": [
            {"value": "5",  "label": "5s",  "price_per_sec": None},
            {"value": "10", "label": "10s", "price_per_sec": None},
            {"value": "15", "label": "15s", "price_per_sec": None},
        ],
        "default_resolution": "768P",
        "default_ratio": "9:16",
        "default_duration": 5,
        "supports_reference_image": True,
        "supports_negative_prompt": False,
        "task_max_age_hours": 168,
    }


def test_get_options_returns_wanx(client):
    """VIDEO_GEN_PROVIDER=wanx 时 /options 应返回万相选项。"""
    from src.video_gen.base import ProviderOptions
    opts = ProviderOptions(
        provider="wanx",
        resolutions=[],
        ratios=[],
        durations=[],
        default_resolution="720P", default_ratio="9:16", default_duration=5,
        supports_reference_image=True, supports_negative_prompt=True,
        task_max_age_hours=24,
    )
    mock_svc = MagicMock()
    mock_svc.get_options.return_value = opts
    with patch.object(api_mod, "_service", mock_svc):
        resp = client.get("/api/video-gen/options")
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["provider"] == "wanx"
    assert body["data"]["default_resolution"] == "720P"
    assert body["data"]["task_max_age_hours"] == 24
    assert body["data"]["supports_negative_prompt"] is True


def test_get_options_returns_minimax(client):
    """VIDEO_GEN_PROVIDER=minimax 时 /options 应返回 MiniMax 选项。"""
    from src.video_gen.base import ProviderOptions
    opts = ProviderOptions(
        provider="minimax",
        resolutions=[],
        ratios=[],
        durations=[],
        default_resolution="768P", default_ratio="9:16", default_duration=5,
        supports_reference_image=True, supports_negative_prompt=False,
        task_max_age_hours=168,
    )
    mock_svc = MagicMock()
    mock_svc.get_options.return_value = opts
    with patch.object(api_mod, "_service", mock_svc):
        resp = client.get("/api/video-gen/options")
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["provider"] == "minimax"
    assert body["data"]["default_resolution"] == "768P"
    assert body["data"]["task_max_age_hours"] == 168
    assert body["data"]["supports_negative_prompt"] is False


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
    # 验证默认值：enable_ai_label=True, duration_sec=5（cee67101 为省成本 10→5）
    kwargs = mock_svc.create_session.call_args.kwargs
    assert kwargs["enable_ai_label"] is True
    assert kwargs["duration_sec"] == 5


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
