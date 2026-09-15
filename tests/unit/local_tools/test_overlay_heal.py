"""弹层自愈单元测试（2026-08-31，mock DB/LLM，无真实依赖）

覆盖：
- overlay_heal_service.pick_heuristic：白名单+弹层类名直选 / 无类名特征不盲选 / 非白名单拒绝
- pick_dismiss_text_with_llm：合法选择 / 非白名单防幻觉拦截 / found=false 放弃 / 围栏 JSON 容忍
- 编排：UI_CHANGED 失败 → inspect → heuristic → dismiss → 重试成功 → data.heal + 自愈专项费
- EXECUTION_UNKNOWN 不触发自愈；heal 关闭时不发起 inspect；候选为空放弃并保留原错误
- overlay 原语自身 heal_eligible=False、零计费（不递归不扣费）
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.settings import settings
from src.local_tools.proxy_tool import (
    BossGreetTool,
    BossOverlayDismissTool,
    BossOverlayInspectTool,
)
from src.services import overlay_heal_service

pytestmark = pytest.mark.unit

TENANT = "tenant_t1"
USER = "user_t1"
REPO = "src.local_tools.proxy_tool.repository"
USAGE_DB = "src.local_tools.proxy_tool.ClientUsageLogDB"
# 余额预检经函数内 from ... import TenantDB 后调用类方法，patch 类方法即可
TENANT_DB = "src.saas.db.tenant_db.TenantDB.get_by_id"

CANDIDATES = [
    {"text": "新人礼包", "x": 500, "y": 200, "w": 200, "h": 40, "cls": "wb-dialog-title"},
    {"text": "关闭", "x": 600, "y": 300, "w": 50, "h": 24, "cls": "wb-dialog-close"},
]


def _online_device():
    return {
        "id": "dev-1",
        "selected": True,
        "status": "active",
        "last_seen_at": datetime.now(),
        "capabilities_json": {"provider_id": "ai.aidwork.boss-recruiting"},
    }


def _kwargs(**extra):
    return {"_trusted_tenant_id": TENANT, "_trusted_user_id": USER,
            "_session_id": "sess-v1", **extra}


def _inv(state="succeeded", data=None, error_code=None, error_message=None):
    return {
        "id": 0,
        "tenant_id": TENANT,
        "state": state,
        "effect": "applied" if state == "succeeded" else None,
        "result_json": {"message": "ok", **({"data": data} if data is not None else {})},
        "error_code": error_code,
        "error_message": error_message,
    }


def _stateful_repo(rows_by_id, create_ids):
    """返回 (patcher, mocks)：patch.multiple 传显式 new 时 as 字典为空，由本助手持有 mock 引用"""
    creates = iter(create_ids)
    mocks = {
        "list_devices": MagicMock(return_value=[_online_device()]),
        "create_invocation": MagicMock(side_effect=lambda *a, **k: next(creates)),
        "list_events": MagicMock(return_value=[]),
        "get_invocation": MagicMock(side_effect=lambda inv_id, tenant: rows_by_id.get(inv_id)),
        "request_cancel": MagicMock(return_value=True),
        "set_invocation_credit_cost": MagicMock(return_value=None),
    }
    return patch.multiple(REPO, **mocks), mocks


# ==================== overlay_heal_service ====================


class TestPickHeuristic:
    def test_whitelist_with_overlay_cls_picked(self):
        assert overlay_heal_service.pick_heuristic(CANDIDATES) == "关闭"

    def test_whitelist_without_overlay_cls_not_blindly_picked(self):
        cands = [{"text": "取消", "cls": "btn-default"}, {"text": "标题", "cls": ""}]
        assert overlay_heal_service.pick_heuristic(cands) is None

    def test_non_whitelist_rejected(self):
        cands = [{"text": "立即领取", "cls": "dialog-btn"}]
        assert overlay_heal_service.pick_heuristic(cands) is None

    def test_empty_candidates(self):
        assert overlay_heal_service.pick_heuristic([]) is None

    def test_icon_candidates_first(self):
        """icon 关闭控件（class 含 close/guanbi）是最强信号，优先于文本启发式"""
        icons = [{"icon_cls": "boss-popup__close", "x": 986, "y": 372, "w": 24, "h": 26}]
        assert overlay_heal_service.pick_heuristic([], icons) == "icon:boss-popup__close"

    def test_icon_candidates_non_close_cls_ignored(self):
        icons = [{"icon_cls": "btn-gold", "x": 986, "y": 372, "w": 24, "h": 26}]
        assert overlay_heal_service.pick_heuristic([], icons) is None


class TestPickWithLlm:
    async def test_valid_pick(self, monkeypatch):
        gw = MagicMock()
        gw.chat_no_thinking = AsyncMock(return_value={
            "content": '```json\n{"found": true, "text": "关闭", "why": "dialog 右上角"}\n```',
            "usage": {"prompt_tokens": 100, "completion_tokens": 10},
        })
        record = MagicMock()
        monkeypatch.setattr(overlay_heal_service, "llm_gateway", gw)
        monkeypatch.setattr(overlay_heal_service, "record_background_llm_usage", record)
        text = await overlay_heal_service.pick_dismiss_text_with_llm(TENANT, USER, CANDIDATES)
        assert text == "关闭"
        record.assert_called_once()
        assert record.call_args.kwargs["source"] == "boss_overlay_heal"

    async def test_non_whitelist_pick_rejected(self, monkeypatch):
        gw = MagicMock()
        gw.chat_no_thinking = AsyncMock(return_value={
            "content": '{"found": true, "text": "立即领取", "why": "x"}',
            "usage": None,
        })
        monkeypatch.setattr(overlay_heal_service, "llm_gateway", gw)
        monkeypatch.setattr(overlay_heal_service, "record_background_llm_usage", MagicMock())
        text = await overlay_heal_service.pick_dismiss_text_with_llm(TENANT, USER, CANDIDATES)
        assert text is None  # 防幻觉拦截：两轮都拒绝

    async def test_found_false_gives_up(self, monkeypatch):
        gw = MagicMock()
        gw.chat_no_thinking = AsyncMock(return_value={
            "content": '{"found": false, "why": "无弹层"}',
            "usage": None,
        })
        monkeypatch.setattr(overlay_heal_service, "llm_gateway", gw)
        monkeypatch.setattr(overlay_heal_service, "record_background_llm_usage", MagicMock())
        assert await overlay_heal_service.pick_dismiss_text_with_llm(TENANT, USER, CANDIDATES) is None

    async def test_valid_icon_pick(self, monkeypatch):
        icons = [{"icon_cls": "boss-popup__close", "x": 986, "y": 372, "w": 24, "h": 26}]
        gw = MagicMock()
        gw.chat_no_thinking = AsyncMock(return_value={
            "content": '{"found": true, "text": "icon:boss-popup__close", "why": "弹窗右上角"}',
            "usage": None,
        })
        monkeypatch.setattr(overlay_heal_service, "llm_gateway", gw)
        monkeypatch.setattr(overlay_heal_service, "record_background_llm_usage", MagicMock())
        text = await overlay_heal_service.pick_dismiss_text_with_llm(TENANT, USER, [], icons)
        assert text == "icon:boss-popup__close"

    async def test_llm_fabricated_icon_ref_rejected(self, monkeypatch):
        icons = [{"icon_cls": "boss-popup__close", "x": 986, "y": 372, "w": 24, "h": 26}]
        gw = MagicMock()
        gw.chat_no_thinking = AsyncMock(return_value={
            "content": '{"found": true, "text": "icon:made-up-close", "why": "x"}',
            "usage": None,
        })
        monkeypatch.setattr(overlay_heal_service, "llm_gateway", gw)
        monkeypatch.setattr(overlay_heal_service, "record_background_llm_usage", MagicMock())
        assert await overlay_heal_service.pick_dismiss_text_with_llm(TENANT, USER, [], icons) is None

    async def test_fabricated_text_rejected(self, monkeypatch):
        """LLM 编造候选清单里不存在的文本 → 拒绝"""
        gw = MagicMock()
        gw.chat_no_thinking = AsyncMock(return_value={
            "content": '{"found": true, "text": "我知道了", "why": "x"}',  # 清单里没有
            "usage": None,
        })
        monkeypatch.setattr(overlay_heal_service, "llm_gateway", gw)
        monkeypatch.setattr(overlay_heal_service, "record_background_llm_usage", MagicMock())
        assert await overlay_heal_service.pick_dismiss_text_with_llm(TENANT, USER, CANDIDATES) is None


# ==================== 编排 ====================


class TestHealOrchestration:
    async def test_heal_success_flow(self, monkeypatch):
        """UI_CHANGED 失败 → inspect → 启发式命中 → dismiss → 重试成功 → data.heal + 自愈专项费"""
        rows = {
            "g1": _inv(state="failed", error_code="UI_CHANGED", error_message="头部未切换"),
            "i1": _inv(data={"candidates": CANDIDATES, "viewport": {"width": 1249, "height": 1277}}),
            "d1": _inv(),
            "g2": _inv(data={"sent": True}),
        }
        record = MagicMock(return_value={"credit_cost": 1.0, "balance_after": 9.0})
        repo_patch, repo = _stateful_repo(rows, ["g1", "i1", "d1", "g2"])
        with repo_patch, patch(USAGE_DB, record_tool_usage=record), \
             patch(TENANT_DB, MagicMock(return_value=None)), \
             patch("src.services.overlay_heal_service.pick_heuristic", return_value="关闭"):
            result = await BossGreetTool().execute(**_kwargs())

        assert result["success"] is True
        heal = (result.get("data") or {}).get("heal")
        assert heal == {"attempted": True, "dismissed_text": "关闭",
                        "llm_used": False, "dismissed": True, "healed": True}
        # 下发序列：greet(败) → overlay_inspect → overlay_dismiss → greet(重试成功)
        names = [c.args[3] for c in repo["create_invocation"].call_args_list]
        assert names == ["boss_greet", "boss_overlay_inspect", "boss_overlay_dismiss", "boss_greet"]
        # 计费分工（2026-09-01 迁移）：重试成功的原工具费在 write_result 落库侧，
        # proxy 只收无独立 invocation 的自愈专项费；归属字段 session/user 随行
        billed = {c.kwargs["tool_name"]: c.kwargs["credit_cost"] for c in record.call_args_list}
        assert billed == {"boss_overlay_heal": 2.0}
        heal_call = record.call_args_list[0]
        assert heal_call.kwargs["session_id"] == "sess-v1"
        assert heal_call.kwargs["user_id"] == USER

    async def test_execution_unknown_never_heals(self, monkeypatch):
        """EXECUTION_UNKNOWN（写后结果不明）绝不自愈重试"""
        rows = {"g1": _inv(state="unknown", error_code="UNKNOWN", error_message="结果无法确认")}
        record = MagicMock()
        repo_patch, repo = _stateful_repo(rows, ["g1"])
        with repo_patch, patch(USAGE_DB, record_tool_usage=record), \
             patch(TENANT_DB, MagicMock(return_value=None)):  # 租户不存在 -> 不阻断（等价跳过预检）
            result = await BossGreetTool().execute(**_kwargs())
        assert result["success"] is False
        assert result["code"] == "EXECUTION_UNKNOWN"
        assert repo["create_invocation"].call_count == 1  # 只有一次下发，无自愈
        record.assert_not_called()

    async def test_heal_disabled_skips(self, monkeypatch):
        monkeypatch.setattr(settings.boss_tool_billing, "overlay_heal_enabled", False)
        rows = {"g1": _inv(state="failed", error_code="UI_CHANGED", error_message="x")}
        record = MagicMock()
        repo_patch, repo = _stateful_repo(rows, ["g1"])
        with repo_patch, patch(USAGE_DB, record_tool_usage=record), \
             patch(TENANT_DB, MagicMock(return_value=None)):  # 租户不存在 -> 不阻断（等价跳过预检）
            result = await BossGreetTool().execute(**_kwargs())
        assert result["success"] is False
        assert repo["create_invocation"].call_count == 1
        assert (result.get("data") or {}) == {}

    async def test_heal_gives_up_on_empty_candidates_keeps_original_error(self, monkeypatch):
        rows = {
            "g1": _inv(state="failed", error_code="UI_CHANGED", error_message="页面结构异常"),
            "i1": _inv(data={"candidates": [], "viewport": {"width": 1249, "height": 1277}}),
        }
        record = MagicMock()
        repo_patch, _repo = _stateful_repo(rows, ["g1", "i1"])
        with repo_patch, patch(USAGE_DB, record_tool_usage=record), \
             patch(TENANT_DB, MagicMock(return_value=None)), \
             patch("src.services.overlay_heal_service.pick_heuristic", return_value=None):
            result = await BossGreetTool().execute(**_kwargs())
        assert result["success"] is False
        assert result["code"] == "UI_CHANGED"  # 原错误保留
        heal = (result.get("data") or {}).get("heal")
        assert heal == {"attempted": True, "dismissed_text": None, "llm_used": False}
        record.assert_not_called()  # 自愈未救回，不收 heal 费


class TestOverlayPrimitives:
    def test_overlay_tools_not_healable_and_free(self):
        for cls in (BossOverlayInspectTool, BossOverlayDismissTool):
            tool = cls()
            assert tool.heal_eligible is False  # 不递归自愈
            assert tool._tool_credit_price() == 0.0  # 零单价（heal 走专项费）
        assert BossGreetTool().heal_eligible is True
