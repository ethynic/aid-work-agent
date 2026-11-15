"""企微应用渠道「通知用途」分支必填校验 API 测试（渠道合并 2026-11-15）：

- create：notify_only 仅 corp_id/agent_id/secret 三项必填（token/encoding_aes_key
  免填）；agent_only（含缺省）保持原 5 项必填（存量行为不变）；notify_usage
  非法值 400
- update：同样按用途分支（notify_only 免填回调字段；agent_only 缺 token 拒）

参照 tests/unit/api 现有路由测试手法：最小 app + monkeypatch require_admin +
patch DB 与行为审计。
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

from src.saas.api import channel_config as cc

_ADMIN = {"user_id": "admin_1", "tenant_id": "tenant_1", "role": "tenant_admin"}

_WECOM_FULL = {
    "corp_id": "ww123",
    "agent_id": "1000002",
    "secret": "app-secret",
    "token": "callback-token",
    "encoding_aes_key": "aes-key-43-characters-base64-xxxxxxx",
}

_WECOM_NOTIFY_ONLY = {"corp_id": "ww123", "agent_id": "1000002", "secret": "app-secret",
                      "notify_usage": "notify_only"}


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(cc.router)
    monkeypatch.setattr(cc, "require_admin", lambda request: dict(_ADMIN))
    return TestClient(app, raise_server_exceptions=False)


class TestCreateUsageBranch:
    def test_notify_only_three_fields_accepted(self, client, monkeypatch):
        """notify_only：仅 3 项必填，token/encoding_aes_key 缺省通过。"""
        created = {}

        def fake_create(**kwargs):
            created.update(kwargs)
            return {"config_id": "chan_new", "tenant_id": _ADMIN["tenant_id"],
                    "channel_type": kwargs["channel_type"], "config": kwargs["config"],
                    "verified": 0}

        monkeypatch.setattr(cc.ChannelConfigDB, "create", staticmethod(fake_create))
        resp = client.post(
            "/api/saas/channels",
            json={"channel_type": "wecom", "name": "通知应用", "config": _WECOM_NOTIFY_ONLY},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        # notify_usage 原样入库
        assert created["config"]["notify_usage"] == "notify_only"

    def test_notify_only_missing_corp_id_rejected(self, client, monkeypatch):
        """notify_only：corp_id 缺失仍拒（3 项必填不受放宽影响）。"""
        resp = client.post(
            "/api/saas/channels",
            json={"channel_type": "wecom", "name": "x",
                  "config": {"agent_id": "1000002", "secret": "s", "notify_usage": "notify_only"}},
        )
        assert resp.status_code == 400
        assert "corp_id" in resp.json()["detail"]

    @pytest.mark.parametrize("usage", [None, "agent_only", "both"])
    def test_agent_or_both_keep_original_required(self, client, monkeypatch, usage):
        """agent_only（含缺省）/both：保持原 5 项必填（存量行为不变）。"""
        config = dict(_WECOM_FULL)
        if usage is not None:
            config["notify_usage"] = usage
        del config["token"]
        resp = client.post(
            "/api/saas/channels",
            json={"channel_type": "wecom", "name": "x", "config": config},
        )
        assert resp.status_code == 400
        assert "token" in resp.json()["detail"]

    def test_both_full_fields_accepted(self, client, monkeypatch):
        """both：5 项齐全通过。"""
        config = dict(_WECOM_FULL)
        config["notify_usage"] = "both"
        monkeypatch.setattr(
            cc.ChannelConfigDB, "create",
            staticmethod(lambda **kwargs: {"config_id": "chan_new", "tenant_id": _ADMIN["tenant_id"],
                                           "channel_type": kwargs["channel_type"],
                                           "config": kwargs["config"], "verified": 0}))
        resp = client.post(
            "/api/saas/channels",
            json={"channel_type": "wecom", "name": "x", "config": config},
        )
        assert resp.status_code == 200

    def test_invalid_usage_rejected(self, client, monkeypatch):
        """notify_usage 非法值 400（不静默纠正）。"""
        resp = client.post(
            "/api/saas/channels",
            json={"channel_type": "wecom", "name": "x",
                  "config": {**_WECOM_FULL, "notify_usage": "notify"}},
        )
        assert resp.status_code == 400
        assert "通知用途" in resp.json()["detail"]


class TestUpdateUsageBranch:
    def _existing(self, config):
        return {"config_id": "chan_w1", "tenant_id": _ADMIN["tenant_id"],
                "channel_type": "wecom", "config": config, "verified": 1}

    def test_update_to_notify_only_without_callback_fields_accepted(self, client, monkeypatch):
        """update 切到 notify_only：免填回调 token/aes_key。"""
        monkeypatch.setattr(
            cc.ChannelConfigDB, "get_by_id",
            staticmethod(lambda config_id: self._existing(dict(_WECOM_FULL))))
        monkeypatch.setattr(cc.ChannelConfigDB, "update", staticmethod(lambda *a, **k: True))
        invalidate = AsyncMock()
        monkeypatch.setattr(cc.ChannelFactory, "invalidate_adapter", invalidate)
        resp = client.put(
            "/api/saas/channels/chan_w1",
            json={"name": "x", "config": _WECOM_NOTIFY_ONLY},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_update_agent_only_missing_token_rejected(self, client, monkeypatch):
        """update 保持 agent_only：缺 token 拒（wecom 类型 update 不做字段保留合并）。"""
        monkeypatch.setattr(
            cc.ChannelConfigDB, "get_by_id",
            staticmethod(lambda config_id: self._existing(dict(_WECOM_FULL))))
        resp = client.put(
            "/api/saas/channels/chan_w1",
            json={"name": "x",
                  "config": {"corp_id": "ww123", "agent_id": "1000002", "secret": "s"}},
        )
        assert resp.status_code == 400
        assert "token" in resp.json()["detail"]

    def test_update_invalid_usage_rejected(self, client, monkeypatch):
        monkeypatch.setattr(
            cc.ChannelConfigDB, "get_by_id",
            staticmethod(lambda config_id: self._existing(dict(_WECOM_FULL))))
        resp = client.put(
            "/api/saas/channels/chan_w1",
            json={"name": "x", "config": {**_WECOM_FULL, "notify_usage": "notification"}},
        )
        assert resp.status_code == 400
        assert "通知用途" in resp.json()["detail"]

    def test_update_missing_rejected_before_usage_branch(self, client, monkeypatch):
        monkeypatch.setattr(cc.ChannelConfigDB, "get_by_id", staticmethod(lambda config_id: None))
        resp = client.put(
            "/api/saas/channels/chan_none",
            json={"name": "x", "config": _WECOM_NOTIFY_ONLY},
        )
        assert resp.status_code == 404
