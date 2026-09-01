"""BOSS 本地工具按次计费单元测试（boss_tool_billing，mock DB，无真实库）

覆盖（2026-08-31 租户交付计费；2026-09-01 计费时机迁移后聚焦 proxy 侧职责）：
- 价格解析：价目表命中 / 未命中走 default_credit_price / 总开关关闭 / 非法配置容错
- 成功终态：从 invocation.credit_cost 附带实扣金额进 data（计费本体在
  repository.write_result 落库侧，见 test_write_result_billing.py）；proxy 不再发起计费
- 失败 / 超时终态不附带金额
- 余额 ≤0 阻断（NO_CREDIT，不建 invocation）；SaaS 关闭 / 租户不存在不阻断
- 免费工具不查余额
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.config.settings import BossToolBillingConfig, settings
from src.db.client_binding_db import BOSS_TOOL_BINDING_SENTINEL, BOSS_TOOL_USAGE_STAGE
from src.local_tools.proxy_tool import BossGotoTool, BossGreetTool

pytestmark = pytest.mark.unit

TENANT = "tenant_t1"
USER = "user_t1"
REPO = "src.local_tools.proxy_tool.repository"
TENANT_DB = "src.saas.db.tenant_db.TenantDB.get_by_id"
USAGE_DB = "src.local_tools.proxy_tool.ClientUsageLogDB"


def _online_device():
    return {
        "id": "dev-1",
        "selected": True,
        "status": "active",
        "last_seen_at": datetime.now(),
        "capabilities_json": {"provider_id": "ai.aidwork.boss-recruiting"},
    }


def _kwargs(**extra):
    return {"_trusted_tenant_id": TENANT, "_trusted_user_id": USER, **extra}


def _invocation(state="succeeded", data=None, credit_cost=None):
    return {
        "id": 1,
        "tenant_id": TENANT,
        "state": state,
        "effect": "applied" if state == "succeeded" else None,
        "result_json": {"message": "ok", **({"data": data} if data is not None else {})},
        "error_code": None,
        "error_message": None,
        "credit_cost": credit_cost,
    }


def _patch_repo(devices, invocation=None, events=None):
    """返回 (patcher, mocks)：patch.multiple 传显式 new 时 as 字典为空，故由本助手持有 mock 引用"""
    mocks = {
        "list_devices": MagicMock(return_value=devices),
        "create_invocation": MagicMock(return_value="inv-1"),
        "list_events": MagicMock(return_value=events or []),
        "get_invocation": MagicMock(return_value=invocation),
        "request_cancel": MagicMock(return_value=True),
        "set_invocation_credit_cost": MagicMock(return_value=None),
    }
    return patch.multiple(REPO, **mocks), mocks


class TestPriceResolution:
    def test_priced_tool_hit_table(self):
        assert BossGreetTool()._tool_credit_price() == 1.0

    def test_unlisted_tool_falls_back_to_default_zero(self):
        assert BossGotoTool()._tool_credit_price() == 0.0

    def test_default_price_applies_when_configured(self, monkeypatch):
        monkeypatch.setattr(
            settings, "boss_tool_billing",
            BossToolBillingConfig(default_credit_price=0.5),
        )
        assert BossGotoTool()._tool_credit_price() == 0.5

    def test_master_switch_disabled(self, monkeypatch):
        monkeypatch.setattr(settings, "boss_tool_billing", BossToolBillingConfig(enabled=False))
        assert BossGreetTool()._tool_credit_price() == 0.0

    def test_invalid_price_value_fails_closed_to_zero(self, monkeypatch):
        monkeypatch.setattr(
            settings, "boss_tool_billing",
            SimpleNamespace(enabled=True, default_credit_price=0.0,
                            tool_credit_prices={"boss_greet": "abc"}),
        )
        assert BossGreetTool()._tool_credit_price() == 0.0


class TestSuccessAttachesCost:
    async def test_success_attaches_credit_cost_from_invocation(self, monkeypatch):
        """计费本体在 write_result 落库侧：proxy 只把 invocation.credit_cost 附进 data"""
        monkeypatch.setattr(settings.saas, "enabled", True)
        record = MagicMock()
        repo_patch, repo = _patch_repo(
            [_online_device()], invocation=_invocation(data={"foo": 1}, credit_cost=1.0)
        )
        with repo_patch, patch(USAGE_DB, record_tool_usage=record):
            result = await BossGreetTool().execute(**_kwargs())

        assert result["success"] is True
        record.assert_not_called()  # 轮询侧不再计费
        repo["set_invocation_credit_cost"].assert_not_called()
        # data 附带实扣
        assert result["data"] == {"foo": 1, "credit_cost": 1.0}

    async def test_success_without_credit_cost_keeps_data_intact(self, monkeypatch):
        """credit_cost 为 NULL（落库侧计费降级/历史行）：成功结果不附带金额，data 原形"""
        monkeypatch.setattr(settings.saas, "enabled", True)
        record = MagicMock()
        repo_patch, _repo = _patch_repo(
            [_online_device()], invocation=_invocation(data={"foo": 1}, credit_cost=None)
        )
        with repo_patch, patch(USAGE_DB, record_tool_usage=record):
            result = await BossGreetTool().execute(**_kwargs())

        assert result["success"] is True
        assert result["data"] == {"foo": 1}
        record.assert_not_called()

    async def test_free_tool_zero_cost_no_attach(self, monkeypatch):
        """免费工具 credit_cost=0 占位：不附带金额（不给上下文添噪音）"""
        monkeypatch.setattr(settings.saas, "enabled", True)
        record = MagicMock()
        repo_patch, _repo = _patch_repo(
            [_online_device()], invocation=_invocation(data={"via": "already"}, credit_cost=0.0)
        )
        with repo_patch, patch(USAGE_DB, record_tool_usage=record):
            result = await BossGotoTool().execute(**_kwargs())

        assert result["success"] is True
        assert result["data"] == {"via": "already"}

    async def test_failed_terminal_never_attaches(self, monkeypatch):
        monkeypatch.setattr(settings.saas, "enabled", True)
        record = MagicMock()
        repo_patch, repo = _patch_repo(
            [_online_device()], invocation=_invocation(state="failed", credit_cost=None)
        )
        with repo_patch, patch(USAGE_DB, record_tool_usage=record):
            result = await BossGreetTool().execute(**_kwargs())

        assert result["success"] is False
        record.assert_not_called()
        repo["set_invocation_credit_cost"].assert_not_called()

    async def test_timeout_never_bills(self, monkeypatch):
        monkeypatch.setattr(settings.saas, "enabled", True)
        record = MagicMock()
        tool = BossGreetTool()
        tool.timeout_seconds = 0  # 立即超时（get_invocation 恒 None）
        repo_patch, repo = _patch_repo([_online_device()], invocation=None)
        with repo_patch, patch(USAGE_DB, record_tool_usage=record):
            result = await tool.execute(**_kwargs())

        assert result["success"] is False
        assert result["code"] == "TIMEOUT"
        record.assert_not_called()
        repo["set_invocation_credit_cost"].assert_not_called()


class TestCreditPrecheck:
    async def test_zero_balance_blocks_before_invocation(self, monkeypatch):
        monkeypatch.setattr(settings.saas, "enabled", True)
        get_tenant = MagicMock(return_value={"credit_balance": 0.0})
        record = MagicMock()
        repo_patch, repo = _patch_repo([_online_device()])
        with repo_patch, patch(TENANT_DB, get_tenant), patch(USAGE_DB, record_tool_usage=record):
            result = await BossGreetTool().execute(**_kwargs())

        assert result["success"] is False
        assert result["code"] == "NO_CREDIT"
        assert "积分余额已耗尽" in result["message"]
        repo["create_invocation"].assert_not_called()
        record.assert_not_called()

    async def test_positive_balance_passes(self, monkeypatch):
        monkeypatch.setattr(settings.saas, "enabled", True)
        get_tenant = MagicMock(return_value={"credit_balance": 5.0})
        record = MagicMock()
        repo_patch, _repo = _patch_repo(
            [_online_device()], invocation=_invocation(credit_cost=1.0)
        )
        with repo_patch, patch(TENANT_DB, get_tenant), patch(USAGE_DB, record_tool_usage=record):
            result = await BossGreetTool().execute(**_kwargs())

        assert result["success"] is True
        get_tenant.assert_called_once_with(TENANT)
        record.assert_not_called()

    async def test_saas_disabled_skips_precheck(self, monkeypatch):
        monkeypatch.setattr(settings.saas, "enabled", False)
        get_tenant = MagicMock(return_value={"credit_balance": 0.0})
        record = MagicMock()
        repo_patch, _repo = _patch_repo(
            [_online_device()], invocation=_invocation(credit_cost=1.0)
        )
        with repo_patch, patch(TENANT_DB, get_tenant), patch(USAGE_DB, record_tool_usage=record):
            result = await BossGreetTool().execute(**_kwargs())

        assert result["success"] is True
        get_tenant.assert_not_called()  # 非 SaaS 模式不查余额

    async def test_unknown_tenant_passes(self, monkeypatch):
        monkeypatch.setattr(settings.saas, "enabled", True)
        get_tenant = MagicMock(return_value=None)
        record = MagicMock()
        repo_patch, _repo = _patch_repo(
            [_online_device()], invocation=_invocation(credit_cost=1.0)
        )
        with repo_patch, patch(TENANT_DB, get_tenant), patch(USAGE_DB, record_tool_usage=record):
            result = await BossGreetTool().execute(**_kwargs())
        assert result["success"] is True

    async def test_free_tool_skips_precheck(self, monkeypatch):
        monkeypatch.setattr(settings.saas, "enabled", True)
        get_tenant = MagicMock(return_value={"credit_balance": 0.0})
        record = MagicMock()
        repo_patch, repo = _patch_repo(
            [_online_device()], invocation=_invocation(credit_cost=0.0)
        )
        with repo_patch, patch(TENANT_DB, get_tenant), patch(USAGE_DB, record_tool_usage=record):
            result = await BossGotoTool().execute(**_kwargs())

        assert result["success"] is True
        get_tenant.assert_not_called()  # 免费工具不查余额
        record.assert_not_called()
        repo["set_invocation_credit_cost"].assert_not_called()


class TestLedgerConstants:
    def test_stage_and_sentinel_values(self):
        """台账标记固定值：/portal/client-logs 按 stage 过滤、绑定哨兵不串真实 client_bindings"""
        assert BOSS_TOOL_USAGE_STAGE == "boss_tool"
        assert BOSS_TOOL_BINDING_SENTINEL == "boss-local-runtime"
