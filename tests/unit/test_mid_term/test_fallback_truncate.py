"""_fallback_truncate 降级路径测试"""

import pytest

from src.memory.mid_term import ContextCompressionService


@pytest.fixture
def service(mid_term_settings):
    return ContextCompressionService(settings_cfg=mid_term_settings)


def test_fallback_produces_structured_skeleton(service):
    """降级路径输出结构化骨架（含 user 消息、工具调用名）"""
    compress = [
        {"role": "user", "content": "请帮我查天气", "id": 1},
        {
            "role": "assistant",
            "content": "",
            "id": 2,
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "weather", "arguments": '{"city":"Beijing"}'}},
            ],
        },
        {"role": "tool", "content": "sunny 25C", "id": 3, "metadata": {"tool_name": "weather"}},
        {"role": "assistant", "content": "Beijing is sunny", "id": 4},
        {"role": "user", "content": "谢谢", "id": 5},
    ]
    text = service._fallback_truncate(compress)
    # 结构化标题
    assert "降级摘要" in text
    # 两条 user 消息
    assert "请帮我查天气" in text
    assert "谢谢" in text
    # 工具名出现在「调用的工具」段
    assert "weather" in text
    # tool 结果不应出现（降级丢弃结果）
    assert "sunny 25C" not in text
    # assistant 最终回复保留
    assert "Beijing is sunny" in text


def test_fallback_truncates_long_content(service, mid_term_settings):
    """长 user 消息应截断到 500 字符"""
    long_text = "a" * 1000
    compress = [{"role": "user", "content": long_text, "id": 1}]
    text = service._fallback_truncate(compress)
    # 500 字符内容应被保留（不含原 1000 个 a）
    assert "a" * 500 in text
    assert "a" * 600 not in text


def test_fallback_empty_compress(service):
    """COMPRESS 为空时仍输出标题"""
    text = service._fallback_truncate([])
    assert "降级摘要" in text
