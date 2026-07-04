"""工具链边界对齐 + tool_calls 还原 回归测试（v3.2.2）

覆盖死循环 bug 的根因和修复：
1. _load_messages 应把 metadata.tool_calls 还原到顶层（修复前缺失，导致所有 tool 被判孤儿）
2. _drop_orphan_tool_messages 对孤儿 tool 降级保留（不删除），避免占消息数却进不了 COMPRESS
3. _split_messages 的 HEADER/TAIL 边界对齐到安全切断点，不在 assistant(tc)→tool 之间切断
4. 死循环回归：含大量工具链的长会话，COMPRESS 区足够大（不再只有 2-5 条）
"""

from typing import Any, Dict, List

import pytest

from src.memory.mid_term import ContextCompressionService


@pytest.fixture
def service(mid_term_settings):
    return ContextCompressionService(settings_cfg=mid_term_settings)


# ============== _extract_tool_calls 兼容性 ==============

def test_extract_tool_calls_toplevel():
    """顶层 tool_calls 优先读取"""
    msg = {"role": "assistant", "tool_calls": [{"id": "c1"}]}
    assert ContextCompressionService._extract_tool_calls(msg) == [{"id": "c1"}]


def test_extract_tool_calls_from_metadata():
    """顶层无 tool_calls 时，从 metadata 读取（DB 存储格式）"""
    msg = {
        "role": "assistant",
        "metadata": {"tool_calls": [{"id": "c1", "function": {"name": "search"}}]},
    }
    assert len(ContextCompressionService._extract_tool_calls(msg)) == 1


def test_extract_tool_calls_from_metadata_str():
    """metadata 是 JSON 字符串时也能解析"""
    import json

    msg = {
        "role": "assistant",
        "metadata": json.dumps({"tool_calls": [{"id": "c1"}]}),
    }
    assert len(ContextCompressionService._extract_tool_calls(msg)) == 1


def test_extract_tool_calls_none():
    """无 tool_calls 返回空列表"""
    assert ContextCompressionService._extract_tool_calls({"role": "user"}) == []
    assert ContextCompressionService._extract_tool_calls({"role": "assistant"}) == []


# ============== _is_safe_split_point ==============


def test_is_safe_split_point():
    """user 和 assistant(无tc) 是安全切断点；tool 和 assistant(tc) 不是"""
    assert ContextCompressionService._is_safe_split_point({"role": "user"}) is True
    assert ContextCompressionService._is_safe_split_point({"role": "assistant"}) is True
    assert ContextCompressionService._is_safe_split_point({"role": "tool"}) is False
    assert (
        ContextCompressionService._is_safe_split_point(
            {"role": "assistant", "tool_calls": [{"id": "c1"}]}
        )
        is False
    )
    assert ContextCompressionService._is_safe_split_point(None) is True


# ============== _drop_orphan_tool_messages 降级保留 ==============


def test_orphan_tool_downgraded_not_dropped():
    """孤儿 tool 应降级为 assistant 文本保留（v3.2.2：不再删除）"""
    msgs = [
        {"role": "user", "content": "ask", "id": 1},
        {"role": "tool", "content": "orphan", "id": 2, "metadata": {"tool_name": "search"}},
        {"role": "user", "content": "next", "id": 3},
    ]
    out = ContextCompressionService._drop_orphan_tool_messages(msgs)
    assert len(out) == 3  # 不删除，数量不变
    assert all(m["role"] != "tool" for m in out)  # 无 role=tool
    # id 保留（用于 compressed_ids）
    assert any(m.get("id") == 2 for m in out)
    # 降级消息带工具名标记
    downgraded = [m for m in out if m.get("id") == 2][0]
    assert downgraded["role"] == "assistant"
    assert "search" in downgraded["content"]


def test_paired_tool_not_downgraded():
    """配对的 tool（前面是 assistant(tool_calls)）不降级"""
    msgs = [
        {"role": "user", "content": "ask"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "content": "result", "id": 99},
    ]
    out = ContextCompressionService._drop_orphan_tool_messages(msgs)
    assert len(out) == 3
    tool_msg = [m for m in out if m.get("id") == 99][0]
    assert tool_msg["role"] == "tool"  # 保持不变


# ============== _split_messages 工具链边界对齐 ==============


def _build_unit(
    uid: int,
    with_tool: bool = False,
    tool_name: str = "search",
) -> List[Dict[str, Any]]:
    """构造一个完整对话单元：user → [assistant(tc) → tool] → assistant(最终)"""
    unit = [{"role": "user", "content": f"question {uid}", "id": uid * 10 + 1}]
    if with_tool:
        unit.append(
            {
                "role": "assistant",
                "content": "",
                "id": uid * 10 + 2,
                "tool_calls": [
                    {"id": f"call_{uid}", "type": "function", "function": {"name": tool_name}}
                ],
            }
        )
        unit.append(
            {"role": "tool", "content": f"result {uid}", "id": uid * 10 + 3, "metadata": {"tool_name": tool_name}}
        )
    unit.append({"role": "assistant", "content": f"answer {uid}", "id": uid * 10 + (4 if with_tool else 2)})
    return unit


def test_split_does_not_cut_tool_chain_in_compress(service):
    """COMPRESS 区内不能出现孤立的 tool（配对的 assistant(tc) 必须同段）"""
    # 40 个带工具的完整单元 = 160 条 + 3 header(user) → 163 条，超过 33
    msgs = []
    for i in range(3):
        msgs.append({"role": "user", "content": f"header {i}", "id": 9000 + i})
    for uid in range(40):
        msgs.extend(_build_unit(uid, with_tool=True))

    header, compress, tail = service._split_messages(msgs)

    # HEADER 末尾必须是安全切断点
    assert ContextCompressionService._is_safe_split_point(header[-1])
    # TAIL 开头必须是安全切断点
    assert ContextCompressionService._is_safe_split_point(tail[0])
    # COMPRESS 区足够大（远超 5 条，证明死循环已消除）
    assert len(compress) > 50, f"COMPRESS 区只有 {len(compress)} 条，死循环未消除"


def test_split_tail_boundary_safe(service):
    """TAIL 起点对齐到安全切断点，不在 tool 或 assistant(tc) 上"""
    msgs = []
    for i in range(3):
        msgs.append({"role": "user", "content": f"header {i}", "id": i + 1})
    # 40 个普通单元
    for uid in range(40):
        msgs.extend(_build_unit(uid, with_tool=False))
    # 末尾插入一个带工具的单元（让 TAIL 区包含工具链）
    msgs.extend(_build_unit(999, with_tool=True))

    header, compress, tail = service._split_messages(msgs)

    # TAIL 开头是安全切断点
    assert ContextCompressionService._is_safe_split_point(tail[0]), (
        f"TAIL 开头不是安全切断点: role={tail[0].get('role')}"
    )


# ============== 死循环回归（核心场景）==============


def test_death_loop_regression(service):
    """死循环回归：大量工具链消息，COMPRESS 区足够大，一次压缩可降到阈值以下。

    修复前：tool_calls 未还原 → 所有 tool 判孤儿删除 → COMPRESS 只有 2-5 条
            → 消息数永远 >=200 → 死循环触发压缩（生产 2 小时 13 次）
    修复后：tool_calls 还原 + 边界对齐 → COMPRESS 包含完整工具链 → 一次压到位
    """
    msgs = []
    # 3 条 header
    for i in range(3):
        msgs.append({"role": "user", "content": f"header {i}", "id": i + 1})
    # 70 个带工具的完整单元（每个 4 条 = 280 条），模拟旅游顾问的多轮工具调用
    for uid in range(70):
        msgs.extend(_build_unit(uid, with_tool=True, tool_name="search_hotels"))
    # 总计 283 条 > 200 阈值

    header, compress, tail = service._split_messages(msgs)

    # 核心断言：COMPRESS 区远大于 5 条（修复前只有 5 条）
    assert len(compress) > 100, f"COMPRESS 区 {len(compress)} 条，死循环未消除（修复前=5）"
    # 压缩后剩余 = HEADER + TAIL ≈ 33 条，远低于 200 阈值，不会再次触发
    remaining = len(header) + len(tail)
    assert remaining < 200, f"压缩后剩余 {remaining} 条，仍会触发压缩"


def test_metadata_only_tool_calls_works(service):
    """tool_calls 只在 metadata（不在顶层）时也能正确配对（生产数据场景）"""
    # 模拟 DB 返回的格式：tool_calls 在 metadata，顶层无
    msgs = []
    for i in range(3):
        msgs.append({"role": "user", "content": f"header {i}", "id": i + 1})
    for uid in range(40):
        # user
        msgs.append({"role": "user", "content": f"q {uid}", "id": uid * 10 + 1})
        # assistant(tc) —— tool_calls 只在 metadata（生产 DB 格式）
        msgs.append(
            {
                "role": "assistant",
                "content": "",
                "id": uid * 10 + 2,
                "metadata": {
                    "tool_calls": [{"id": f"c{uid}", "function": {"name": "search"}}]
                },
            }
        )
        # tool 结果
        msgs.append(
            {"role": "tool", "content": f"r {uid}", "id": uid * 10 + 3, "metadata": {"tool_name": "search"}}
        )
        # assistant 最终回复
        msgs.append({"role": "assistant", "content": f"a {uid}", "id": uid * 10 + 4})

    # 先验证 _drop_orphan_tool_messages 不误删配对的 tool
    cleaned = ContextCompressionService._drop_orphan_tool_messages(msgs)
    tool_count = sum(1 for m in cleaned if m["role"] == "tool")
    assert tool_count == 40, f"配对的 tool 被误删: 期望 40，实际 {tool_count}"

    # 再验证分段
    header, compress, tail = service._split_messages(msgs)
    assert len(compress) > 50
