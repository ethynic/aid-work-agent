"""PUT /api/saas/channels/{config_id} 接口集成测试（wecom_kf 保留客服账号）

回归背景：渠道编辑页内新建客服账号后，前端表单 form.config 仍是打开编辑页时的
旧快照，点保存渠道时若后端整体覆盖 config，会把数据库里新建的 kf_account 覆盖
丢失（客户扫码链接失效）。修复：update_channel 对 wecom_kf 类型保存时以数据库
现有 kf_account 为准，账号增删改只走 /wecom-kf/accounts 独立接口。

测试隔离策略（参照 test_channel_config_generate_keypair.py）：
- 最小 FastAPI app + include channel_config.router
- mock require_admin（鉴权）、ChannelConfigDB（DB 层）、ChannelFactory.invalidate_adapter
"""

# ===========================================================================
# 0. 环境隔离：必须在任何 src.* 导入之前完成
# ===========================================================================

import os
import sys
import types

# 关闭远程 DB 连接尝试（tests/integration/conftest.py autouse fixture 会读 DATABASE_URL）
os.environ.pop("DATABASE_URL", None)


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

from unittest.mock import AsyncMock, MagicMock, patch  # noqa: E402

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


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
def wecom_kf_row():
    """模拟一条 wecom_kf 渠道配置，数据库现有 3 个客服账号（含一个新建的）。"""
    return {
        "config_id": "chan_cc10f9591586",
        "tenant_id": "tenant_9eb3e45cab83",
        "channel_type": "wecom_kf",
        "config": {
            "corp_id": "ww2ed7298c926e081c",
            "secret": "encrypted-secret",
            "token": "token",
            "encoding_aes_key": "aes-key",
            "kf_account": [
                {"name": "旅游咨询", "open_kfid": "wk_id_1", "scene": "kf_scene_1"},
                {"name": "小蔡老师", "open_kfid": "wk_id_2", "scene": "kf_scene_2"},
                {
                    "name": "爱定义-高老师",
                    "open_kfid": "wkS6oOTAAA9C7BP6XsKO1YnLYMApJ_bg",
                    "subagent_type": "pre-sales",
                    "tenant_user_id": "user_1dc51d7027fe",
                    "scene": "kf_ede60b670cef4b28",
                    "contact_url": "https://work.weixin.qq.com/kfid/xxx",
                    "credit_limit": 0,
                    "qr_title": "爱定义-高老师",
                },
            ],
        },
        "subagent_type": None,
        "verified": 1,
    }


# ===========================================================================
# 测试用例
# ===========================================================================


class TestUpdateChannelKeepKfAccount:
    """PUT /api/saas/channels/{config_id}"""

    def test_wecom_kf_preserves_db_kf_account(self, client, app, tenant_admin, wecom_kf_row):
        """wecom_kf 保存时，即使前端提交旧快照 kf_account，数据库现有账号（含新建）不丢失。"""
        # 前端旧快照：只有 2 个账号，缺新建的「爱定义-高老师」
        stale_body_config = {
            "corp_id": "ww2ed7298c926e081c",
            "secret": "encrypted-secret",
            "token": "token",
            "encoding_aes_key": "aes-key",
            "waiting_indicator": {"enabled": False},
            "kf_account": [
                {"name": "旅游咨询", "open_kfid": "wk_id_1", "scene": "kf_scene_1"},
                {"name": "小蔡老师", "open_kfid": "wk_id_2", "scene": "kf_scene_2"},
            ],
        }

        # get_by_id 首次返回 existing（数据库 3 账号），保存后再查返回 updated（仍 3 账号）
        updated_row = dict(wecom_kf_row)
        updated_row["config"] = dict(wecom_kf_row["config"])
        get_by_id_mock = MagicMock(side_effect=[wecom_kf_row, updated_row])

        with patch(
            "src.saas.api.channel_config.require_admin", return_value=tenant_admin
        ), patch(
            "src.saas.api.channel_config.ChannelConfigDB.get_by_id", get_by_id_mock
        ), patch(
            "src.saas.api.channel_config.ChannelConfigDB.update", return_value=True
        ) as mock_update, patch(
            "src.saas.api.channel_config.ChannelFactory.invalidate_adapter",
            new=AsyncMock(return_value=None),
        ):
            resp = client.put(
                "/api/saas/channels/chan_cc10f9591586",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "name": "企微客服1",
                    "config": stale_body_config,
                },
            )

        assert resp.status_code == 200, resp.text
        assert resp.json()["success"] is True

        # 传给 DB 的 config 中 kf_account 以数据库 3 条为准，新建账号不丢失
        mock_update.assert_called_once()
        config_arg = mock_update.call_args.args[1]
        saved_kf = config_arg["kf_account"]
        assert len(saved_kf) == 3
        saved_open_kfids = {k["open_kfid"] for k in saved_kf}
        assert "wkS6oOTAAA9C7BP6XsKO1YnLYMApJ_bg" in saved_open_kfids
        # 渠道级字段仍保留（等待提示来自请求体）
        assert config_arg["waiting_indicator"] == {"enabled": False}

    def test_non_wecom_kf_passes_kf_account_through(self, client, app, tenant_admin):
        """非 wecom_kf 渠道保存时，请求体 config 原样传递（不做干预）。"""
        existing_row = {
            "config_id": "chan_wecom_1",
            "tenant_id": "tenant_9eb3e45cab83",
            "channel_type": "wecom",
            "config": {"corp_id": "ww_old", "agent_id": "1000001"},
            "subagent_type": None,
            "verified": 1,
        }
        body_config = {"corp_id": "ww_new", "agent_id": "1000001", "kf_account": [{"name": "x"}]}
        updated_row = dict(existing_row)
        updated_row["config"] = dict(body_config)
        get_by_id_mock = MagicMock(side_effect=[existing_row, updated_row])

        with patch(
            "src.saas.api.channel_config.require_admin", return_value=tenant_admin
        ), patch(
            "src.saas.api.channel_config.ChannelConfigDB.get_by_id", get_by_id_mock
        ), patch(
            "src.saas.api.channel_config.ChannelConfigDB.update", return_value=True
        ) as mock_update, patch(
            "src.saas.api.channel_config.ChannelFactory.invalidate_adapter",
            new=AsyncMock(return_value=None),
        ):
            resp = client.put(
                "/api/saas/channels/chan_wecom_1",
                headers={"Authorization": "Bearer fake-token"},
                json={"name": "wecom", "config": body_config},
            )

        assert resp.status_code == 200, resp.text
        config_arg = mock_update.call_args.args[1]
        # 非 wecom_kf：kf_account 原样保留（未做数据库覆盖）
        assert config_arg["kf_account"] == [{"name": "x"}]
