"""转人工后回复抑制闸门单测（2026-10-09 生产事故回归锁定）

场景：wecom_kf 会话中 LLM 成功调用 transfer_to_human 后，微信客服接口会拒收
该会话后续所有机器人消息（errcode=95018）。渠道层 prepare_response/send_response
依赖 _has_successful_transfer_to_human 判定本轮是否已成功转人工，命中则跳过发送。

该轮计费与 trace 由 Runner 侧结算（finalizer.settle_record + worker._persist_trace），
与渠道层是否抑制发送无关；本测试锁定抑制判定契约本身。
"""

import json

import pytest

from src.saas.api.channel_routes import _has_successful_transfer_to_human

pytestmark = pytest.mark.agent


def _transfer_exchange(success: bool):
    """构造一对 assistant tool_calls + tool 结果消息"""
    call_id = "call_transfer_1"
    assistant = {
        "role": "assistant",
        "tool_calls": [{"id": call_id, "function": {"name": "transfer_to_human",
                                                    "arguments": "{}"}}],
    }
    tool = {
        "role": "tool",
        "tool_call_id": call_id,
        "content": json.dumps({"success": success, "message": "转接结果"}),
    }
    return [assistant, tool]


class TestHasSuccessfulTransferToHuman:
    def test_empty_tool_messages_returns_false(self):
        assert _has_successful_transfer_to_human([]) is False
        assert _has_successful_transfer_to_human(None) is False

    def test_successful_transfer_detected(self):
        assert _has_successful_transfer_to_human(_transfer_exchange(True)) is True

    def test_failed_transfer_not_detected(self):
        # 工具执行失败（如微信接口报错）时会话未切人工，不应抑制发送
        assert _has_successful_transfer_to_human(_transfer_exchange(False)) is False

    def test_transfer_without_tool_result_not_detected(self):
        # 只有 tool_calls 没有 tool 结果（执行被中断）时不应抑制
        assistant = {
            "role": "assistant",
            "tool_calls": [{"id": "call_x", "function": {"name": "transfer_to_human",
                                                         "arguments": "{}"}}],
        }
        assert _has_successful_transfer_to_human([assistant]) is False

    def test_call_id_mismatch_not_detected(self):
        # tool 结果的 tool_call_id 与 transfer 调用不配对时不应抑制
        assistant, tool = _transfer_exchange(True)
        tool["tool_call_id"] = "call_other"
        assert _has_successful_transfer_to_human([assistant, tool]) is False

    def test_other_tools_ignored(self):
        # 其它工具调用成功不触发抑制
        assistant = {
            "role": "assistant",
            "tool_calls": [{"id": "call_k", "function": {"name": "knowledge_base_search",
                                                         "arguments": "{}"}}],
        }
        tool = {"role": "tool", "tool_call_id": "call_k",
                "content": json.dumps({"success": True})}
        assert _has_successful_transfer_to_human([assistant, tool]) is False

    def test_dict_content_accepted(self):
        # tool content 直接是 dict 时同样识别
        assistant, tool = _transfer_exchange(True)
        tool["content"] = {"success": True}
        assert _has_successful_transfer_to_human([assistant, tool]) is True

    def test_success_after_other_tools(self):
        # 转人工前有其它工具调用不影响判定
        other_assistant = {
            "role": "assistant",
            "tool_calls": [{"id": "call_s", "function": {"name": "skill_execute",
                                                         "arguments": "{}"}}],
        }
        other_tool = {"role": "tool", "tool_call_id": "call_s",
                      "content": json.dumps({"success": True})}
        assert _has_successful_transfer_to_human(
            [other_assistant, other_tool] + _transfer_exchange(True)) is True
