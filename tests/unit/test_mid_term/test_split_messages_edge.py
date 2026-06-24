"""_split_messages 边界场景测试"""

import pytest

from src.config.settings import MidTermMemoryConfig
from src.memory.mid_term import ContextCompressionService


@pytest.fixture
def service(mid_term_settings):
    return ContextCompressionService(settings_cfg=mid_term_settings)


def test_empty_messages_returns_all_empty(service):
    """空列表：HEADER/COMPRESS/TAIL 全空"""
    header, compress, tail = service._split_messages([])
    assert header == []
    assert compress == []
    assert tail == []


def test_single_message_all_in_header(service):
    """单条消息：全部进 HEADER（总数 1 <= 33）"""
    m = [{"role": "user", "content": "hi", "id": 1}]
    header, compress, tail = service._split_messages(m)
    assert compress == []
    assert len(header) + len(tail) == 1


def test_total_exactly_equals_header_plus_tail_compress_empty(service):
    """总数恰好 == header_keep + tail_keep（33）：边界值，COMPRESS 为空"""
    # 默认 header_keep=3, tail_keep=30
    msgs = [{"role": "user", "content": str(i), "id": i} for i in range(33)]
    header, compress, tail = service._split_messages(msgs)
    assert compress == [], "总数恰好等于 header_keep+tail_keep 时 COMPRESS 必须为空"
    # 三段加起来 == 总数（不丢消息）
    assert len(header) + len(compress) + len(tail) == 33


def test_total_one_more_than_header_plus_tail_compress_has_one(service):
    """总数 == header_keep + tail_keep + 1（34）：COMPRESS 有 1 条"""
    msgs = [{"role": "user", "content": str(i), "id": i} for i in range(34)]
    header, compress, tail = service._split_messages(msgs)
    assert len(compress) == 1
    assert len(header) == 3
    # TAIL 长度 >= 30（可能因对齐扩展）
    assert len(tail) >= 30


def test_tail_start_is_isolated_tool_no_matching_calls(service):
    """TAIL 起点恰好是 tool 消息，但前面没有 assistant(tool_calls)（孤儿 tool）。
    代码应将该孤儿 tool 从 messages 中移除，重新分段，COMPRESS 末尾不再有孤立 tool。"""
    # 构造 35 条：header 3 + 1 user + 1 tool(孤儿) + 30 普通对话
    msgs = []
    for i in range(3):
        msgs.append({"role": "user", "content": f"h{i}", "id": i})
    # 填充使 TAIL 起点恰好落在 tool 上：tail_start_idx = 35 - 30 = 5
    # msgs[3]=user, msgs[4]=tool, msgs[5..34]=普通
    msgs.append({"role": "user", "content": "mid user", "id": 3})
    msgs.append({"role": "tool", "content": "orphan result", "id": 4, "metadata": {"tool_name": "x"}})
    for i in range(30):
        msgs.append({"role": "user", "content": f"tail {i}", "id": 100 + i})
    assert len(msgs) == 35

    header, compress, tail = service._split_messages(msgs)
    # 关键断言：COMPRESS 区末尾不能是孤立的 tool 消息（孤儿 tool 已被丢弃）
    if compress:
        assert compress[-1]["role"] != "tool", (
            "COMPRESS 末尾不应是孤立 tool 消息（会被 LLM 视为无主孤儿）"
        )


def test_header_consecutive_user_messages_no_assistant(service):
    """HEADER 区出现连续两个 user 消息（无 assistant 回复）——
    现有实现只按数量截取，不校验成对，验证至少不抛异常。"""
    msgs = []
    # 全部 user 消息，强制 HEADER 开头是连续 user
    for i in range(50):
        msgs.append({"role": "user", "content": f"u{i}", "id": i})
    header, compress, tail = service._split_messages(msgs)
    assert len(header) == 3
    # 前 3 条都是 user
    assert all(m["role"] == "user" for m in header)


def test_skill_summary_marker_in_compress_section(service):
    """设计 §3.5 的特殊消息（如 skill_summary 摘要）落入 COMPRESS 区不应导致分段异常。
    验证带特殊 metadata 的消息被正常分段。"""
    msgs = []
    # header 3
    for i in range(3):
        msgs.append({"role": "user", "content": f"h{i}", "id": i})
    # 中间 5 条：包含 skill_summary 标记的 assistant
    msgs.append({
        "role": "assistant",
        "content": "[skill summary] blah",
        "id": 100,
        "metadata": {"skill_summary": True, "skill_name": "weather"},
    })
    for i in range(4):
        msgs.append({"role": "user", "content": f"mid{i}", "id": 101 + i})
    # 末尾 30 条
    for i in range(30):
        msgs.append({"role": "user", "content": f"t{i}", "id": 200 + i})
    # 总 38 条
    header, compress, tail = service._split_messages(msgs)
    # skill_summary 消息（id=100）应在 COMPRESS 区
    compress_ids = [m.get("id") for m in compress]
    assert 100 in compress_ids, "skill_summary 消息应落入 COMPRESS 区"


def test_custom_header_tail_keep_config():
    """自定义 header_keep/tail_keep 配置：分段遵守自定义值"""
    cfg = MidTermMemoryConfig(header_keep=5, tail_keep=10)
    svc = ContextCompressionService(settings_cfg=cfg)
    # 50 条普通消息
    msgs = [{"role": "user", "content": str(i), "id": i} for i in range(50)]
    header, compress, tail = svc._split_messages(msgs)
    assert len(header) == 5
    # TAIL >= 10（可能因对齐扩展）
    assert len(tail) >= 10
    assert len(compress) >= 1


def test_no_message_loss_after_split(service):
    """分段后三段拼接应等于原消息列表（不丢不重）"""
    msgs = [{"role": "user", "content": str(i), "id": i} for i in range(100)]
    header, compress, tail = service._split_messages(msgs)
    combined_ids = [m.get("id") for m in header + compress + tail]
    original_ids = [m.get("id") for m in msgs]
    assert sorted(combined_ids) == sorted(original_ids), "分段不能丢消息或重复"
    assert len(combined_ids) == len(set(combined_ids)), "分段不能有重复 id"
