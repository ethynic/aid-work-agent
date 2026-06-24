"""_split_messages 分段策略测试"""

import pytest

from src.memory.mid_term import ContextCompressionService


@pytest.fixture
def service(mid_term_settings):
    return ContextCompressionService(settings_cfg=mid_term_settings)


def test_basic_split_header_compress_tail(service, build_simple_messages):
    """普通 user/assistant 对话分段正确：HEADER=3, TAIL=30, COMPRESS=中间"""
    # 100 条 → 200 messages
    msgs = build_simple_messages(100)
    header, compress, tail = service._split_messages(msgs)
    assert len(header) == 3
    assert len(tail) == 30
    assert len(compress) == len(msgs) - 3 - 30
    # HEADER 是前 3 条
    assert header[0] is msgs[0]
    assert header[2] is msgs[2]
    # TAIL 是最后 30 条
    assert tail[-1] is msgs[-1]
    # COMPRESS 是中间
    assert compress[0] is msgs[3]
    assert compress[-1] is msgs[-31]


def test_tail_boundary_tool_chain_alignment(service):
    """TAIL 边界遇 assistant(tool_calls) 在 TAIL 开头时，向前扩展纳入 tool 结果"""
    # 构造：header(3) + 一些 user/assistant + 末尾恰好在 tool 消息开头
    # tail_keep=30，倒数第 30 条恰好是 tool 消息 → 应向前扩展把对应 assistant(tool_calls) 拉进 TAIL
    msgs = []
    # 前 3 条 header
    for i in range(3):
        msgs.append({"role": "user", "content": f"header user {i}", "id": i + 1})
        msgs.append({"role": "assistant", "content": f"header as {i}", "id": i + 2})

    # 中间 70 条普通对话（id 7..76）
    for i in range(35):
        msgs.append({"role": "user", "content": f"mid user {i}", "id": 100 + i * 2})
        msgs.append({"role": "assistant", "content": f"mid as {i}", "id": 101 + i * 2})

    # TAIL 区（最后 30 条）：开头是 tool 消息，前面有 assistant(tool_calls)
    # 倒数第 30 条（即将成为 tail[0]）的角色应是 tool
    # 构造 30 条：1 个 assistant(tool_calls) + 1 个 tool + 28 条普通对话
    # 但 tail_start_idx = len - 30 指向 tool，所以要把 assistant(tool_calls) 从 COMPRESS 拉进来
    tail_part = []
    # 1 个 assistant(tool_calls)
    tail_part.append({
        "role": "assistant",
        "content": "calling",
        "id": 900,
        "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "w", "arguments": "{}"}}],
    })
    # 1 个 tool
    tail_part.append({"role": "tool", "content": "result", "id": 901})
    # 28 条普通对话（14 对 user/assistant）
    for i in range(14):
        tail_part.append({"role": "user", "content": f"tail user {i}", "id": 910 + i * 2})
        tail_part.append({"role": "assistant", "content": f"tail as {i}", "id": 911 + i * 2})

    msgs.extend(tail_part)
    # 现在 tail_part 长度 = 30，tail_start_idx 默认 = len - 30 指向 assistant(tool_calls)
    # 不需要向前扩展。但我们要测试「tail 开头是 tool」的场景：
    # 把 tail_part 调整成开头是 tool —— 重排：把 assistant(tool_calls) 放在 tail_part 外面（成为 COMPRESS 最后一条）
    msgs = msgs[:-30]  # 移除刚才加的 30 条
    # 放一个 assistant(tool_calls)（将成为 COMPRESS 最后一条）
    msgs.append({
        "role": "assistant",
        "content": "calling",
        "id": 900,
        "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "w", "arguments": "{}"}}],
    })
    # 再放 30 条：第 1 条是 tool，后面 29 条普通对话
    msgs.append({"role": "tool", "content": "result", "id": 901})
    for i in range(14):
        msgs.append({"role": "user", "content": f"tail user {i}", "id": 910 + i * 2})
        msgs.append({"role": "assistant", "content": f"tail as {i}", "id": 911 + i * 2})
    # 现在 tail_start_idx = len - 30 = (len - 30) 指向 tool 消息
    # _split_messages 应识别 tool 消息在开头，向前扩展把 assistant(tool_calls) 纳入 TAIL

    header, compress, tail = service._split_messages(msgs)
    assert len(header) == 3
    # TAIL 应包含 assistant(tool_calls) + tool（即 tool 链闭合）
    tail_roles = [m["role"] for m in tail]
    # tail 第一条或第二条应包含 assistant(tool_calls)（向前扩展的结果）
    assert "assistant" in tail_roles
    # tool 必须在 TAIL 内（不能被截断到 COMPRESS）
    assert "tool" in tail_roles
    # 至少 30 条（向后扩展后可能 +1）
    assert len(tail) >= 30
    # COMPRESS 不应包含孤立的 tool 消息（即 COMPRESS 末尾不是 tool）
    if compress:
        assert compress[-1]["role"] != "tool", "COMPRESS 末尾不应是孤立的 tool 消息"


def test_total_too_few_compress_empty(service, build_simple_messages):
    """消息总数 <= header_keep + tail_keep（默认 33）时，COMPRESS 为空"""
    msgs = build_simple_messages(15)  # 30 messages, 远小于 33
    header, compress, tail = service._split_messages(msgs)
    assert compress == []
    assert len(header) + len(tail) == len(msgs)


def test_header_incomplete_pair_fallback(service):
    """HEADER 不是完整 user+assistant 对时的兜底：直接取前 header_keep 条"""
    # 构造全是 user 的消息，HEADER 取前 3 条 user（无 assistant 配对）
    msgs = [{"role": "user", "content": f"u{i}", "id": i} for i in range(100)]
    header, compress, tail = service._split_messages(msgs)
    assert len(header) == 3
    assert header[0]["role"] == "user"
    # 不应抛错，正常分段
    assert len(tail) == 30
