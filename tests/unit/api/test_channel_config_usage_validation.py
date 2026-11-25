"""企微应用渠道「通知用途」分支必填校验 API 测试（渠道合并 2026-11-15）：

- create：notify_only 仅 corp_id/agent_id/secret 三项必填（token/encoding_aes_key
  免填）；agent_only（含缺省）保持原 5 项必填（存量行为不变）；notify_usage
  非法值 400
- update：同样按用途分支（notify_only 免填回调字段；agent_only 缺 token 拒）
- update 切到 notify_only：表单已隐藏、未提交的回调字段保留库内旧值（不随整体
  覆盖清除），改回 agent_only/both 时无需重填；显式提交的回调字段以提交值为准

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


def _capture_update(monkeypatch):
    """替换 ChannelConfigDB.update 并返回调用记录 [(位置参数, 关键字参数)]，
    用于断言实际落库的 config。"""
    calls = []
    monkeypatch.setattr(
        cc.ChannelConfigDB, "update",
        staticmethod(lambda *a, **k: calls.append((a, k)) or True),
    )
    return calls


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

    @pytest.mark.parametrize("blank_callback", [{}, {"token": "", "encoding_aes_key": ""}])
    def test_update_to_notify_only_retains_stored_callback_fields(
        self, client, monkeypatch, blank_callback
    ):
        """切到 notify_only：未提交（或提交空串）的 token/encoding_aes_key 保留库内
        旧值而非随整体覆盖清除——改回 agent_only/both 时无需重填。"""
        monkeypatch.setattr(
            cc.ChannelConfigDB, "get_by_id",
            staticmethod(lambda config_id: self._existing(dict(_WECOM_FULL))))
        calls = _capture_update(monkeypatch)
        monkeypatch.setattr(cc.ChannelFactory, "invalidate_adapter", AsyncMock())
        resp = client.put(
            "/api/saas/channels/chan_w1",
            json={"name": "x", "config": {**_WECOM_NOTIFY_ONLY, **blank_callback}},
        )
        assert resp.status_code == 200
        saved = calls[0][0][1]
        assert saved["notify_usage"] == "notify_only"
        assert saved["token"] == _WECOM_FULL["token"]
        assert saved["encoding_aes_key"] == _WECOM_FULL["encoding_aes_key"]

    def test_update_to_notify_only_with_submitted_callback_fields_keeps_submitted(
        self, client, monkeypatch
    ):
        """切到 notify_only 且显式提交了回调字段：以提交值为准，不被库内旧值覆盖。"""
        monkeypatch.setattr(
            cc.ChannelConfigDB, "get_by_id",
            staticmethod(lambda config_id: self._existing(dict(_WECOM_FULL))))
        calls = _capture_update(monkeypatch)
        monkeypatch.setattr(cc.ChannelFactory, "invalidate_adapter", AsyncMock())
        resp = client.put(
            "/api/saas/channels/chan_w1",
            json={"name": "x", "config": {**_WECOM_NOTIFY_ONLY,
                                           "token": "new-token", "encoding_aes_key": "new-aes"}},
        )
        assert resp.status_code == 200
        saved = calls[0][0][1]
        assert saved["token"] == "new-token"
        assert saved["encoding_aes_key"] == "new-aes"

    def test_update_to_notify_only_without_stored_callback_fields_adds_none(
        self, client, monkeypatch
    ):
        """库内本无回调字段（直接以 notify_only 新建的配置）：不凭空补键。"""
        monkeypatch.setattr(
            cc.ChannelConfigDB, "get_by_id",
            staticmethod(lambda config_id: self._existing(dict(_WECOM_NOTIFY_ONLY))))
        calls = _capture_update(monkeypatch)
        monkeypatch.setattr(cc.ChannelFactory, "invalidate_adapter", AsyncMock())
        resp = client.put(
            "/api/saas/channels/chan_w1",
            json={"name": "x", "config": _WECOM_NOTIFY_ONLY},
        )
        assert resp.status_code == 200
        saved = calls[0][0][1]
        assert "token" not in saved
        assert "encoding_aes_key" not in saved

    def test_update_agent_only_missing_token_rejected(self, client, monkeypatch):
        """update 保持 agent_only：缺 token 拒（回调字段保留仅限 notify_only 用途）。"""
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


# ============== 飞书应用「通知用途」分支（飞书通知接入 2026-11-15，同模式） ==============

_FEISHU_FULL = {
    "app_id": "cli_a1",
    "app_secret": "feishu-secret",
    "verification_token": "verify-token",
}

_FEISHU_NOTIFY_ONLY = {"app_id": "cli_a1", "app_secret": "feishu-secret",
                       "notify_usage": "notify_only"}


class TestCreateFeishuUsageBranch:
    def _fake_create(self, monkeypatch, created):
        def fake_create(**kwargs):
            created.update(kwargs)
            return {"config_id": "chan_new", "tenant_id": _ADMIN["tenant_id"],
                    "channel_type": kwargs["channel_type"], "config": kwargs["config"],
                    "verified": 0}
        monkeypatch.setattr(cc.ChannelConfigDB, "create", staticmethod(fake_create))

    def test_notify_only_two_fields_accepted(self, client, monkeypatch):
        """飞书 notify_only：仅 app_id/app_secret 两项必填，verification_token 免填。"""
        created = {}
        self._fake_create(monkeypatch, created)
        resp = client.post(
            "/api/saas/channels",
            json={"channel_type": "feishu", "name": "飞书应用", "config": _FEISHU_NOTIFY_ONLY},
        )
        assert resp.status_code == 200
        assert created["config"]["notify_usage"] == "notify_only"

    def test_notify_only_missing_app_secret_rejected(self, client):
        """notify_only：app_secret 缺失仍拒（两项必填不受放宽影响）。"""
        resp = client.post(
            "/api/saas/channels",
            json={"channel_type": "feishu", "name": "x",
                  "config": {"app_id": "cli_a1", "notify_usage": "notify_only"}},
        )
        assert resp.status_code == 400
        assert "app_secret" in resp.json()["detail"]

    @pytest.mark.parametrize("usage", [None, "agent_only", "both"])
    def test_agent_or_both_keep_original_required(self, client, usage):
        """agent_only（含缺省）/both：保持原必填（app_id/app_secret/
        verification_token；encrypt_key 本就可选，存量行为不变）。"""
        config = dict(_FEISHU_FULL)
        if usage is not None:
            config["notify_usage"] = usage
        del config["verification_token"]
        resp = client.post(
            "/api/saas/channels",
            json={"channel_type": "feishu", "name": "x", "config": config},
        )
        assert resp.status_code == 400
        assert "verification_token" in resp.json()["detail"]

    def test_agent_only_missing_secret_rejected(self, client):
        """缺省用途（agent_only）：app_secret 缺失照常拒（存量行为不变）。"""
        resp = client.post(
            "/api/saas/channels",
            json={"channel_type": "feishu", "name": "x",
                  "config": {"app_id": "cli_a1", "verification_token": "vt"}},
        )
        assert resp.status_code == 400
        assert "app_secret" in resp.json()["detail"]

    def test_invalid_usage_rejected(self, client):
        """飞书 notify_usage 非法值 400（不静默纠正）。"""
        resp = client.post(
            "/api/saas/channels",
            json={"channel_type": "feishu", "name": "x",
                  "config": {**_FEISHU_FULL, "notify_usage": "notification"}},
        )
        assert resp.status_code == 400
        assert "通知用途" in resp.json()["detail"]


class TestUpdateFeishuUsageBranch:
    def _existing(self, config):
        return {"config_id": "chan_f1", "tenant_id": _ADMIN["tenant_id"],
                "channel_type": "feishu", "config": config, "verified": 1}

    def test_update_to_notify_only_without_callback_fields_accepted(self, client, monkeypatch):
        """update 切到 notify_only：免填 verification_token。"""
        monkeypatch.setattr(
            cc.ChannelConfigDB, "get_by_id",
            staticmethod(lambda config_id: self._existing(dict(_FEISHU_FULL))))
        monkeypatch.setattr(cc.ChannelConfigDB, "update", staticmethod(lambda *a, **k: True))
        invalidate = AsyncMock()
        monkeypatch.setattr(cc.ChannelFactory, "invalidate_adapter", invalidate)
        resp = client.put(
            "/api/saas/channels/chan_f1",
            json={"name": "x", "config": _FEISHU_NOTIFY_ONLY},
        )
        assert resp.status_code == 200

    def test_update_to_notify_only_retains_stored_callback_fields(self, client, monkeypatch):
        """切到 notify_only：未提交的 verification_token 与可选 encrypt_key
        均保留库内旧值（改回 agent_only/both 时无需重填）。"""
        stored = {**_FEISHU_FULL, "encrypt_key": "encrypt-key"}
        monkeypatch.setattr(
            cc.ChannelConfigDB, "get_by_id",
            staticmethod(lambda config_id: self._existing(dict(stored))))
        calls = _capture_update(monkeypatch)
        monkeypatch.setattr(cc.ChannelFactory, "invalidate_adapter", AsyncMock())
        resp = client.put(
            "/api/saas/channels/chan_f1",
            json={"name": "x", "config": _FEISHU_NOTIFY_ONLY},
        )
        assert resp.status_code == 200
        saved = calls[0][0][1]
        assert saved["notify_usage"] == "notify_only"
        assert saved["verification_token"] == stored["verification_token"]
        assert saved["encrypt_key"] == "encrypt-key"

    def test_update_agent_only_missing_verification_token_rejected(self, client, monkeypatch):
        """update 保持 agent_only：缺 verification_token 拒（存量行为不变）。"""
        monkeypatch.setattr(
            cc.ChannelConfigDB, "get_by_id",
            staticmethod(lambda config_id: self._existing(dict(_FEISHU_FULL))))
        resp = client.put(
            "/api/saas/channels/chan_f1",
            json={"name": "x", "config": {"app_id": "cli_a1", "app_secret": "s"}},
        )
        assert resp.status_code == 400
        assert "verification_token" in resp.json()["detail"]

    def test_update_invalid_usage_rejected(self, client, monkeypatch):
        monkeypatch.setattr(
            cc.ChannelConfigDB, "get_by_id",
            staticmethod(lambda config_id: self._existing(dict(_FEISHU_FULL))))
        resp = client.put(
            "/api/saas/channels/chan_f1",
            json={"name": "x", "config": {**_FEISHU_FULL, "notify_usage": "notify"}},
        )
        assert resp.status_code == 400
        assert "通知用途" in resp.json()["detail"]


# ============== 钉钉应用「通知用途」分支（钉钉通知接入 2026-11-16，同模式） ==============

_DINGTALK_FULL = {
    "app_key": "ding-key",
    "app_secret": "ding-secret",
    "token": "callback-token",
    "encoding_aes_key": "aes-key",
}

_DINGTALK_NOTIFY_ONLY = {"app_key": "ding-key", "app_secret": "ding-secret",
                         "agent_id": "1000002", "notify_usage": "notify_only"}


class TestCreateDingtalkUsageBranch:
    def _fake_create(self, monkeypatch, created):
        def fake_create(**kwargs):
            created.update(kwargs)
            return {"config_id": "chan_new", "tenant_id": _ADMIN["tenant_id"],
                    "channel_type": kwargs["channel_type"], "config": kwargs["config"],
                    "verified": 0}
        monkeypatch.setattr(cc.ChannelConfigDB, "create", staticmethod(fake_create))

    def test_notify_only_three_fields_accepted(self, client, monkeypatch):
        """钉钉 notify_only：app_key/app_secret/agent_id 三项必填，
        token/encoding_aes_key 免填。"""
        created = {}
        self._fake_create(monkeypatch, created)
        resp = client.post(
            "/api/saas/channels",
            json={"channel_type": "dingtalk", "name": "钉钉通知应用", "config": _DINGTALK_NOTIFY_ONLY},
        )
        assert resp.status_code == 200
        assert created["config"]["notify_usage"] == "notify_only"

    def test_notify_only_missing_agent_id_rejected(self, client):
        """notify_only：agent_id 缺失仍拒（工作通知发送必须）。"""
        resp = client.post(
            "/api/saas/channels",
            json={"channel_type": "dingtalk", "name": "x",
                  "config": {"app_key": "ding-key", "app_secret": "s",
                             "notify_usage": "notify_only"}},
        )
        assert resp.status_code == 400
        assert "agent_id" in resp.json()["detail"]

    @pytest.mark.parametrize("usage", [None, "agent_only", "both"])
    def test_agent_or_both_keep_original_required(self, client, usage):
        """agent_only（含缺省）/both：保持原必填（app_key/app_secret/token/
        encoding_aes_key，存量行为不变）。"""
        config = dict(_DINGTALK_FULL)
        if usage is not None:
            config["notify_usage"] = usage
        del config["token"]
        resp = client.post(
            "/api/saas/channels",
            json={"channel_type": "dingtalk", "name": "x", "config": config},
        )
        assert resp.status_code == 400
        assert "token" in resp.json()["detail"]

    def test_agent_only_missing_secret_rejected(self, client):
        """缺省用途（agent_only）：app_secret 缺失照常拒（存量行为不变）。"""
        resp = client.post(
            "/api/saas/channels",
            json={"channel_type": "dingtalk", "name": "x",
                  "config": {"app_key": "ding-key", "token": "t", "encoding_aes_key": "a"}},
        )
        assert resp.status_code == 400
        assert "app_secret" in resp.json()["detail"]

    def test_invalid_usage_rejected(self, client):
        """钉钉 notify_usage 非法值 400（不静默纠正）。"""
        resp = client.post(
            "/api/saas/channels",
            json={"channel_type": "dingtalk", "name": "x",
                  "config": {**_DINGTALK_FULL, "notify_usage": "notification"}},
        )
        assert resp.status_code == 400
        assert "通知用途" in resp.json()["detail"]


class TestUpdateDingtalkUsageBranch:
    def _existing(self, config):
        return {"config_id": "chan_d1", "tenant_id": _ADMIN["tenant_id"],
                "channel_type": "dingtalk", "config": config, "verified": 1}

    def test_update_to_notify_only_without_callback_fields_accepted(self, client, monkeypatch):
        """update 切到 notify_only：免填 token/encoding_aes_key（agent_id 必填）。"""
        monkeypatch.setattr(
            cc.ChannelConfigDB, "get_by_id",
            staticmethod(lambda config_id: self._existing(dict(_DINGTALK_FULL))))
        monkeypatch.setattr(cc.ChannelConfigDB, "update", staticmethod(lambda *a, **k: True))
        invalidate = AsyncMock()
        monkeypatch.setattr(cc.ChannelFactory, "invalidate_adapter", invalidate)
        resp = client.put(
            "/api/saas/channels/chan_d1",
            json={"name": "x", "config": _DINGTALK_NOTIFY_ONLY},
        )
        assert resp.status_code == 200

    def test_update_to_notify_only_retains_stored_callback_fields(self, client, monkeypatch):
        """切到 notify_only：未提交的 token/encoding_aes_key 保留库内旧值
        （改回 agent_only/both 时无需重填）。"""
        monkeypatch.setattr(
            cc.ChannelConfigDB, "get_by_id",
            staticmethod(lambda config_id: self._existing(dict(_DINGTALK_FULL))))
        calls = _capture_update(monkeypatch)
        monkeypatch.setattr(cc.ChannelFactory, "invalidate_adapter", AsyncMock())
        resp = client.put(
            "/api/saas/channels/chan_d1",
            json={"name": "x", "config": _DINGTALK_NOTIFY_ONLY},
        )
        assert resp.status_code == 200
        saved = calls[0][0][1]
        assert saved["notify_usage"] == "notify_only"
        assert saved["token"] == _DINGTALK_FULL["token"]
        assert saved["encoding_aes_key"] == _DINGTALK_FULL["encoding_aes_key"]

    def test_update_agent_only_missing_token_rejected(self, client, monkeypatch):
        """update 保持 agent_only：缺 token 拒（存量行为不变）。"""
        monkeypatch.setattr(
            cc.ChannelConfigDB, "get_by_id",
            staticmethod(lambda config_id: self._existing(dict(_DINGTALK_FULL))))
        resp = client.put(
            "/api/saas/channels/chan_d1",
            json={"name": "x",
                  "config": {"app_key": "ding-key", "app_secret": "s"}},
        )
        assert resp.status_code == 400
        assert "token" in resp.json()["detail"]

    def test_update_invalid_usage_rejected(self, client, monkeypatch):
        monkeypatch.setattr(
            cc.ChannelConfigDB, "get_by_id",
            staticmethod(lambda config_id: self._existing(dict(_DINGTALK_FULL))))
        resp = client.put(
            "/api/saas/channels/chan_d1",
            json={"name": "x", "config": {**_DINGTALK_FULL, "notify_usage": "notify"}},
        )
        assert resp.status_code == 400
        assert "通知用途" in resp.json()["detail"]
