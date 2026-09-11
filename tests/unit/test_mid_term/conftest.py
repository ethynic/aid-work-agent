"""fixtures for mid_term compression tests."""

from contextlib import contextmanager
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.settings import MidTermMemoryConfig


@pytest.fixture
def mid_term_settings() -> MidTermMemoryConfig:
    """标准中期记忆配置（用默认值）"""
    return MidTermMemoryConfig()


@pytest.fixture(autouse=True)
def _no_summary_llm_api_key(request, monkeypatch):
    """默认屏蔽所有 summary_llm provider 的 API key（autouse）。

    这样 _call_summary_llm 走 fallback 到 mock_llm_for_summary，
    避免单元测试真的打到第三方 LLM API。

    标记了 pytest.mark.allow_direct_llm 的用例不应用此屏蔽，
    以便测试直连路径（_call_summary_llm_direct）。
    """
    marker = request.node.get_closest_marker("allow_direct_llm")
    if marker is not None:
        # 该测试需要真实环境变量 / 真实 _get_provider_api_key，不屏蔽
        return
    monkeypatch.setattr(
        "src.memory.mid_term._get_provider_api_key",
        lambda provider: None,
    )


def _make_message(
    role: str,
    content: str,
    msg_id: Optional[int] = None,
    tool_calls: Optional[List[Dict[str, Any]]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构造单条测试消息

    Args:
        role: user / assistant / tool
        content: 文本内容
        msg_id: 模拟的 BIGINT id（用于压缩时记录 compressed_message_ids）
        tool_calls: assistant 的 tool_calls
        metadata: 元数据
    """
    msg: Dict[str, Any] = {"role": role, "content": content}
    if msg_id is not None:
        msg["id"] = msg_id
    if tool_calls is not None:
        msg["tool_calls"] = tool_calls
    if metadata is not None:
        msg["metadata"] = metadata
    return msg


@pytest.fixture
def make_message():
    """工厂 fixture：构造单条测试消息"""
    return _make_message


@pytest.fixture
def build_simple_messages():
    """工厂 fixture：构造 N 轮简单 user/assistant 对话"""

    def _make(count: int, prefix: str = "msg") -> List[Dict[str, Any]]:
        msgs: List[Dict[str, Any]] = []
        for i in range(count):
            msgs.append(_make_message("user", f"{prefix} user {i}", msg_id=2 * i + 1))
            msgs.append(_make_message("assistant", f"{prefix} assistant {i}", msg_id=2 * i + 2))
        return msgs

    return _make


@pytest.fixture
def build_messages_with_tool_chain():
    """工厂 fixture：构造包含完整工具链的 messages（user → assistant(tool_calls) → tool → assistant）"""

    def _make(prefix: str = "tc") -> List[Dict[str, Any]]:
        return [
            _make_message("user", f"{prefix} user asks weather", msg_id=1),
            _make_message(
                "assistant",
                f"{prefix} calling tool",
                msg_id=2,
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "weather",
                            "arguments": '{"city": "Beijing"}',
                        },
                    }
                ],
            ),
            _make_message(
                "tool",
                f"{prefix} result: sunny, 25C",
                msg_id=3,
                metadata={"tool_name": "weather"},
            ),
            _make_message("assistant", f"{prefix} final: Beijing is sunny", msg_id=4),
        ]

    return _make


@pytest.fixture
def mock_llm_for_summary():
    """mock LLM gateway，用于摘要调用。

    摘要走 chat_lite（关思考）；默认返回成功，可通过设置
    `gateway.chat_lite.return_value` 或 `side_effect` 在测试中改写。
    """
    gateway = MagicMock()
    gateway.chat_lite = AsyncMock(return_value={
        "content": "## 用户与背景\n- test user\n\n## 关键事实与决策\n- decided X",
        "tool_calls": None,
        "finish_reason": "stop",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    })
    return gateway


@pytest.fixture
def fake_db_connection(monkeypatch):
    """mock get_db_connection 上下文管理器，提供可控的 cursor。

    返回 (mock_conn, mock_cursor) 元组，测试可设置 cursor.execute / fetchone / fetchall 的返回值。
    每次调用 get_db_connection() 都返回同一个 mock_conn。
    """
    mock_cursor = MagicMock()
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_conn.commit = MagicMock()
    mock_conn.rollback = MagicMock()

    @contextmanager
    def _ctx():
        yield mock_conn

    import src.memory.mid_term as mid_term_mod
    import src.db.models as models_mod
    monkeypatch.setattr(mid_term_mod, "get_db_connection", _ctx)
    monkeypatch.setattr(models_mod, "get_db_connection", _ctx)
    return mock_conn, mock_cursor
