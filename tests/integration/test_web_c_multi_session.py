"""web_c 多会话模型 SQL 集成测试（真实 PG，经 docker-compose.test-db.yml 入口运行）。

核心不变量：
1. get_or_create_session 默认路径（chat_id 为空）只匹配 chat_id 为空（NULL/空串）的
   会话，不会命中「新会话」创建的带唯一 chat_id token 的额外会话；
2. get_or_create_session 指定 chat_id token 时精确命中对应额外会话；
3. ChannelSessionManager.create_session 按显式 session_id 落库并返回完整字段。
"""

import pytest

from src.channels.session import channel_session_manager
from src.db.database import get_db_connection

pytestmark = pytest.mark.integration

TENANT = "tenant_ms_test"
UID = "u_ms_1"
SUBAGENT = "main"


@pytest.fixture
def channel_sessions_table():
    """创建本轮测试所需的 channel_sessions 最小表结构（隔离库自清理）。"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DROP TABLE IF EXISTS channel_sessions")
        cursor.execute("""
            CREATE TABLE channel_sessions (
                session_id TEXT PRIMARY KEY,
                tenant_id TEXT,
                channel_type TEXT,
                channel_user_id TEXT,
                subagent_id TEXT,
                channel_chat_id TEXT,
                user_id TEXT,
                username TEXT,
                title TEXT,
                context_data TEXT DEFAULT '{}',
                metadata TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_message_at TIMESTAMP
            )
        """)
        cursor.execute("""
            INSERT INTO channel_sessions
            (session_id, tenant_id, channel_type, channel_user_id, subagent_id, channel_chat_id)
            VALUES
            (%s, %s, 'web_c', %s, %s, NULL),
            (%s, %s, 'web_c', %s, %s, 'n_aaa11111')
        """, (
            f"{TENANT}_web_c_{UID}_{SUBAGENT}", TENANT, UID, SUBAGENT,
            f"{TENANT}_web_c_{UID}_{SUBAGENT}_saaaa1111", TENANT, UID, SUBAGENT,
        ))
        conn.commit()
    yield
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DROP TABLE IF EXISTS channel_sessions")
        conn.commit()


def test_default_lookup_ignores_extra_sessions(channel_sessions_table):
    """默认查找（chat_id 空）不得命中带 token 的额外会话（防劫持默认会话）。"""
    session = channel_session_manager.get_or_create_session(
        "web_c", UID, TENANT, None, None, None, SUBAGENT)
    assert session["session_id"] == f"{TENANT}_web_c_{UID}_{SUBAGENT}"


def test_token_lookup_hits_extra_session(channel_sessions_table):
    """指定 chat_id token 时精确命中对应额外会话。"""
    session = channel_session_manager.get_or_create_session(
        "web_c", UID, TENANT, None, "n_aaa11111", None, SUBAGENT)
    assert session["session_id"] == f"{TENANT}_web_c_{UID}_{SUBAGENT}_saaaa1111"


def test_create_session_persists_explicit_id(channel_sessions_table):
    """create_session 按显式 session_id 落库，字段完整可读回。"""
    session_id = f"{TENANT}_web_c_{UID}_{SUBAGENT}_sbbbb2222"
    created = channel_session_manager.create_session(
        session_id=session_id, channel_type="web_c", channel_user_id=UID,
        tenant_id=TENANT, channel_chat_id="n_bbb22222",
        user_info={"user_id": "user_1", "name": "访客"}, subagent_id=SUBAGENT)
    assert created["session_id"] == session_id
    assert created["channel_chat_id"] == "n_bbb22222"
    assert created["user_id"] == "user_1"
    assert created["created_at"] is not None
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT session_id, user_id, username, channel_chat_id FROM channel_sessions WHERE session_id = %s",
            (session_id,))
        rows = cursor.fetchall()
    assert len(rows) == 1
    assert rows[0]["username"] == "访客"
    # 默认查找仍不受新会话影响
    session = channel_session_manager.get_or_create_session(
        "web_c", UID, TENANT, None, None, None, SUBAGENT)
    assert session["session_id"] == f"{TENANT}_web_c_{UID}_{SUBAGENT}"
