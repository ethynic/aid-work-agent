"""
external_push 推送循环累计摘要注入单元测试（方案 B）

覆盖：
1. _extract_field_values 递归提取（嵌套 dict/list、多字段、空值跳过）
2. _run_push_loop：http_api 查询结果命中 summary_fields 后，当轮工具结果之后
   注入「当前值 + 回写规则」提醒，且整个循环至多注入一次
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.recap.runner import RecapPayload
from src.services.recap.tasks.external_push import (
    _extract_field_values,
    _run_push_loop,
)


def _tool_call(name: str, arguments: str, call_id: str) -> dict:
    return {"id": call_id, "function": {"name": name, "arguments": arguments}}


def _llm_response(content="", tool_calls=None) -> dict:
    return {"content": content, "tool_calls": tool_calls or [], "usage": None}


def _make_payload() -> RecapPayload:
    return RecapPayload(
        tenant_id="tenant_abc",
        session_id="tenant_abc_wecom_kf_kf1_user1_pre-sales",
        subagent_name="pre-sales",
        round_message_id="msg_1",
        user_content="hi",
        assistant_reply="hello",
        record_service=None,
        user_id="user_1",
        trace_id=None,
        enqueued_at=time.time(),
    )


# ============== _extract_field_values ==============

class TestExtractFieldValues:
    def test_nested_dict_and_list(self):
        obj = {
            "data": {
                "list": [
                    {"id": 1, "genjinhuizongzhaiyao": "2026-9-18, 想了解价格"},
                    {"zhuangtai": "感兴趣"},
                ]
            }
        }
        found = _extract_field_values(obj, ["genjinhuizongzhaiyao", "zhuangtai"])
        assert found == {
            "genjinhuizongzhaiyao": "2026-9-18, 想了解价格",
            "zhuangtai": "感兴趣",
        }

    def test_blank_value_skipped(self):
        assert _extract_field_values({"genjinhuizongzhaiyao": "  "}, ["genjinhuizongzhaiyao"]) == {}
        assert _extract_field_values({"genjinhuizongzhaiyao": None}, ["genjinhuizongzhaiyao"]) == {}

    def test_first_occurrence_wins(self):
        obj = {"a": {"genjinhuizongzhaiyao": "first"}, "b": {"genjinhuizongzhaiyao": "second"}}
        assert _extract_field_values(obj, ["genjinhuizongzhaiyao"]) == {
            "genjinhuizongzhaiyao": "first"
        }

    def test_no_match(self):
        assert _extract_field_values({"other": "x"}, ["genjinhuizongzhaiyao"]) == {}


# ============== 方案 A：规则 8 同日合并自查条款 ==============

class TestSystemPromptSelfCheck:
    def test_self_check_clause_present(self):
        from src.services.recap.tasks.external_push import _build_system_prompt

        prompt = _build_system_prompt("doc", {
            "user_token_name": "client_token",
            "external_userid_field": "unionid",
            "user_token_header": "Client-Authorize-Token",
            "agent_token_header": "Api-Authorize-Token",
        })
        assert "同一日期在整个摘要中只能出现一条" in prompt
        assert "严禁在已有当天条目的情况下再追加同日期新条目" in prompt


# ============== _run_push_loop 摘要提醒注入 ==============

class TestPushLoopSummaryInjection:
    def _patch_runtime_with_holder(self, executor, report_holder):
        runtime = (MagicMock(), executor, report_holder)
        return patch(
            "src.services.recap.tasks.external_push._create_tool_runtime",
            return_value=runtime,
        )

    @pytest.mark.asyncio
    async def test_summary_reminder_injected_once(self):
        """查询结果命中累计摘要字段后注入提醒；后续轮次不再重复注入"""
        meta = {
            "summary_fields": "genjinhuizongzhaiyao",
            "user_token_name": "client_token",
            "user_token_header": "Client-Authorize-Token",
            "agent_token_header": "Api-Authorize-Token",
        }
        payload = _make_payload()
        ctx = {"subagent": "pre-sales", "external_userid": "ext_1"}

        executor = MagicMock()
        report_holder: list = []

        async def _execute(name, args, context=None):
            if name == "report_push_result":
                report = {"success": True, "detail": "ok"}
                report_holder.append(report)
                return {"success": True}
            return {"success": True, "data": {"list": [{"genjinhuizongzhaiyao": "2026-9-18, 想了解价格"}]}}

        executor.execute = AsyncMock(side_effect=_execute)

        responses = [
            _llm_response(tool_calls=[_tool_call(
                "http_api", '{"url": "https://x/list", "method": "POST", "params_json": "{}"}', "c1")]),
            _llm_response(tool_calls=[_tool_call(
                "report_push_result", '{"success": true, "detail": "ok"}', "c2")]),
        ]
        gateway = MagicMock()
        gateway.chat_lite = AsyncMock(side_effect=responses)

        with self._patch_runtime_with_holder(executor, report_holder), \
                patch("src.llm.gateway.llm_gateway", gateway), \
                patch("src.services.session_record.record_background_llm_usage"):
            detail = await _run_push_loop(payload, ctx, {}, "doc", meta, "tok", {"client_token": "t"})

        assert detail == "ok"
        calls = gateway.chat_lite.call_args_list
        round2_messages = calls[1].args[0]
        reminders = [
            m for m in round2_messages
            if m.get("role") == "user" and "系统提醒" in (m.get("content") or "")
        ]
        assert len(reminders) == 1
        assert "2026-9-18, 想了解价格" in reminders[0]["content"]
        assert "同一日期" in reminders[0]["content"]

    @pytest.mark.asyncio
    async def test_no_summary_in_query_no_injection(self):
        """查询结果无摘要字段时不注入提醒"""
        meta = {"summary_fields": "genjinhuizongzhaiyao"}
        payload = _make_payload()
        ctx = {"subagent": "pre-sales", "external_userid": "ext_1"}

        executor = MagicMock()
        report_holder: list = []

        async def _execute(name, args, context=None):
            if name == "report_push_result":
                report_holder.append({"success": True, "detail": "ok"})
                return {"success": True}
            return {"success": True, "data": {"list": [{"id": 1}]}}

        executor.execute = AsyncMock(side_effect=_execute)

        responses = [
            _llm_response(tool_calls=[_tool_call(
                "http_api", '{"url": "https://x/list", "method": "POST", "params_json": "{}"}', "c1")]),
            _llm_response(tool_calls=[_tool_call(
                "report_push_result", '{"success": true, "detail": "ok"}', "c2")]),
        ]
        gateway = MagicMock()
        gateway.chat_lite = AsyncMock(side_effect=responses)

        with self._patch_runtime_with_holder(executor, report_holder), \
                patch("src.llm.gateway.llm_gateway", gateway), \
                patch("src.services.session_record.record_background_llm_usage"):
            await _run_push_loop(payload, ctx, {}, "doc", meta, "tok", {"client_token": "t"})

        round2_messages = gateway.chat_lite.call_args_list[1].args[0]
        assert not [
            m for m in round2_messages
            if m.get("role") == "user" and "系统提醒" in (m.get("content") or "")
        ]
