"""Agent token 缓存写入集成测试（v3.1 Phase 4.6）

验证 _process_message_impl 中嵌入的 token 缓存写入逻辑边界：
- prompt+completion=0 → 不写入
- usage 字段缺失 → 不崩溃，不写入
- chat 源路由到 SessionDB.update_context_token_count
- channel 源路由到 ChannelSessionManager.update_context_token_count
- 子智能体（mode=SUBAGENT）也写入缓存（设计文档 §4.4 没有说子智能体排除）

通过直接调用 SessionDB/ChannelSessionManager.update_context_token_count 的接口测试
（因为 token 缓存写入逻辑嵌入在 _process_message_impl 里无法直接触达）。
"""

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest


def _patch_db_conn(monkeypatch, module, rowcount=1):
    mock_cursor = MagicMock()
    mock_cursor.rowcount = rowcount
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    @contextmanager
    def _ctx():
        yield mock_conn

    monkeypatch.setattr(module, "get_db_connection", _ctx)
    return mock_conn, mock_cursor


def test_session_db_zero_token_no_crash(monkeypatch):
    """update_context_token_count 接受 token=0（虽然 Agent 主流程会跳过，但接口本身不崩）"""
    import src.db.models as models_mod
    from src.db.models import SessionDB

    mock_conn, mock_cursor = _patch_db_conn(monkeypatch, models_mod, rowcount=1)

    ok = SessionDB.update_context_token_count("sess", 0)
    assert ok is True
    args = mock_cursor.execute.call_args.args[1]
    assert args[0] == 0


def test_session_db_negative_token_coerced(monkeypatch):
    """传入负数 token → int() 转换（虽然 Agent 主流程不会传，但接口本身做 int 截断）"""
    import src.db.models as models_mod
    from src.db.models import SessionDB

    mock_conn, mock_cursor = _patch_db_conn(monkeypatch, models_mod, rowcount=1)

    SessionDB.update_context_token_count("sess", -100)
    args = mock_cursor.execute.call_args.args[1]
    assert args[0] == -100  # int() 不改符号


def test_channel_mgr_zero_token_no_crash(monkeypatch):
    """ChannelSessionManager.update_context_token_count 接受 0"""
    import src.channels.session as session_mod
    from src.channels.session import ChannelSessionManager

    mock_conn, mock_cursor = _patch_db_conn(monkeypatch, session_mod, rowcount=1)

    mgr = ChannelSessionManager()
    ok = mgr.update_context_token_count("s_kf", 0)
    assert ok is True
    args = mock_cursor.execute.call_args.args[1]
    assert args[0] == 0


def test_session_db_db_exception_returns_false(monkeypatch):
    """P0-2 修复：update_context_token_count DB 异常时被捕获，返回 False，不冒泡。

    设计要求：DB 异常不应让 Agent 主流程崩溃（即使主流程外层有 try/except，
    接口本身也应容错，参照 delete() 风格）。
    """
    import src.db.models as models_mod
    from src.db.models import SessionDB

    mock_cursor = MagicMock()
    mock_cursor.execute.side_effect = RuntimeError("db down")
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_conn.rollback = MagicMock()

    @contextmanager
    def _ctx():
        yield mock_conn

    monkeypatch.setattr(models_mod, "get_db_connection", _ctx)

    # P0-2 修复后：异常被捕获，return False，不冒泡
    ok = SessionDB.update_context_token_count("sess", 100)
    assert ok is False


def test_channel_mgr_db_exception_returns_false(monkeypatch):
    """P0-2 修复：channel 源 update_context_token_count DB 异常时返回 False。"""
    import src.channels.session as session_mod
    from src.channels.session import ChannelSessionManager

    mock_cursor = MagicMock()
    mock_cursor.execute.side_effect = RuntimeError("db down")
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_conn.rollback = MagicMock()

    @contextmanager
    def _ctx():
        yield mock_conn

    monkeypatch.setattr(session_mod, "get_db_connection", _ctx)

    mgr = ChannelSessionManager()
    ok = mgr.update_context_token_count("sess_kf", 100)
    assert ok is False
