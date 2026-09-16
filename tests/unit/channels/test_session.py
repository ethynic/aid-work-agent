"""
渠道会话管理单元测试

测试 metadata / context_data / attachments 的 JSON 序列化 round-trip。
验证租户隔离：session_id 包含 tenant_id，查询带租户过滤。
"""

import json
import pytest
from unittest.mock import MagicMock, patch

from src.channels.session import ChannelSessionManager
from src.core.cache_utils import CacheKeys, delete_cached_pattern

TEST_TENANT = "test_tenant"


@pytest.fixture(autouse=True)
def _clear_channel_cache():
    """每个测试前清除渠道会话缓存，避免跨测试缓存污染"""
    delete_cached_pattern(CacheKeys.CHANNEL_SESSION, "")


@pytest.fixture
def session_manager():
    """创建 ChannelSessionManager 实例（已 mock 数据库）"""
    manager = ChannelSessionManager()
    manager._initialized = False
    return manager


@pytest.fixture
def mock_db():
    """模拟 PostgreSQL 内存数据库"""
    memory_store = {
        "sessions": {},  # session_id -> dict
        "messages": [],  # list of dict
    }

    class MockRow(dict):
        """模拟 psycopg2 RealDictCursor 返回的行"""
        pass

    def _make_connection():
        conn = MagicMock()
        cursor = MagicMock()
        cursor._fetch_rows = []
        cursor._fetch_idx = 0

        def execute(sql, params=None):
            sql_lower = sql.lower()
            params = params or ()
            cursor._fetch_rows = []
            cursor._fetch_idx = 0

            if "insert into channel_sessions" in sql_lower:
                # 生产 INSERT 为 11 列 + RETURNING created_at, updated_at, last_message_at
                row = MockRow(
                    session_id=params[0],
                    tenant_id=params[1],
                    channel_type=params[2],
                    channel_user_id=params[3],
                    subagent_id=params[4],
                    channel_chat_id=params[5],
                    user_id=params[6],
                    username=params[7],
                    title=params[8],
                    context_data=params[9],
                    metadata=params[10],
                )
                memory_store["sessions"][params[0]] = row
                # RETURNING 子句：供 fetchone() 读取时间戳
                cursor._fetch_rows = [MockRow(
                    created_at="2026-01-01 00:00:00",
                    updated_at="2026-01-01 00:00:00",
                    last_message_at="2026-01-01 00:00:00",
                )]
                cursor.rowcount = 1

            elif "update channel_sessions" in sql_lower:
                sid = params[-1]
                if sid in memory_store["sessions"]:
                    row = memory_store["sessions"][sid]
                    if len(params) >= 3:
                        row["updated_at"] = params[0]
                        if "title" in sql_lower and "context_data" not in sql_lower and "metadata" not in sql_lower:
                            row["title"] = params[1]
                        elif "context_data" in sql_lower and "metadata" not in sql_lower:
                            row["context_data"] = params[1]
                        elif "metadata" in sql_lower and "context_data" not in sql_lower:
                            row["metadata"] = params[1]
                        elif "context_data" in sql_lower and "metadata" in sql_lower:
                            row["context_data"] = params[1]
                            row["metadata"] = params[2]
                    cursor.rowcount = 1
                else:
                    cursor.rowcount = 0

            elif "select * from channel_sessions where session_id" in sql_lower:
                sid = params[0]
                if sid in memory_store["sessions"]:
                    cursor._fetch_rows = [memory_store["sessions"][sid]]
                cursor.rowcount = 1 if cursor._fetch_rows else 0

            elif "select 1 from channel_sessions where session_id" in sql_lower:
                # is_channel_session 判定：渠道会话登记表成员查询
                sid = params[0]
                if sid in memory_store["sessions"]:
                    cursor._fetch_rows = [MockRow(_exists=1)]

            elif "select tenant_id, channel_type, channel_user_id" in sql_lower:
                # update_session 清除缓存时查询租户信息（生产按列名取值，需返回 dict 行）
                sid = params[0]
                if sid in memory_store["sessions"]:
                    row = memory_store["sessions"][sid]
                    cursor._fetch_rows = [MockRow(
                        tenant_id=row["tenant_id"],
                        channel_type=row["channel_type"],
                        channel_user_id=row["channel_user_id"],
                        subagent_id=row.get("subagent_id", ""),
                        channel_chat_id=row.get("channel_chat_id") or "",
                    )]
                cursor.rowcount = 1 if cursor._fetch_rows else 0

            elif "select * from channel_sessions where tenant_id" in sql_lower:
                tid, ctype, cuid = params[0], params[1], params[2]
                said = params[3] if len(params) > 3 else ""
                chat_id = params[4] if len(params) > 4 else ""
                for row in memory_store["sessions"].values():
                    if (row.get("tenant_id") == tid and
                        row["channel_type"] == ctype and
                        row["channel_user_id"] == cuid and
                        row.get("subagent_id", "") == said and
                        (row.get("channel_chat_id") or "") == chat_id):
                        cursor._fetch_rows = [row]
                        break

            elif "select * from channel_sessions" in sql_lower and "where" in sql_lower:
                # Generic fallback for list queries
                pass

            elif "insert into channel_messages" in sql_lower:
                # 生产 INSERT 为 8 列（created_at 由 DB 默认值填充）
                row = MockRow(
                    message_id=params[0],
                    session_id=params[1],
                    tenant_id=params[2],
                    role=params[3],
                    content=params[4],
                    message_type=params[5],
                    attachments=params[6],
                    metadata=params[7],
                    created_at="2026-01-01 00:00:00",
                )
                memory_store["messages"].append(row)
                cursor.rowcount = 1

            elif "select * from channel_messages" in sql_lower:
                sid = params[0]
                rows = [r for r in memory_store["messages"] if r["session_id"] == sid]
                # 模拟 status 软删除过滤（隐藏命令"新会话"标记的 invalid 消息不入 LLM 上下文）。
                # SQL 含 "status = 'active'" 时才过滤，缺失 status 的行按 active 处理。
                if "status = 'active'" in sql_lower:
                    rows = [r for r in rows if r.get("status", "active") != "invalid"]
                # 生产用子查询「ORDER BY id DESC LIMIT N」取最近 N 条再正序返回。
                # memory_store 按插入顺序（=id ASC），最近 N 条即末尾 N 条，保持 ASC。
                import re as _re
                _m = _re.search(r"limit\s+(\d+)", sql_lower)
                _lim = int(_m.group(1)) if _m else len(rows)
                cursor._fetch_rows = rows[-_lim:] if _lim < len(rows) else list(rows)

            elif "select count(*)" in sql_lower and "channel_messages" in sql_lower:
                # count_messages_by_session：按 SQL 中实际出现的过滤条件模拟计数，
                # 与 get_messages 分支的过滤语义保持一致（缺失列按 active/未压缩处理）
                sid = params[0]
                rows = [r for r in memory_store["messages"] if r["session_id"] == sid]
                if "compacted = false" in sql_lower:
                    rows = [r for r in rows if not r.get("compacted")]
                if "is_recalled = false" in sql_lower:
                    rows = [r for r in rows if not r.get("is_recalled")]
                if "status = 'active'" in sql_lower:
                    rows = [r for r in rows if r.get("status", "active") != "invalid"]
                cursor._fetch_rows = [MockRow(cnt=len(rows))]

            elif "delete from" in sql_lower:
                if "channel_messages" in sql_lower:
                    sid = params[0]
                    memory_store["messages"] = [m for m in memory_store["messages"] if m["session_id"] != sid]
                elif "channel_sessions" in sql_lower:
                    sid = params[0]
                    if sid in memory_store["sessions"]:
                        del memory_store["sessions"][sid]
                cursor.rowcount = 1

            elif "create table" in sql_lower or "create index" in sql_lower:
                cursor.rowcount = 0

        def fetchone():
            rows = cursor._fetch_rows
            if rows:
                return rows[0]
            return None

        def fetchall():
            return cursor._fetch_rows

        cursor.execute = execute
        cursor.fetchone = fetchone
        cursor.fetchall = fetchall
        conn.cursor.return_value = cursor
        return conn

    with patch("src.channels.session.get_db_connection") as mock_get_conn:
        mock_conn = _make_connection()
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
        yield memory_store


class TestMetadataRoundTrip:
    """Metadata 序列化 round-trip 测试"""

    def test_session_metadata_roundtrip(self, session_manager, mock_db):
        """会话 metadata 写入和读取 round-trip"""
        metadata = {"message_type": "text", "source": "wecom", "extra": {"key": "value"}}
        session = session_manager.get_or_create_session(
            channel_type="wecom",
            channel_user_id="user123",
            tenant_id=TEST_TENANT,
            metadata=metadata,
        )
        sid = session["session_id"]
        assert TEST_TENANT in sid

        # 读取会话，验证 metadata 正确反序列化
        result = session_manager.get_session("wecom", "user123", tenant_id=TEST_TENANT)
        assert result is not None
        assert result["metadata"] == metadata
        assert result["tenant_id"] == TEST_TENANT

    def test_session_context_data_roundtrip(self, session_manager, mock_db):
        """会话 context_data 更新和读取 round-trip（直接验证 DB 数据）"""
        session_manager.get_or_create_session(
            channel_type="wecom",
            channel_user_id="user123",
            tenant_id=TEST_TENANT,
        )

        context_data = {"step": 3, "data": {"items": [1, 2, 3]}, "flag": True}
        sid = session_manager._generate_session_id(TEST_TENANT, "wecom", "user123")

        # 直接在 mock_db 中验证初始 context_data
        assert sid in mock_db["sessions"]
        assert mock_db["sessions"][sid]["context_data"] == "{}"

        # 更新 context_data
        updated = session_manager.update_session(
            session_id=sid,
            context_data=context_data,
        )
        assert updated is True

        # 验证 mock_db 中已更新
        import json
        assert json.loads(mock_db["sessions"][sid]["context_data"]) == context_data

    def test_message_metadata_roundtrip(self, session_manager, mock_db):
        """消息 metadata 和 attachments 写入和读取 round-trip"""
        session_manager.get_or_create_session(
            channel_type="wecom",
            channel_user_id="user123",
            tenant_id=TEST_TENANT,
        )

        metadata = {"raw_xml": "<xml>...</xml>", "msg_id": "msg_001"}
        attachments = [
            {"file_name": "test.pdf", "url": "https://example.com/test.pdf"},
            {"file_name": "image.png", "url": "https://example.com/image.png"},
        ]

        sid = session_manager._generate_session_id(TEST_TENANT, "wecom", "user123")
        session_manager.add_message(
            session_id=sid,
            role="user",
            content="测试消息",
            attachments=attachments,
            metadata=metadata,
            tenant_id=TEST_TENANT,
        )

        messages = session_manager.get_messages(sid)
        assert len(messages) == 1
        msg = messages[0]
        assert msg["metadata"] == metadata
        assert msg["attachments"] == attachments
        assert msg["tenant_id"] == TEST_TENANT

    def test_legacy_str_dict_fallback(self, session_manager, mock_db):
        """兼容旧的 str(dict) 格式：fallback 到原样返回"""
        old_metadata_str = "{'source': 'wecom', 'old': True}"
        old_attachments_str = "[{'file': 'old.pdf'}]"
        legacy_sid = session_manager._generate_session_id(TEST_TENANT, "wecom", "legacy_user")
        row = dict(
            session_id=legacy_sid,
            tenant_id=TEST_TENANT,
            channel_type="wecom",
            channel_user_id="legacy_user",
            subagent_id="",
            channel_chat_id=None,
            user_id=None,
            username=None,
            title="Legacy Session",
            context_data="{'step': 1}",
            created_at="2026-01-01 00:00:00",
            updated_at="2026-01-01 00:00:00",
            last_message_at="2026-01-01 00:00:00",
            metadata=old_metadata_str,
        )
        mock_db["sessions"][legacy_sid] = row
        mock_db["messages"].append(dict(
            message_id="msg_legacy",
            session_id=legacy_sid,
            tenant_id=TEST_TENANT,
            role="user",
            content="legacy",
            message_type="text",
            attachments=old_attachments_str,
            metadata=old_metadata_str,
            created_at="2026-01-01 00:00:00",
        ))

        result = session_manager.get_session("wecom", "legacy_user", tenant_id=TEST_TENANT)
        assert result["metadata"] == old_metadata_str

        messages = session_manager.get_messages(legacy_sid)
        assert messages[0]["attachments"] == old_attachments_str


class TestTenantIsolation:
    """租户隔离测试"""

    def test_different_tenants_get_different_sessions(self, session_manager, mock_db):
        """不同租户的同名用户获得不同会话"""
        s1 = session_manager.get_or_create_session(
            channel_type="wecom",
            channel_user_id="ZhangSan",
            tenant_id="tenant_a",
        )
        s2 = session_manager.get_or_create_session(
            channel_type="wecom",
            channel_user_id="ZhangSan",
            tenant_id="tenant_b",
        )
        assert s1["session_id"] != s2["session_id"]
        assert "tenant_a" in s1["session_id"]
        assert "tenant_b" in s2["session_id"]

    def test_session_id_format(self, session_manager, mock_db):
        """验证 session_id 格式包含租户和智能体信息"""
        sid = session_manager._generate_session_id("mytenant", "wecom", "user001", "travel-agent")
        assert sid == "mytenant_wecom_user001_travel-agent"

    def test_session_id_format_empty_subagent(self, session_manager, mock_db):
        """空 subagent_id 时 session_id 格式"""
        sid = session_manager._generate_session_id("mytenant", "wecom", "user001")
        assert sid == "mytenant_wecom_user001_"

    def test_empty_tenant_session_id_format(self, session_manager, mock_db):
        """空 tenant_id 时 session_id 格式"""
        sid = session_manager._generate_session_id("", "wecom", "user001")
        assert sid == "_wecom_user001_"


class TestSubagentIsolation:
    """子智能体隔离测试"""

    def test_different_subagents_get_different_sessions(self, session_manager, mock_db):
        """同一租户同一用户的不同智能体获得不同会话"""
        s1 = session_manager.get_or_create_session(
            channel_type="wecom",
            channel_user_id="user123",
            tenant_id=TEST_TENANT,
            subagent_id="travel-agent",
        )
        s2 = session_manager.get_or_create_session(
            channel_type="wecom",
            channel_user_id="user123",
            tenant_id=TEST_TENANT,
            subagent_id="trade-agent",
        )
        assert s1["session_id"] != s2["session_id"]
        assert "travel-agent" in s1["session_id"]
        assert "trade-agent" in s2["session_id"]

    def test_same_subagent_gets_same_session(self, session_manager, mock_db):
        """同一租户同一用户同一智能体获得相同会话"""
        s1 = session_manager.get_or_create_session(
            channel_type="wecom",
            channel_user_id="user123",
            tenant_id=TEST_TENANT,
            subagent_id="travel-agent",
        )
        s2 = session_manager.get_or_create_session(
            channel_type="wecom",
            channel_user_id="user123",
            tenant_id=TEST_TENANT,
            subagent_id="travel-agent",
        )
        assert s1["session_id"] == s2["session_id"]

    def test_subagent_id_stored_in_session(self, session_manager, mock_db):
        """subagent_id 正确存入并读取"""
        session = session_manager.get_or_create_session(
            channel_type="wecom",
            channel_user_id="user123",
            tenant_id=TEST_TENANT,
            subagent_id="travel-agent",
        )
        assert session["subagent_id"] == "travel-agent"


class TestChannelChatIdIsolation:
    """渠道会话/群ID（channel_chat_id）隔离测试。

    wecom_kf 场景：同一微信用户可从不同客服账号（open_kfid）进入，每个账号应
    独立会话（独立上下文、独立积分），避免复用第一个账号的会话导致积分归属错乱。
    channel_chat_id 为空时保持旧行为不变（wecom/dingtalk/feishu 不受影响）。
    """

    def test_different_channel_chat_id_get_different_sessions(self, session_manager, mock_db):
        """同一用户不同 channel_chat_id 获得不同会话（不复用第一个账号的会话）"""
        s1 = session_manager.get_or_create_session(
            channel_type="wecom_kf",
            channel_user_id="wx_user_001",
            tenant_id=TEST_TENANT,
            subagent_id="sales-assistant",
            channel_chat_id="open_kfid_A",
        )
        s2 = session_manager.get_or_create_session(
            channel_type="wecom_kf",
            channel_user_id="wx_user_001",
            tenant_id=TEST_TENANT,
            subagent_id="sales-assistant",
            channel_chat_id="open_kfid_B",
        )
        assert s1["session_id"] != s2["session_id"]
        assert "open_kfid_A" in s1["session_id"]
        assert "open_kfid_B" in s2["session_id"]
        assert s1["channel_chat_id"] == "open_kfid_A"
        assert s2["channel_chat_id"] == "open_kfid_B"

    def test_same_channel_chat_id_gets_same_session(self, session_manager, mock_db):
        """同一用户同一 channel_chat_id 重复调用获得相同会话"""
        s1 = session_manager.get_or_create_session(
            channel_type="wecom_kf",
            channel_user_id="wx_user_001",
            tenant_id=TEST_TENANT,
            subagent_id="sales-assistant",
            channel_chat_id="open_kfid_A",
        )
        s2 = session_manager.get_or_create_session(
            channel_type="wecom_kf",
            channel_user_id="wx_user_001",
            tenant_id=TEST_TENANT,
            subagent_id="sales-assistant",
            channel_chat_id="open_kfid_A",
        )
        assert s1["session_id"] == s2["session_id"]
        assert s1["channel_chat_id"] == s2["channel_chat_id"] == "open_kfid_A"

    def test_session_id_format_with_chat_id(self, session_manager, mock_db):
        """channel_chat_id 非空时 session_id 含 chat_id（位于 channel_user_id 前）"""
        sid = session_manager._generate_session_id(
            "mytenant", "wecom_kf", "user001", "sales-assistant", "open_kfid_x"
        )
        assert sid == "mytenant_wecom_kf_open_kfid_x_user001_sales-assistant"


class TestIsChannelSession:
    """is_channel_session 判定测试。

    意图（WHY）：上下文重建按会话来源分流——渠道会话读 channel_messages、web 会话读
    chat_messages，严格分离。判定依据是 channel_sessions 登记表成员。历史误写会让渠道
    会话的 chat_messages 残留陈旧行，若误判为 web 会话就会读到陈旧行、劫持真实对话
    （见 wecom-kf-context-loss-research.md §9）。本测试锁定"登记即为渠道会话"这一判定。
    """

    def test_registered_session_is_channel(self, session_manager, mock_db):
        """已登记的渠道会话判定为 True（应读 channel_messages）"""
        sid = "test_tenant_wecom_kf_wmS6o_user1_travel-consultant"
        # 直接注入 channel_sessions 登记表（绕开 get_or_create_session 的 INSERT mock，
        # 该 mock 与当前 INSERT 列数不同步是既有问题，与本测试无关）
        mock_db["sessions"][sid] = {"session_id": sid}
        assert session_manager.is_channel_session(sid) is True

    def test_unregistered_session_is_not_channel(self, session_manager, mock_db):
        """未登记的 session_id（web 会话）判定为 False（应读 chat_messages）"""
        assert session_manager.is_channel_session("session_abc123notregistered") is False

    def test_empty_session_id_is_not_channel(self, session_manager, mock_db):
        """空 session_id 不查库，直接返回 False"""
        assert session_manager.is_channel_session("") is False
        assert session_manager.is_channel_session(None) is False


class TestGetMessagesKeepsNewest:
    """get_messages 裁剪方向回归测试。

    意图（WHY）：长会话超过窗口上限 N 时，必须保留【最近】N 条（丢最老的），
    否则最近一轮 assistant 回复会被切掉，agent 遗忘用户刚确认的信息、重复提问。
    见 wecom-kf-context-loss-research.md §10（ORDER BY id ASC LIMIT 取最老N条的 bug）。
    """

    def test_returns_newest_n_not_oldest(self, session_manager, mock_db):
        """limit 小于总条数时，返回最近 N 条（正序），不是最老 N 条"""
        sid = "test_tenant_wecom_kf_user1_travel-consultant"
        mock_db["sessions"][sid] = {"session_id": sid}
        for i in range(5):
            session_manager.add_message(
                sid,
                role="user" if i % 2 == 0 else "assistant",
                content=f"msg{i}",
                tenant_id="test_tenant",
            )
        msgs = session_manager.get_messages(sid, limit=3)
        contents = [m["content"] for m in msgs]
        assert contents == ["msg2", "msg3", "msg4"], f"应保留最近3条, 实际: {contents}"


class TestGetMessagesFiltersInvalid:
    """get_messages 软删除过滤测试（隐藏命令"新会话"标记 status='invalid' 的消息不入 LLM 上下文）"""

    def test_excludes_all_invalid(self, session_manager, mock_db):
        """全部消息失效后，get_messages 返回空列表（新会话效果）"""
        sid = "test_tenant_wecom_kf_user1_travel-consultant"
        mock_db["sessions"][sid] = {"session_id": sid}
        session_manager.add_message(sid, role="user", content="msg1", tenant_id="test_tenant")
        session_manager.add_message(sid, role="assistant", content="msg2", tenant_id="test_tenant")
        for m in mock_db["messages"]:
            m["status"] = "invalid"
        with patch.object(session_manager, "_has_status_column", return_value=True):
            msgs = session_manager.get_messages(sid, limit=10)
        assert msgs == []

    def test_keeps_active_excludes_invalid(self, session_manager, mock_db):
        """仅失效消息被过滤，正常（active）消息保留"""
        sid = "test_tenant_wecom_kf_user1_travel-consultant"
        mock_db["sessions"][sid] = {"session_id": sid}
        session_manager.add_message(sid, role="user", content="msg1", tenant_id="test_tenant")
        session_manager.add_message(sid, role="assistant", content="msg2", tenant_id="test_tenant")
        mock_db["messages"][0]["status"] = "invalid"  # 第一条失效
        with patch.object(session_manager, "_has_status_column", return_value=True):
            msgs = session_manager.get_messages(sid, limit=10)
        assert [m["content"] for m in msgs] == ["msg2"]


class TestCountMessagesBySessionFilters:
    """count_messages_by_session 与 get_messages 过滤口径一致性测试。

    WHY：压缩阈值（mid_term.check_threshold）用 COUNT 判定，实际加载用
    get_messages（过滤 compacted/recalled/invalid）。若口径不一致——例如
    「新会话」软删除的 status='invalid' 消息永远 compacted=FALSE 且被 COUNT
    计入——COUNT 永远 >= 阈值，每轮触发压缩但 COMPRESS 区近空（只压到 2 条
    活跃消息），形成 context_compressed 死循环（2026-09-16 wecom_kf 会话事故）。
    """

    def test_excludes_invalid_and_compacted(self, session_manager, mock_db):
        """invalid 软删除消息与 compacted 消息不计入计数，与 get_messages 口径一致"""
        sid = "test_tenant_wecom_kf_user1_travel-consultant"
        mock_db["sessions"][sid] = {"session_id": sid}
        for i in range(4):
            session_manager.add_message(
                sid,
                role="user" if i % 2 == 0 else "assistant",
                content=f"msg{i}",
                tenant_id="test_tenant",
            )
        mock_db["messages"][0]["status"] = "invalid"
        mock_db["messages"][1]["compacted"] = True
        with patch.object(session_manager, "_has_status_column", return_value=True), \
             patch.object(session_manager, "_has_is_recalled_column", return_value=True):
            cnt = session_manager.count_messages_by_session(sid)
        assert cnt == 2, f"应只数 2 条 active 未压缩消息, 实际: {cnt}"

    def test_legacy_db_without_columns_counts_all(self, session_manager, mock_db):
        """存量库无 status/is_recalled 列时不启用对应过滤（迁移兼容路径）"""
        sid = "test_tenant_wecom_kf_user1_travel-consultant"
        mock_db["sessions"][sid] = {"session_id": sid}
        for i in range(3):
            session_manager.add_message(
                sid,
                role="user" if i % 2 == 0 else "assistant",
                content=f"msg{i}",
                tenant_id="test_tenant",
            )
        mock_db["messages"][0]["status"] = "invalid"
        with patch.object(session_manager, "_has_status_column", return_value=False), \
             patch.object(session_manager, "_has_is_recalled_column", return_value=False):
            cnt = session_manager.count_messages_by_session(sid)
        assert cnt == 3, f"列不存在时不应过滤, 实际: {cnt}"


class TestSoftDeleteMessages:
    """soft_delete_messages 软删除测试"""

    def test_marks_messages_invalid_and_summaries_superseded(self, session_manager):
        """channel_messages 置 status='invalid'，chat_context_summaries 置 superseded"""
        executed = []
        cursor = MagicMock()
        cursor.rowcount = 3

        def fake_execute(sql, params=None):
            executed.append((sql, params))

        cursor.execute = fake_execute
        conn = MagicMock()
        conn.cursor.return_value = cursor

        with patch("src.channels.session.get_db_connection") as mock_conn:
            mock_conn.return_value.__enter__ = MagicMock(return_value=conn)
            mock_conn.return_value.__exit__ = MagicMock(return_value=False)
            with patch("src.core.cache_utils.delete_cached_pattern") as mock_cache:
                result = session_manager.soft_delete_messages("sid_test", "tenant_test")

        assert result is True
        sqls = [s for s, _ in executed]
        assert any("UPDATE channel_messages" in s and "status = 'invalid'" in s for s in sqls), sqls
        assert any(
            "UPDATE chat_context_summaries" in s and "status = 'superseded'" in s
            for s in sqls
        ), sqls
        # 失效 SESSION_MSGS 缓存
        mock_cache.assert_called_once()

    def test_no_tenant(self, session_manager):
        """无 tenant_id 时按 session_id 单独过滤"""
        executed = []
        cursor = MagicMock()
        cursor.rowcount = 0

        def fake_execute(sql, params=None):
            executed.append((sql, params))

        cursor.execute = fake_execute
        conn = MagicMock()
        conn.cursor.return_value = cursor

        with patch("src.channels.session.get_db_connection") as mock_conn:
            mock_conn.return_value.__enter__ = MagicMock(return_value=conn)
            mock_conn.return_value.__exit__ = MagicMock(return_value=False)
            result = session_manager.soft_delete_messages("sid_test")

        assert result is False  # 无消息被标记
        sqls = [s for s, _ in executed]
        # 无租户分支：UPDATE 不带 tenant_id
        assert any(
            "UPDATE channel_messages" in s and "tenant_id" not in s for s in sqls
        ), sqls
