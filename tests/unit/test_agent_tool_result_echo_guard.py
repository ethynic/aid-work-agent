"""tool_result 回显兜底检测单元测试

背景：2026-08-19 线上事故，qwen3.7-flash 概率性把工具结果按 Anthropic
tool_result 语义回显为最终回复（见 docs/incidents/qwen-tool-message-cache-echo-incident.md）。
Agent 主循环/子智能体循环在收到该形态回复时会重试一次 LLM 调用，
本文件测试检测函数本身的判定边界。
"""
import pytest

from src.core.agent import Agent

# 事故真实回显开头（截取自 api3 容器 LLM 日志）
INCIDENT_ECHO = (
    '[{"id": "chatcmpl-9531", "tool_call_id": "call_4028e3a9c67d4b3fa752f8", '
    '"type": "tool_result", "function": {"name": "knowledge_base_search"}, '
    '"result": "[{\\"text\\": \\"| 59.9 | 72小时内发货\\"}]"}]'
)


class TestIsToolResultEcho:
    def test_incident_echo_detected(self):
        # 事故原始形态必须命中
        assert Agent._is_tool_result_echo(INCIDENT_ECHO) is True

    def test_leading_whitespace_tolerated(self):
        assert Agent._is_tool_result_echo("  \n" + INCIDENT_ECHO) is True

    def test_normal_text_not_detected(self):
        assert Agent._is_tool_result_echo("为您推荐以下商品：...") is False

    def test_normal_text_starting_with_json_array_not_detected(self):
        # 正常业务 JSON 数组回复（不含 tool_result）不命中
        assert Agent._is_tool_result_echo('[{"id": 1, "name": "商品A"}]') is False

    def test_tool_result_beyond_head_window_not_detected(self):
        # "tool_result" 出现在 300 字符窗口之外时不命中（窗口限定降低误报面）
        content = '[{"id": "x", "type": "text", "text": "' + "a" * 400 + '"}, {"type": "tool_result"}]'
        assert Agent._is_tool_result_echo(content) is False

    def test_tool_result_late_no_id_prefix_not_detected(self):
        # 不以 [{"id" 开头的不命中，避免误伤以普通文本开头的回复
        assert Agent._is_tool_result_echo('查询结果如下：[{"id": "x", "type": "tool_result"}]') is False

    def test_non_string_content_not_detected(self):
        assert Agent._is_tool_result_echo(None) is False
        assert Agent._is_tool_result_echo([]) is False

    def test_empty_string_not_detected(self):
        assert Agent._is_tool_result_echo("") is False
