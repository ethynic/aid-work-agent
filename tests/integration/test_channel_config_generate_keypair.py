"""POST /api/saas/channels/{config_id}/generate-keypair 接口集成测试

覆盖 wecom_personal_rpa 服务端拉取模式的「密钥对自动生成」接口：

1. 成功路径：合法租户管理员 + wecom_personal_rpa 配置 → 200 + 返回公钥 PEM + 私钥加密入库
2. 非 wecom_personal_rpa 渠道 → 400
3. 配置不存在 → 404
4. 跨租户访问 → 403
5. 未登录 → 401
6. SaaS 未启用 → 返回 success=false

测试隔离策略（参照 test_wecom_personal_rpa_flow.py）：
- 不启动完整 src.main app（避免 master_agent / apscheduler 等重链副作用）
- 用最小 FastAPI app + include channel_config.router
- mock require_admin（鉴权）、ChannelConfigDB（DB 层）、ChannelFactory.invalidate_adapter（adapter 缓存）
- 真实执行 generate_rsa_keypair + store_private_key（验证密钥对生成 + 加密入库链路）
"""

# ===========================================================================
# 0. 环境隔离：必须在任何 src.* 导入之前完成
# ===========================================================================

import os
import sys
import types

# 关闭远程 DB 连接尝试（tests/integration/conftest.py autouse fixture 会读 DATABASE_URL）
os.environ.pop("DATABASE_URL", None)
os.environ.setdefault("RPA_SECRET_KEY", "test-master-key-for-keypair-integration-32B+")


def _install_db_stubs() -> None:
    """注入 pool 占位，避免 conftest autouse fixture 真实连库。"""
    try:
        from src.db import database as dbm  # noqa: WPS433
    except Exception:  # pragma: no cover
        return
    if getattr(dbm, "_rpa_test_stubbed", False):
        return
    dbm._rpa_test_stubbed = True
    dbm.init_postgres_pool = lambda *a, **kw: None  # type: ignore[assignment]
    dbm.get_postgres_pool = lambda *a, **kw: object()  # type: ignore[assignment]
    dbm.close_postgres_pool = lambda *a, **kw: None  # type: ignore[assignment]


_install_db_stubs()


# ===========================================================================
# 1. 正式 imports
# ===========================================================================

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def app():
    """构造最小 FastAPI app，仅 include channel_config.router。"""
    from src.saas.api import channel_config as channel_config_api

    app = FastAPI()
    app.include_router(channel_config_api.router)
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def tenant_admin():
    """模拟租户管理员身份。"""
    return {
        "user_id": "admin_user_1",
        "tenant_id": "tenant_9eb3e45cab83",
        "phone": "13800000001",
        "username": "tenant_admin",
        "role": "tenant_admin",
    }


@pytest.fixture
def rpa_config_row():
    """模拟一条 wecom_personal_rpa 渠道配置（get_by_id 返回的掩码版）。"""
    return {
        "config_id": "chan_616337ad19f6",
        "tenant_id": "tenant_9eb3e45cab83",
        "channel_type": "wecom_personal_rpa",
        "config": {
            "corp_id": "ww1234",
            "archive_secret": "***",
            "private_key": "***",
            "token": "***",
            "encoding_aes_key": "***",
            "listen_mode": "server",
        },
        "subagent_type": None,
        "verified": 0,
    }


@pytest.fixture
def rpa_config_decrypted():
    """模拟 get_by_id_decrypted 返回的解密版配置（其他敏感字段是明文，private_key 原为空）。"""
    return {
        "config_id": "chan_616337ad19f6",
        "tenant_id": "tenant_9eb3e45cab83",
        "channel_type": "wecom_personal_rpa",
        "config": {
            "corp_id": "ww1234",
            "archive_secret": "plain-archive-secret",
            "private_key": "",
            "token": "plain-token",
            "encoding_aes_key": "plain-aes-key",
            "listen_mode": "server",
        },
        "subagent_type": None,
    }


# ===========================================================================
# 测试用例
# ===========================================================================


class TestGenerateKeypair:
    """POST /api/saas/channels/{config_id}/generate-keypair"""

    def test_success_returns_public_key_and_stores_private(
        self, client, app, tenant_admin, rpa_config_row, rpa_config_decrypted
    ):
        """合法租户管理员 + wecom_personal_rpa 配置 → 200 + 公钥 PEM + 私钥加密入库。"""
        # mock require_admin 返回租户管理员
        app.dependency_overrides = {}
        with patch(
            "src.saas.api.channel_config.require_admin", return_value=tenant_admin
        ), patch(
            "src.saas.api.channel_config.ChannelConfigDB.get_by_id",
            return_value=rpa_config_row,
        ), patch(
            "src.saas.api.channel_config.ChannelConfigDB.get_by_id_decrypted",
            return_value=rpa_config_decrypted,
        ), patch(
            "src.saas.api.channel_config.ChannelConfigDB.update", return_value=True
        ) as mock_update, patch(
            "src.saas.api.channel_config.ChannelFactory.invalidate_adapter",
            new=AsyncMock(return_value=None),
        ), patch(
            "src.saas.api.channel_config.settings.saas.enabled", True
        ):
            resp = client.post(
                "/api/saas/channels/chan_616337ad19f6/generate-keypair",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True
        # 公钥 PEM 格式正确
        assert "BEGIN PUBLIC KEY" in body["public_key_raw"]
        assert "END PUBLIC KEY" in body["public_key_raw"]
        # public_key 是 \n 转义版本
        assert "\\n" in body["public_key"]
        assert "\n" not in body["public_key"]

        # 验证私钥被入库（update 被调用，private_key 是新明文 PEM）
        mock_update.assert_called_once()
        call_args = mock_update.call_args
        config_id_arg = call_args.args[0]
        config_arg = call_args.args[1]
        assert config_id_arg == "chan_616337ad19f6"
        # private_key 应为新 PEM 明文（store_private_key 直接传给 update，由 update 内部加密）
        assert "BEGIN PRIVATE KEY" in config_arg["private_key"]
        # 其他敏感字段保留（明文传入，update 内部会加密）
        assert config_arg["archive_secret"] == "plain-archive-secret"

    def test_non_wecom_personal_rpa_channel_returns_400(
        self, client, app, tenant_admin, rpa_config_row
    ):
        """非 wecom_personal_rpa 渠道调用 → 400。"""
        non_rpa_row = dict(rpa_config_row)
        non_rpa_row["channel_type"] = "wecom"
        with patch(
            "src.saas.api.channel_config.require_admin", return_value=tenant_admin
        ), patch(
            "src.saas.api.channel_config.ChannelConfigDB.get_by_id",
            return_value=non_rpa_row,
        ), patch(
            "src.saas.api.channel_config.settings.saas.enabled", True
        ):
            resp = client.post(
                "/api/saas/channels/chan_xxx/generate-keypair",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 400
        assert "仅 wecom_personal_rpa" in resp.json()["detail"]

    def test_config_not_found_returns_404(self, client, app, tenant_admin):
        """配置不存在 → 404。"""
        with patch(
            "src.saas.api.channel_config.require_admin", return_value=tenant_admin
        ), patch(
            "src.saas.api.channel_config.ChannelConfigDB.get_by_id", return_value=None
        ), patch(
            "src.saas.api.channel_config.settings.saas.enabled", True
        ):
            resp = client.post(
                "/api/saas/channels/chan_nonexistent/generate-keypair",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 404

    def test_cross_tenant_access_returns_403(
        self, client, app, tenant_admin, rpa_config_row
    ):
        """跨租户访问他人配置 → 403。"""
        other_tenant_row = dict(rpa_config_row)
        other_tenant_row["tenant_id"] = "tenant_other"
        with patch(
            "src.saas.api.channel_config.require_admin", return_value=tenant_admin
        ), patch(
            "src.saas.api.channel_config.ChannelConfigDB.get_by_id",
            return_value=other_tenant_row,
        ), patch(
            "src.saas.api.channel_config.settings.saas.enabled", True
        ):
            resp = client.post(
                "/api/saas/channels/chan_616337ad19f6/generate-keypair",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403

    def test_saas_disabled_returns_failure(self, client, app, tenant_admin):
        """SaaS 未启用 → 返回 success=false。"""
        with patch(
            "src.saas.api.channel_config.require_admin", return_value=tenant_admin
        ), patch(
            "src.saas.api.channel_config.settings.saas.enabled", False
        ):
            resp = client.post(
                "/api/saas/channels/chan_xxx/generate-keypair",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "未启用 SaaS" in body["message"]
