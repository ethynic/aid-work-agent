"""
external_push_human 适配器单元测试（人工期对话推送第三方系统，#64 Phase 2 §9.5）

覆盖：
1. 租户文档缺失 no-op（非 10605 对接租户，不触碰冷却键）
2. 窗口内无人工期消息 no-op
3. 冷却中跳过（不调 LLM / 不走推送循环）
4. 正常链路（system_prompt 含人工期特例、user_message 含对话窗口与转人工信息，摘要计费）
5. 摘要解析失败降级（推送继续，user_message 用降级提示语）
6. 推送失败删冷却键
7. 转人工信息线索字段兜底
8. external_push._run_push_loop 不传新参数时回退默认 builder（回归）
"""

import json
import time
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.recap.runner import RecapPayload
from src.services.recap.tasks.external_push import _run_push_loop
from src.services.recap.tasks.external_push_human import ExternalPushHumanAdapter, _resolve_transfer_info

_TOPIC_NS = "src.services.recap.tasks.external_push_human"

_FAKE_DOC = (
    "# 10605 售前咨询接口文档\n"
    "```api-meta\n"
    "login_url: https://erp.example.com/api/delegate-login\n"
    "user_token_name: client_token\n"
    "```\n"
    "## 同步业务规则\n"
    "每轮问答一条跟进记录。\n"
)


def _make_payload(**overrides) -> RecapPayload:
    base = dict(
        tenant_id="tenant_abc",
        session_id="tenant_abc_wecom_kf_kf1_user1_pre-sales",
        subagent_name="pre-sales",
        round_message_id="msg_123",
        user_content="",
        assistant_reply="",
        record_service=None,
        user_id=None,
        trace_id=None,
        enqueued_at=time.time(),
    )
    base.update(overrides)
    return RecapPayload(**base)


def _make_ctx() -> dict:
    return {
        "open_kfid": "kf1",
        "external_userid": "user1",
        "subagent": "pre-sales",
        "nickname": "张三",
        "avatar": None,
        "gender": 1,
        "lead_phone": None,
        "assignee_phone": "13800000000",
        "assignee_name": "李四",
    }


def _human_period_messages() -> list:
    return [
        {"message_id": "m1", "role": "user", "content": "你们的产品多少钱", "metadata": {}},
        {"message_id": "m2", "role": "assistant", "content": "价格根据配置不同", "metadata": {}},
        {"message_id": "m3", "role": "system", "content": "[已转人工] ……", "metadata": {}},
        {"message_id": "m4", "role": "user", "content": "[人工客服] 张三您好，我是李四", "metadata": {"source": "servicer"}},
        {"message_id": "m5", "role": "user", "content": "那我再考虑下", "metadata": {"source": "customer_human"}},
    ]


def _summary_json() -> str:
    return json.dumps({
        "customer_need": "客户询价后表示再考虑",
        "reply_summary": "员工介绍了价格体系",
        "customer_name_hint": "张三",
    }, ensure_ascii=False)


class TestExternalPushHumanAdapter:
    @pytest.mark.asyncio
    async def test_noop_when_tenant_doc_missing(self):
        """租户文档缺失（非 10605 对接租户）：静默 no-op，不触碰冷却键"""
        payload = _make_payload()
        with patch(f"{_TOPIC_NS}._collect_context", return_value=_make_ctx()), \
                patch(f"{_TOPIC_NS}._load_tenant_doc", return_value=None), \
                patch(f"{_TOPIC_NS}.redis_client") as mock_redis:
            await ExternalPushHumanAdapter.execute(payload)
            mock_redis.acquire_lock.assert_not_called()

    @pytest.mark.asyncio
    async def test_noop_when_no_human_period_message(self):
        """窗口内无人工期消息（纯智能体期）：no-op，不占冷却坑"""
        payload = _make_payload()
        ai_only = [
            {"message_id": "m1", "role": "user", "content": "多少钱", "metadata": {}},
            {"message_id": "m2", "role": "assistant", "content": "报价中", "metadata": {}},
        ]
        with patch(f"{_TOPIC_NS}._collect_context", return_value=_make_ctx()), \
                patch(f"{_TOPIC_NS}._load_tenant_doc", return_value=_FAKE_DOC), \
                patch(f"{_TOPIC_NS}._get_agent_token", return_value="agent_tok"), \
                patch("src.channels.session.channel_session_manager") as mock_mgr, \
                patch(f"{_TOPIC_NS}.redis_client") as mock_redis:
            mock_mgr.get_messages.return_value = ai_only
            await ExternalPushHumanAdapter.execute(payload)
            mock_redis.acquire_lock.assert_not_called()

    @pytest.mark.asyncio
    async def test_noop_when_api_meta_invalid(self):
        """租户文档 api-meta 解析失败：no-op，不占冷却坑"""
        payload = _make_payload()
        with patch(f"{_TOPIC_NS}._collect_context", return_value=_make_ctx()), \
                patch(f"{_TOPIC_NS}._load_tenant_doc", return_value="文档无 api-meta 块"), \
                patch(f"{_TOPIC_NS}._get_agent_token", return_value="agent_tok"), \
                patch(f"{_TOPIC_NS}.redis_client") as mock_redis:
            await ExternalPushHumanAdapter.execute(payload)
            mock_redis.acquire_lock.assert_not_called()

    @pytest.mark.asyncio
    async def test_noop_when_agent_token_missing(self):
        """租户未配置 AGENT_TOKEN：no-op"""
        payload = _make_payload()
        with patch(f"{_TOPIC_NS}._collect_context", return_value=_make_ctx()), \
                patch(f"{_TOPIC_NS}._load_tenant_doc", return_value=_FAKE_DOC), \
                patch(f"{_TOPIC_NS}._get_agent_token", return_value=None), \
                patch("src.channels.session.channel_session_manager") as mock_mgr, \
                patch(f"{_TOPIC_NS}.redis_client") as mock_redis:
            mock_mgr.get_messages.return_value = _human_period_messages()
            await ExternalPushHumanAdapter.execute(payload)
            mock_redis.acquire_lock.assert_not_called()

    @pytest.mark.asyncio
    async def test_noop_when_assignee_phone_missing(self):
        """归属员工手机号缺失（委托登录无法进行）：no-op"""
        payload = _make_payload()
        ctx = _make_ctx()
        ctx["assignee_phone"] = None
        with patch(f"{_TOPIC_NS}._collect_context", return_value=ctx), \
                patch(f"{_TOPIC_NS}._load_tenant_doc", return_value=_FAKE_DOC), \
                patch(f"{_TOPIC_NS}.redis_client") as mock_redis:
            await ExternalPushHumanAdapter.execute(payload)
            mock_redis.acquire_lock.assert_not_called()

    @pytest.mark.asyncio
    async def test_delegate_login_failure_deletes_cooldown(self):
        """委托登录失败：删冷却键允许下一条人工期消息重试，异常上抛由 runner 吞掉"""
        payload = _make_payload()
        with patch(f"{_TOPIC_NS}._collect_context", return_value=_make_ctx()), \
                patch(f"{_TOPIC_NS}._load_tenant_doc", return_value=_FAKE_DOC), \
                patch(f"{_TOPIC_NS}._get_agent_token", return_value="agent_tok"), \
                patch("src.channels.session.channel_session_manager") as mock_mgr, \
                patch(f"{_TOPIC_NS}.redis_client") as mock_redis, \
                patch(f"{_TOPIC_NS}._delegate_login", return_value=None), \
                patch("src.llm.gateway.llm_gateway") as mock_gw, \
                patch("src.services.session_record.record_background_llm_usage"):
            mock_mgr.get_session_by_id.return_value = {"metadata": {"transferred_to": "servicer_1"}}
            mock_mgr.get_messages.return_value = _human_period_messages()
            mock_redis.acquire_lock.return_value = True
            mock_redis.get.return_value = None
            mock_gw.chat_lite = AsyncMock(return_value={"content": _summary_json(), "usage": None})

            with pytest.raises(RuntimeError):
                await ExternalPushHumanAdapter.execute(payload)

            mock_redis.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_cooldown_skip(self):
        """冷却中（占坑失败）：跳过推送，不调 LLM"""
        payload = _make_payload()
        with patch(f"{_TOPIC_NS}._collect_context", return_value=_make_ctx()), \
                patch(f"{_TOPIC_NS}._load_tenant_doc", return_value=_FAKE_DOC), \
                patch(f"{_TOPIC_NS}._get_agent_token", return_value="agent_tok"), \
                patch("src.channels.session.channel_session_manager") as mock_mgr, \
                patch(f"{_TOPIC_NS}.redis_client") as mock_redis, \
                patch(f"{_TOPIC_NS}._run_push_loop") as mock_loop:
            mock_mgr.get_messages.return_value = _human_period_messages()
            mock_redis.acquire_lock.return_value = False
            await ExternalPushHumanAdapter.execute(payload)
            mock_loop.assert_not_called()

    @pytest.mark.asyncio
    async def test_normal_push(self):
        """正常链路：摘要计费 + system_prompt 含人工期特例 + user_message 含窗口与转人工信息"""
        payload = _make_payload()
        response = {"content": _summary_json(), "usage": {"prompt_tokens": 80, "completion_tokens": 40}}
        with patch(f"{_TOPIC_NS}._collect_context", return_value=_make_ctx()), \
                patch(f"{_TOPIC_NS}._load_tenant_doc", return_value=_FAKE_DOC), \
                patch(f"{_TOPIC_NS}._get_agent_token", return_value="agent_tok"), \
                patch("src.channels.session.channel_session_manager") as mock_mgr, \
                patch(f"{_TOPIC_NS}.redis_client") as mock_redis, \
                patch(f"{_TOPIC_NS}._delegate_login", return_value={"client_token": "tok123", "display_name": "李四"}), \
                patch(f"{_TOPIC_NS}._run_push_loop") as mock_loop, \
                patch("src.llm.gateway.llm_gateway") as mock_gw, \
                patch("src.services.session_record.record_background_llm_usage") as mock_bill:
            mock_mgr.get_session_by_id.return_value = {
                "metadata": {"transferred_to": "servicer_1", "last_transferred_at": "2026-09-16 10:00:00"}
            }
            mock_mgr.get_messages.return_value = _human_period_messages()
            mock_redis.acquire_lock.return_value = True
            mock_redis.get.return_value = "王五"
            mock_gw.chat_lite = AsyncMock(return_value=response)

            await ExternalPushHumanAdapter.execute(payload)

            # 摘要计费：billing_audit §3.5 条件 A，source 带人工期标识
            mock_bill.assert_called_once()
            assert mock_bill.call_args.kwargs["source"] == "external_push_human_pre-sales"
            assert "人工期对话摘要" in mock_bill.call_args.kwargs["user_message"]

            mock_loop.assert_awaited_once()
            kwargs = mock_loop.call_args.kwargs
            assert kwargs["system_prompt"].find("人工接待期推送特例") > 0
            assert "微信咨询（人工）" in kwargs["system_prompt"]
            assert "[人工客服] 张三您好，我是李四" in kwargs["user_message"]
            assert "[人工接待] 客户：那我再考虑下" in kwargs["user_message"]
            assert "客户询价后表示再考虑" in kwargs["user_message"]
            assert "转人工客服工号：servicer_1" in kwargs["user_message"]
            assert "转人工客服姓名：王五" in kwargs["user_message"]
            assert "转人工时间：2026-09-16 10:00:00" in kwargs["user_message"]
            assert "client_token：tok123" in kwargs["user_message"]
            assert kwargs["billing_source"] == "external_push_human_pre-sales"
            assert kwargs["trace_prefix"] == "recap:external_push_human"
            # 成功路径不删冷却键（靠 TTL 过期）
            mock_redis.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_summary_parse_failure_fallback(self):
        """摘要输出非法 JSON：降级提示语，推送流程继续"""
        payload = _make_payload()
        response = {"content": "这不是 JSON", "usage": None}
        with patch(f"{_TOPIC_NS}._collect_context", return_value=_make_ctx()), \
                patch(f"{_TOPIC_NS}._load_tenant_doc", return_value=_FAKE_DOC), \
                patch(f"{_TOPIC_NS}._get_agent_token", return_value="agent_tok"), \
                patch("src.channels.session.channel_session_manager") as mock_mgr, \
                patch(f"{_TOPIC_NS}.redis_client") as mock_redis, \
                patch(f"{_TOPIC_NS}._delegate_login", return_value={"client_token": "tok123"}), \
                patch(f"{_TOPIC_NS}._run_push_loop") as mock_loop, \
                patch("src.llm.gateway.llm_gateway") as mock_gw, \
                patch("src.services.session_record.record_background_llm_usage"):
            mock_mgr.get_session_by_id.return_value = {"metadata": {"transferred_to": "servicer_1"}}
            mock_mgr.get_messages.return_value = _human_period_messages()
            mock_redis.acquire_lock.return_value = True
            mock_redis.get.return_value = None
            mock_gw.chat_lite = AsyncMock(return_value=response)

            await ExternalPushHumanAdapter.execute(payload)

            user_message = mock_loop.call_args.kwargs["user_message"]
            assert "摘要生成失败" in user_message

    @pytest.mark.asyncio
    async def test_push_failure_deletes_cooldown(self):
        """推送循环失败：删冷却键允许下一条人工期消息重试，异常上抛由 runner 吞掉"""
        payload = _make_payload()
        with patch(f"{_TOPIC_NS}._collect_context", return_value=_make_ctx()), \
                patch(f"{_TOPIC_NS}._load_tenant_doc", return_value=_FAKE_DOC), \
                patch(f"{_TOPIC_NS}._get_agent_token", return_value="agent_tok"), \
                patch("src.channels.session.channel_session_manager") as mock_mgr, \
                patch(f"{_TOPIC_NS}.redis_client") as mock_redis, \
                patch(f"{_TOPIC_NS}._delegate_login", return_value={"client_token": "tok123"}), \
                patch(f"{_TOPIC_NS}._run_push_loop", side_effect=RuntimeError("push failed")), \
                patch("src.llm.gateway.llm_gateway") as mock_gw, \
                patch("src.services.session_record.record_background_llm_usage"):
            mock_mgr.get_session_by_id.return_value = {"metadata": {"transferred_to": "servicer_1"}}
            mock_mgr.get_messages.return_value = _human_period_messages()
            mock_redis.acquire_lock.return_value = True
            mock_redis.get.return_value = None
            mock_gw.chat_lite = AsyncMock(return_value={"content": _summary_json(), "usage": None})

            with pytest.raises(RuntimeError):
                await ExternalPushHumanAdapter.execute(payload)

            mock_redis.delete.assert_called_once()


class TestResolveTransferInfo:
    def test_session_metadata_primary(self):
        """会话 metadata 优先：transferred_to + Redis 姓名反查"""
        with patch(f"{_TOPIC_NS}.redis_client") as mock_redis:
            mock_redis.get.return_value = "王五"
            info = _resolve_transfer_info("tenant_abc", {
                "transferred_to": "servicer_1",
                "last_transferred_at": "2026-09-16 10:00:00",
            })
        assert info == {
            "servicer_userid": "servicer_1",
            "servicer_name": "王五",
            "transferred_at": "2026-09-16 10:00:00",
        }

    def test_lead_fallback(self):
        """会话 metadata 缺失：已留资会话回退线索事件驱动回写的字段（姓名同理）"""
        lead = {
            "transferred_to": "servicer_2",
            "servicer_name": "赵六",
            "last_human_transfer_at": datetime(2026, 9, 16, 9, 30, 0),
        }
        with patch(f"{_TOPIC_NS}.redis_client") as mock_redis, \
                patch("src.saas.db.lead_capture_db.LeadCaptureDB") as mock_db:
            mock_redis.get.return_value = None
            mock_db.get_by_id.return_value = lead
            info = _resolve_transfer_info("tenant_abc", {
                "lead_capture": {"lead_id": "lead_lc_x"},
            })
        assert info["servicer_userid"] == "servicer_2"
        assert info["servicer_name"] == "赵六"
        assert info["transferred_at"] == "2026-09-16 09:30:00"


class TestRunPushLoopBuilderFallback:
    @pytest.mark.asyncio
    async def test_default_builders_used_when_no_override(self):
        """回归：_run_push_loop 不传 system_prompt/user_message 时走默认 builder（external_push 行为不变）"""
        payload = _make_payload(user_content="客户问价格", assistant_reply="AI 报价")
        ctx = _make_ctx()
        meta = {"login_url": "https://erp.example.com/api/delegate-login", "user_token_name": "client_token"}
        summary = {"customer_need": "n", "reply_summary": "r", "customer_name_hint": ""}

        executor = MagicMock()
        executor.execute = AsyncMock(return_value={"success": True})
        registry = MagicMock()
        registry.get_tool_definitions.return_value = []
        holder: list = []
        with patch("src.services.recap.tasks.external_push._create_tool_runtime", return_value=(registry, executor, holder)), \
                patch("src.services.recap.tasks.external_push._build_system_prompt", return_value="SYS_DEFAULT") as mock_sys, \
                patch("src.services.recap.tasks.external_push._build_user_message", return_value="USER_DEFAULT") as mock_user, \
                patch("src.llm.gateway.llm_gateway") as mock_gw, \
                patch("src.services.session_record.record_background_llm_usage"):
            # 无 tool_calls -> 第一轮即退出；无报告 -> raise（不关心异常，只断言 builder 回退）
            mock_gw.chat_lite = AsyncMock(return_value={"content": "done", "usage": None})
            with pytest.raises(RuntimeError):
                await _run_push_loop(
                    payload, ctx, summary, "DOC", meta, "agent_tok",
                    {"client_token": "t"}, "外部推送",
                )
            mock_sys.assert_called_once()
            mock_user.assert_called_once()
