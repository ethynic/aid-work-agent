"""AI获客联系人手机号：列表掩码 / 显码端点审计 / 掩码工具 单元测试（全 mock，无外部依赖）"""
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import ai_lead_gen as api_mod
from src.utils.masking import mask_phone
from src.saas.models.enums import BehaviorAction

pytestmark = pytest.mark.unit


# ============== mask_phone 工具 ==============

class TestMaskPhone:
    def test_11_digit_mainland(self):
        assert mask_phone("13812345678") == "138****5678"

    def test_international_number_masked(self):
        # 非 11 位海外号码也必须掩码，不得原样返回
        assert mask_phone("+12025550123") == "+12****23"
        assert mask_phone("442071234567") == "442****67"

    def test_short_number(self):
        assert mask_phone("12345") == "1****"
        assert mask_phone("1234") == "1****"

    def test_empty_and_none(self):
        assert mask_phone("") is None
        assert mask_phone(None) is None
        assert mask_phone("   ") is None


# ============== 枚举 ==============

class TestEnum:
    def test_phone_view_in_action_values(self):
        assert "phone_view" in BehaviorAction.all_values()
        assert BehaviorAction.PHONE_VIEW.value == "phone_view"

    def test_display_name(self):
        assert BehaviorAction.PHONE_VIEW.display_name == "查看手机号"


# ============== API 端点 ==============

class _FakeCursor:
    """fetchone/fetchall 按预设顺序出队（fetchall_results 每项为一次查询的行列表）"""

    def __init__(self, fetchone_results, fetchall_results):
        self._fetchone = list(fetchone_results)
        self._fetchall = list(fetchall_results)
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self._fetchone.pop(0) if self._fetchone else None

    def fetchall(self):
        return self._fetchall.pop(0) if self._fetchall else []


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(api_mod.router)
    yield TestClient(app, raise_server_exceptions=False)


def _patch_record_behavior(monkeypatch) -> AsyncMock:
    mock = AsyncMock(return_value=None)
    import src.services.behavior_log as bl_mod
    monkeypatch.setattr(bl_mod, "record_behavior", mock)
    return mock


class TestListContactsMask:
    def test_phone_masked_in_list(self, client, monkeypatch):
        cursor = _FakeCursor(
            fetchone_results=[{"cnt": 1}],
            fetchall_results=[[
                {"id": 1, "name": "张三", "email": "a@b.com",
                 "phone": "13812345678", "title": "CEO"},
            ]],
        )
        monkeypatch.setattr(api_mod, "get_db_connection", lambda: _FakeConn(cursor))
        res = client.get("/api/ai-lead-gen/contacts?tenant_id=t1")
        assert res.status_code == 200
        assert res.json()["success"] is True
        items = res.json()["data"]["items"]
        assert items[0]["phone"] == "138****5678"

    def test_international_phone_masked_in_list(self, client, monkeypatch):
        cursor = _FakeCursor(
            fetchone_results=[{"cnt": 1}],
            fetchall_results=[[{"id": 2, "name": "John", "phone": "+12025550123"}]],
        )
        monkeypatch.setattr(api_mod, "get_db_connection", lambda: _FakeConn(cursor))
        res = client.get("/api/ai-lead-gen/contacts?tenant_id=t1")
        assert res.json()["data"]["items"][0]["phone"] == "+12****23"


class TestRevealPhone:
    def test_reveal_success_and_audit(self, client, monkeypatch):
        cursor = _FakeCursor(
            fetchone_results=[{"id": 7, "name": "张三", "phone": "13812345678"}],
            fetchall_results=[],
        )
        monkeypatch.setattr(api_mod, "get_db_connection", lambda: _FakeConn(cursor))
        record_mock = _patch_record_behavior(monkeypatch)

        res = client.get("/api/ai-lead-gen/contacts/7/reveal_phone?tenant_id=t1")
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["phone"] == "13812345678"

        record_mock.assert_awaited_once()
        args, kwargs = record_mock.await_args
        # action 位置参数 = phone_view
        assert args[1] == "phone_view"
        assert kwargs["resource_type"] == "external_customer"
        assert kwargs["resource_id"] == "7"
        assert kwargs["resource_name"] == "张三"

    def test_cross_tenant_rejected_no_audit(self, client, monkeypatch):
        cursor = _FakeCursor(fetchone_results=[None], fetchall_results=[])
        monkeypatch.setattr(api_mod, "get_db_connection", lambda: _FakeConn(cursor))
        record_mock = _patch_record_behavior(monkeypatch)

        res = client.get("/api/ai-lead-gen/contacts/7/reveal_phone?tenant_id=t2")
        assert res.status_code == 200
        body = res.json()
        assert body["success"] is False
        assert "联系人不存在" in body["error"]
        record_mock.assert_not_awaited()

    def test_no_phone_no_audit(self, client, monkeypatch):
        cursor = _FakeCursor(
            fetchone_results=[{"id": 8, "name": "李四", "phone": None}],
            fetchall_results=[],
        )
        monkeypatch.setattr(api_mod, "get_db_connection", lambda: _FakeConn(cursor))
        record_mock = _patch_record_behavior(monkeypatch)

        res = client.get("/api/ai-lead-gen/contacts/8/reveal_phone?tenant_id=t1")
        assert res.status_code == 200
        assert res.json()["data"]["phone"] is None
        record_mock.assert_not_awaited()

    def test_missing_tenant_context(self, client, monkeypatch):
        cursor = _FakeCursor(fetchone_results=[], fetchall_results=[])
        monkeypatch.setattr(api_mod, "get_db_connection", lambda: _FakeConn(cursor))
        record_mock = _patch_record_behavior(monkeypatch)

        res = client.get("/api/ai-lead-gen/contacts/7/reveal_phone")
        assert res.status_code == 200
        assert res.json()["success"] is False
        assert "缺少租户上下文" in res.json()["error"]
        record_mock.assert_not_awaited()
