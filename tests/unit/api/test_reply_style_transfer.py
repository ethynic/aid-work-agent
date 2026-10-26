"""系统回复风格导出/导入 API 单元测试：导出结构 / 冲突策略 / 统计计数。"""
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.saas.api import reply_styles as api_mod


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(api_mod.router)
    monkeypatch.setattr(api_mod, "_require_platform_admin", lambda request: {"user_id": "admin1"})
    monkeypatch.setattr(api_mod, "_trigger_reload", lambda: None)
    yield TestClient(app, raise_server_exceptions=False)


def _style(style_id: str, name: str) -> dict:
    return {
        "style_id": style_id, "name": name, "description": f"{name}描述",
        "content": f"## {name} 指南", "version": 2, "is_active": True,
        "tenant_id": "system", "created_at": None, "updated_at": None,
    }


def _payload(**overrides):
    payload = {
        "format": "reply_style_export",
        "version": 1,
        "styles": [
            {"style_id": "professional", "name": "专业助手", "description": None, "content": "内容A"},
            {"style_id": "concise", "name": "简洁助手", "description": "简", "content": "内容B"},
        ],
    }
    payload.update(overrides)
    return payload


# ============== 导出 ==============

class TestExport:
    def test_export_all_system_styles(self, client, monkeypatch):
        monkeypatch.setattr(
            api_mod.ReplyStyleDB, "list_system_styles",
            staticmethod(lambda: [_style("a", "风格A"), _style("b", "风格B")]),
        )
        res = client.get("/api/saas/reply-styles/system/export")
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["format"] == "reply_style_export"
        assert data["version"] == 1
        assert len(data["styles"]) == 2
        assert data["styles"][0]["style_id"] == "a"
        assert data["styles"][0]["content"] == "## 风格A 指南"
        # 无 tenant_id/version 等环境相关字段
        assert "tenant_id" not in data["styles"][0]


# ============== 导入 ==============

class TestImport:
    def test_import_bad_format(self, client):
        res = client.post("/api/saas/reply-styles/system/import", json=_payload(format="other"))
        assert res.status_code == 400

    def test_import_too_new_version(self, client):
        res = client.post("/api/saas/reply-styles/system/import", json=_payload(version=99))
        assert res.status_code == 400

    def test_import_empty_styles(self, client):
        res = client.post("/api/saas/reply-styles/system/import", json=_payload(styles=[]))
        assert res.status_code == 400

    def test_import_creates_all_new(self, client, monkeypatch):
        monkeypatch.setattr(
            api_mod.ReplyStyleDB, "get_active", staticmethod(lambda sid, tid: None),
        )
        create_mock = MagicMock(return_value=_style("a", "风格A"))
        monkeypatch.setattr(api_mod.ReplyStyleDB, "create", create_mock)

        res = client.post("/api/saas/reply-styles/system/import", json=_payload())
        assert res.status_code == 200
        body = res.json()
        assert body["created"] == 2 and body["updated"] == 0 and body["skipped"] == 0
        assert create_mock.call_count == 2
        kwargs = create_mock.call_args.kwargs
        assert kwargs["tenant_id"] == "system"
        assert kwargs["style_id"] == "concise"

    def test_import_conflict_without_strategy_returns_409(self, client, monkeypatch):
        existing_ids = {"professional"}
        monkeypatch.setattr(
            api_mod.ReplyStyleDB, "get_active",
            staticmethod(lambda sid, tid: _style(sid, sid) if sid in existing_ids else None),
        )
        monkeypatch.setattr(
            api_mod.ReplyStyleDB, "list_system_styles",
            staticmethod(lambda: [_style("professional", "已有风格")]),
        )

        res = client.post("/api/saas/reply-styles/system/import", json=_payload())
        assert res.status_code == 409
        detail = res.json()["detail"]
        assert detail["conflicts"] == [{"style_id": "professional", "name": "已有风格"}]

    def test_import_overwrite_creates_new_version(self, client, monkeypatch):
        existing_ids = {"professional"}
        monkeypatch.setattr(
            api_mod.ReplyStyleDB, "get_active",
            staticmethod(lambda sid, tid: _style(sid, sid) if sid in existing_ids else None),
        )
        version_mock = MagicMock(return_value=_style("professional", "专业助手"))
        create_mock = MagicMock(return_value=_style("concise", "简洁助手"))
        monkeypatch.setattr(api_mod.ReplyStyleDB, "create_new_version", version_mock)
        monkeypatch.setattr(api_mod.ReplyStyleDB, "create", create_mock)

        res = client.post(
            "/api/saas/reply-styles/system/import",
            json=_payload(conflict_strategy="overwrite"),
        )
        assert res.status_code == 200
        body = res.json()
        assert body["created"] == 1 and body["updated"] == 1
        kwargs = version_mock.call_args.kwargs
        assert kwargs["style_id"] == "professional"
        assert kwargs["content"] == "内容A"

    def test_import_skip_conflicts(self, client, monkeypatch):
        existing_ids = {"professional"}
        monkeypatch.setattr(
            api_mod.ReplyStyleDB, "get_active",
            staticmethod(lambda sid, tid: _style(sid, sid) if sid in existing_ids else None),
        )
        create_mock = MagicMock(return_value=_style("concise", "简洁助手"))
        monkeypatch.setattr(api_mod.ReplyStyleDB, "create", create_mock)
        version_mock = MagicMock()
        monkeypatch.setattr(api_mod.ReplyStyleDB, "create_new_version", version_mock)

        res = client.post(
            "/api/saas/reply-styles/system/import",
            json=_payload(conflict_strategy="skip"),
        )
        assert res.status_code == 200
        body = res.json()
        assert body["created"] == 1 and body["skipped"] == 1 and body["updated"] == 0
        version_mock.assert_not_called()

    def test_import_invalid_item_missing_content(self, client):
        payload = _payload()
        payload["styles"][0]["content"] = ""
        res = client.post("/api/saas/reply-styles/system/import", json=payload)
        assert res.status_code == 422
