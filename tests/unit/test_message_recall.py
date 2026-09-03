"""
撤回消息处理单元测试

覆盖三个缺口的修复：
1. 同批次合并 _build_merged_message 保留 merged_from_msgids / merged_segments
2. session_queue 缓冲区升级：set_merge / append_merge / get_merged_segments / remove_merge_segment
3. mark_recalled_message 合并消息部分撤回 + 未命中兜底清理缓冲区
"""

import json
import pytest
from unittest.mock import MagicMock, patch

from src.core.session_queue import SessionMessageQueue, EnqueueResult
from src.channels.session import ChannelSessionManager


pytestmark = pytest.mark.agent


# ============================================================
# 通用 fixtures
# ============================================================


@pytest.fixture
def q():
    """新鲜的 SessionMessageQueue 实例"""
    return SessionMessageQueue()


@pytest.fixture
def fake_redis():
    """内存级 fake redis，模拟 set/get/delete/exists/expire"""
    store = {}

    fr = MagicMock()
    fr._store = store
    # 1950d123 后 _key() 统一走 redis_client.make_key(prefix, sid)，返回 "{prefix}:{sid}"
    fr.make_key = MagicMock(side_effect=lambda prefix, sid: f"{prefix}:{sid}")

    def acquire_lock(key, value, ex=None):
        if key in store:
            return False
        store[key] = value
        return True

    def release_lock(key, value):
        if store.get(key) == value:
            store.pop(key, None)
            return True
        return False

    def exists(key):
        return key in store

    def get(key):
        return store.get(key)

    def set_(key, value, ex=None):
        store[key] = value

    def delete(key):
        store.pop(key, None)

    def expire(key, ttl):
        return key in store

    fr.acquire_lock = MagicMock(side_effect=acquire_lock)
    fr.release_lock = MagicMock(side_effect=release_lock)
    fr.exists = MagicMock(side_effect=exists)
    fr.get = MagicMock(side_effect=get)
    fr.set = MagicMock(side_effect=set_)
    fr.delete = MagicMock(side_effect=delete)
    fr.expire = MagicMock(side_effect=expire)

    with patch("src.core.session_queue.redis_client", fr):
        yield fr


# ============================================================
# 缺口1路径A：_build_merged_message
# ============================================================


class TestBuildMergedMessage:
    def test_single_message_returns_original(self):
        """单条消息直接返回原 msg，不附加 merged 字段"""
        from src.saas.api.channel_routes import _build_merged_message

        msg = {"msgid": "m1", "text": {"content": "hello"}, "msgtype": "text"}
        result = _build_merged_message([msg])
        assert result is msg, "单条消息应直接返回原对象"
        assert "merged_from_msgids" not in result
        assert "merged_segments" not in result

    def test_merged_message_preserves_msgids_and_segments(self):
        """合并消息保留所有段的 msgid 和原始文本，顺序与输入一致"""
        from src.saas.api.channel_routes import _build_merged_message

        group = [
            {"msgid": "m1", "text": {"content": "你好"}, "msgtype": "text"},
            {"msgid": "m2", "text": {"content": "想去天眼"}, "msgtype": "text"},
            {"msgid": "m3", "text": {"content": "三个人"}, "msgtype": "text"},
        ]
        result = _build_merged_message(group)

        assert result["merged_from_msgids"] == ["m1", "m2", "m3"]
        assert result["merged_segments"] == [
            {"msgid": "m1", "text": "你好"},
            {"msgid": "m2", "text": "想去天眼"},
            {"msgid": "m3", "text": "三个人"},
        ]
        # text 字段保留拼接结果（用 \n 连接）
        assert result["text"]["content"] == "你好\n想去天眼\n三个人"
        # 基底是最后一条
        assert result["msgid"] == "m3"


# ============================================================
# 缺口1路径B：session_queue 缓冲区 segments
# ============================================================


class TestSessionQueueSegments:
    def test_set_merge_stores_segments(self, q, fake_redis):
        """set_merge 写入缓冲区时包含 segments 字段"""
        q.set_merge("sid1", "hello", [{"type": "text"}], msgid="m1")

        raw = fake_redis._store["session_merge:sid1"]
        data = json.loads(raw)
        assert data["segments"] == [{"msgid": "m1", "text": "hello"}]
        assert data["text"] == "hello"

    def test_set_merge_without_msgid(self, q, fake_redis):
        """set_merge 不传 msgid 时，segments 中 msgid 为空字符串"""
        q.set_merge("sid1", "hello")
        raw = fake_redis._store["session_merge:sid1"]
        data = json.loads(raw)
        assert data["segments"] == [{"msgid": "", "text": "hello"}]

    def test_append_merge_appends_segment(self, q, fake_redis):
        """append_merge 追加新段到 segments，text 用 [用户追加消息] 拼接"""
        q.set_merge("sid1", "你好", None, msgid="m1")
        merged = q.append_merge("sid1", "想去天眼", None, new_msgid="m2")

        raw = fake_redis._store["session_merge:sid1"]
        data = json.loads(raw)
        assert data["segments"] == [
            {"msgid": "m1", "text": "你好"},
            {"msgid": "m2", "text": "想去天眼"},
        ]
        assert "你好" in merged and "想去天眼" in merged
        assert "[用户追加消息]" in merged

    def test_get_merged_segments(self, q, fake_redis):
        """get_merged_segments 返回缓冲区分段列表"""
        q.set_merge("sid1", "hello", None, msgid="m1")
        q.append_merge("sid1", "world", None, new_msgid="m2")

        segments = q.get_merged_segments("sid1")
        assert segments == [
            {"msgid": "m1", "text": "hello"},
            {"msgid": "m2", "text": "world"},
        ]

    def test_get_merged_segments_empty(self, q, fake_redis):
        """无缓冲区时返回 None"""
        assert q.get_merged_segments("no_such_sid") is None


# ============================================================
# 缺口3：remove_merge_segment
# ============================================================


class TestRemoveMergeSegment:
    def test_hit_remove_one_segment(self, q, fake_redis):
        """命中指定 msgid 的段，剩余段重新拼接 text"""
        q.set_merge("sid1", "你好", None, msgid="m1")
        q.append_merge("sid1", "想去天眼", None, new_msgid="m2")
        q.append_merge("sid1", "三个人", None, new_msgid="m3")

        removed = q.remove_merge_segment("sid1", "m2")
        assert removed is True

        raw = fake_redis._store["session_merge:sid1"]
        data = json.loads(raw)
        assert data["segments"] == [
            {"msgid": "m1", "text": "你好"},
            {"msgid": "m3", "text": "三个人"},
        ]
        # text 重建：第一段原样，后续段加 [用户追加消息] 前缀
        assert data["text"] == "你好\n\n[用户追加消息] 三个人"

    def test_remove_all_segments_clears_buffer_and_sets_cancel(self, q, fake_redis):
        """所有段都被移除时，清空缓冲区并设置取消标志"""
        q.set_merge("sid1", "你好", None, msgid="m1")

        removed = q.remove_merge_segment("sid1", "m1")
        assert removed is True

        # 缓冲区已清空
        assert "session_merge:sid1" not in fake_redis._store
        # 取消标志已设置
        assert "session_cancel:sid1" in fake_redis._store

    def test_no_match_returns_false(self, q, fake_redis):
        """无匹配段返回 False，缓冲区不变"""
        q.set_merge("sid1", "你好", None, msgid="m1")
        removed = q.remove_merge_segment("sid1", "no_such_msgid")
        assert removed is False
        # 缓冲区仍在
        assert "session_merge:sid1" in fake_redis._store

    def test_empty_msgid_returns_false(self, q, fake_redis):
        """recall_msgid 为空时直接返回 False"""
        q.set_merge("sid1", "你好", None, msgid="m1")
        removed = q.remove_merge_segment("sid1", "")
        assert removed is False

    def test_no_buffer_returns_false(self, q, fake_redis):
        """无缓冲区返回 False"""
        removed = q.remove_merge_segment("no_such_sid", "m1")
        assert removed is False

    def test_legacy_format_without_segments_returns_false(self, q, fake_redis):
        """旧格式缓冲区（无 segments 字段）返回 False，无法部分撤回"""
        # 手动写入旧格式缓冲区
        legacy_data = json.dumps({
            "text": "hello",
            "attachments_meta": [],
            "timestamp": 0,
        }, ensure_ascii=False)
        fake_redis._store["session_merge:sid1"] = legacy_data

        removed = q.remove_merge_segment("sid1", "m1")
        assert removed is False


# ============================================================
# EnqueueResult 新增字段
# ============================================================


class TestEnqueueResultNewFields:
    def test_default_new_fields_none(self):
        """merged_from_msgids / merged_segments 默认 None"""
        r = EnqueueResult(status="success")
        assert r.merged_from_msgids is None
        assert r.merged_segments is None

    @pytest.mark.asyncio
    async def test_idle_merged_returns_segments(self, q, fake_redis):
        """空闲态合并方返回 merged_from_msgids / merged_segments

        场景：首条消息 m1 进入合并窗口，2 秒窗口内追加 m2。
        processor 跑完后返回 success，was_merged=True，merged_from_msgids=[m1, m2]。
        """
        async def processor(cancel_check, user_input_override=None):
            return "reply"

        # 首条消息获取锁，进入合并窗口
        # 在窗口内（_wait_merge_window sleep 2s）追加 m2
        async def append_during_window():
            await asyncio_sleep_short()
            q.append_merge("sid_merge", "第二条", None, new_msgid="m2")

        import asyncio
        task = asyncio.create_task(append_during_window())
        result = await q.enqueue_and_process(
            session_id="sid_merge",
            user_input="第一条",
            processor=processor,
            msgid="m1",
        )
        await task

        assert result.status == "success"
        assert result.was_merged is True
        assert result.merged_from_msgids == ["m1", "m2"]
        assert result.merged_segments == [
            {"msgid": "m1", "text": "第一条"},
            {"msgid": "m2", "text": "第二条"},
        ]


async def asyncio_sleep_short():
    """短睡眠，让出事件循环让 append_merge 能在合并窗口内执行"""
    import asyncio
    await asyncio.sleep(0.3)


# ============================================================
# mark_recalled_message
# ============================================================


def _make_mock_db_with_messages(messages):
    """构造 mock 数据库，预置消息列表"""
    memory_store = {"messages": list(messages)}

    class MockRow(dict):
        pass

    def _make_connection():
        conn = MagicMock()
        cursor = MagicMock()
        cursor._fetch_rows = []

        def execute(sql, params=None):
            sql_lower = sql.lower()
            params = params or ()
            cursor._fetch_rows = []
            cursor._fetch_idx = 0

            if "select id, content, metadata from channel_messages" in sql_lower and "jsonb_exists" not in sql_lower:
                # 情况1：单条消息命中
                sid, msgid = params[0], params[1]
                for r in memory_store["messages"]:
                    if r["session_id"] == sid:
                        meta = r.get("metadata")
                        if isinstance(meta, str):
                            try:
                                meta = json.loads(meta)
                            except Exception:
                                meta = {}
                        elif meta is None:
                            meta = {}
                        if meta.get("msgid") == msgid:
                            cursor._fetch_rows = [MockRow(
                                id=r["id"],
                                content=r["content"],
                                metadata=json.dumps(meta, ensure_ascii=False) if meta else None,
                            )]
                            break
            elif "jsonb_exists" in sql_lower:
                # 情况2：合并消息命中
                sid, msgid = params[0], params[1]
                for r in memory_store["messages"]:
                    if r["session_id"] == sid:
                        meta = r.get("metadata")
                        if isinstance(meta, str):
                            try:
                                meta = json.loads(meta)
                            except Exception:
                                meta = {}
                        elif meta is None:
                            meta = {}
                        if msgid in (meta.get("merged_from_msgids") or []):
                            cursor._fetch_rows = [MockRow(
                                id=r["id"],
                                content=r["content"],
                                metadata=json.dumps(meta, ensure_ascii=False) if meta else None,
                            )]
                            break
            elif "select id from channel_messages" in sql_lower and "role = 'assistant'" in sql_lower:
                # 配对 assistant 查询：同 session 中 id > after_id 的第一条 assistant
                sid, after_id = params[0], params[1]
                for r in memory_store["messages"]:
                    if (
                        r["session_id"] == sid
                        and r["id"] > after_id
                        and r.get("role") == "assistant"
                    ):
                        cursor._fetch_rows = [MockRow(id=r["id"])]
                        break
            elif "update channel_messages" in sql_lower:
                # 更新消息
                for r in memory_store["messages"]:
                    if r["id"] == params[-1]:
                        if "is_recalled = true" in sql_lower and "content" in sql_lower:
                            r["is_recalled"] = True
                            r["content"] = params[0]
                            r["metadata"] = params[1]
                        elif "is_recalled = true" in sql_lower:
                            r["is_recalled"] = True
                        elif "content = " in sql_lower:
                            r["content"] = params[0]
                            r["metadata"] = params[1]
                        cursor.rowcount = 1
                        break
            elif "information_schema.columns" in sql_lower and "is_recalled" in sql_lower:
                cursor._fetch_rows = [MockRow(column_name="is_recalled")]

        def fetchone():
            return cursor._fetch_rows[0] if cursor._fetch_rows else None

        def fetchall():
            return cursor._fetch_rows

        cursor.execute = execute
        cursor.fetchone = fetchone
        cursor.fetchall = fetchall
        conn.cursor.return_value = cursor
        return conn

    return _make_connection, memory_store


class TestMarkRecalledMessage:
    @pytest.fixture
    def session_manager(self):
        manager = ChannelSessionManager()
        manager._initialized = False
        return manager

    def test_single_message_marked(self, session_manager):
        """单条消息命中：metadata.msgid 匹配，标记 is_recalled=TRUE"""
        messages = [{
            "id": 1,
            "session_id": "sid1",
            "content": "你好",
            "metadata": json.dumps({"msgid": "m1", "msgtype": "text"}),
            "is_recalled": False,
        }]
        _make_conn, store = _make_mock_db_with_messages(messages)

        with patch("src.channels.session.get_db_connection") as mock_get_conn:
            mock_get_conn.return_value.__enter__ = MagicMock(return_value=_make_conn())
            mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
            marked = session_manager.mark_recalled_message("sid1", "m1", "tenant1")

        assert marked == 1
        assert store["messages"][0]["is_recalled"] is True

    def test_merged_message_partial_recall(self, session_manager):
        """合并消息部分撤回：merged_from_msgids 包含 recall_msgid，重建 content"""
        merged_segments = [
            {"msgid": "m1", "text": "你好"},
            {"msgid": "m2", "text": "想去天眼"},
            {"msgid": "m3", "text": "三个人"},
        ]
        metadata = {
            "merged_from_msgids": ["m1", "m2", "m3"],
            "merged_segments": merged_segments,
        }
        messages = [{
            "id": 10,
            "session_id": "sid1",
            "content": "你好\n想去天眼\n三个人",
            "metadata": json.dumps(metadata, ensure_ascii=False),
            "is_recalled": False,
        }]
        _make_conn, store = _make_mock_db_with_messages(messages)

        with patch("src.channels.session.get_db_connection") as mock_get_conn:
            mock_get_conn.return_value.__enter__ = MagicMock(return_value=_make_conn())
            mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
            marked = session_manager.mark_recalled_message("sid1", "m2", "tenant1")

        assert marked == 1
        # m2 被撤回，content 重建为 m1 + m3（用 \n 拼接，符合 mark_recalled_message 实现）
        assert store["messages"][0]["content"] == "你好\n三个人"
        # is_recalled 仍为 False（还有有效段）
        assert store["messages"][0]["is_recalled"] is False
        # metadata 记录 recalled_part_msgids
        new_meta = json.loads(store["messages"][0]["metadata"])
        assert "m2" in new_meta["recalled_part_msgids"]
        assert new_meta["original_content_before_recall"] == "你好\n想去天眼\n三个人"

    def test_merged_message_all_recalled(self, session_manager):
        """合并消息所有段都被撤回：整条标记 is_recalled=TRUE"""
        merged_segments = [{"msgid": "m1", "text": "你好"}]
        metadata = {
            "merged_from_msgids": ["m1"],
            "merged_segments": merged_segments,
        }
        messages = [{
            "id": 10,
            "session_id": "sid1",
            "content": "你好",
            "metadata": json.dumps(metadata, ensure_ascii=False),
            "is_recalled": False,
        }]
        _make_conn, store = _make_mock_db_with_messages(messages)

        with patch("src.channels.session.get_db_connection") as mock_get_conn:
            mock_get_conn.return_value.__enter__ = MagicMock(return_value=_make_conn())
            mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
            marked = session_manager.mark_recalled_message("sid1", "m1", "tenant1")

        assert marked == 1
        assert store["messages"][0]["is_recalled"] is True

    def test_not_found_fallback_to_buffer(self, session_manager, q, fake_redis):
        """未命中持久化消息时，兜底调用 session_queue.remove_merge_segment

        场景：消息 m1 在 session_merge 缓冲区里（尚未落库），撤回事件到达。
        mark_recalled_message 查 channel_messages 未命中，调 remove_merge_segment 清理缓冲区。
        """
        # 预置缓冲区含 m1
        q.set_merge("sid1", "你好", None, msgid="m1")
        q.append_merge("sid1", "想去天眼", None, new_msgid="m2")

        messages = []  # 数据库无消息
        _make_conn, store = _make_mock_db_with_messages(messages)

        with patch("src.channels.session.get_db_connection") as mock_get_conn, \
             patch("src.core.session_queue.redis_client", fake_redis):
            mock_get_conn.return_value.__enter__ = MagicMock(return_value=_make_conn())
            mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
            marked = session_manager.mark_recalled_message("sid1", "m1", "tenant1")

        assert marked == 0
        # 缓冲区中 m1 段已被移除
        raw = fake_redis._store["session_merge:sid1"]
        data = json.loads(raw)
        assert data["segments"] == [{"msgid": "m2", "text": "想去天眼"}]

    def test_single_recall_marks_following_assistant(self, session_manager):
        """单条 user 撤回：紧随其后的 assistant 回复也标记 is_recalled=TRUE

        场景：用户撤回 m1，m1 已落库；同 session 中紧随其后有 assistant 回复 a1。
        标记 m1 后应同步标记 a1，避免下一轮上下文拼接基于已撤回输入生成的回复。
        """
        messages = [
            {
                "id": 1,
                "session_id": "sid1",
                "role": "user",
                "content": "亲子房呢",
                "metadata": json.dumps({"msgid": "m1", "msgtype": "text"}),
                "is_recalled": False,
            },
            {
                "id": 2,
                "session_id": "sid1",
                "role": "assistant",
                "content": "120人团建要亲子房？",
                "metadata": None,
                "is_recalled": False,
            },
        ]
        _make_conn, store = _make_mock_db_with_messages(messages)

        with patch("src.channels.session.get_db_connection") as mock_get_conn:
            mock_get_conn.return_value.__enter__ = MagicMock(return_value=_make_conn())
            mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
            marked = session_manager.mark_recalled_message("sid1", "m1", "tenant1")

        assert marked == 1
        # user 消息被标记
        assert store["messages"][0]["is_recalled"] is True
        # 配对 assistant 回复也被标记
        assert store["messages"][1]["is_recalled"] is True

    def test_merged_all_recalled_marks_following_assistant(self, session_manager):
        """合并消息所有段都被撤回：整条标记 + 配对 assistant 也标记"""
        merged_segments = [{"msgid": "m1", "text": "你好"}]
        metadata = {
            "merged_from_msgids": ["m1"],
            "merged_segments": merged_segments,
        }
        messages = [
            {
                "id": 10,
                "session_id": "sid1",
                "role": "user",
                "content": "你好",
                "metadata": json.dumps(metadata, ensure_ascii=False),
                "is_recalled": False,
            },
            {
                "id": 11,
                "session_id": "sid1",
                "role": "assistant",
                "content": "你好，请问需要什么帮助？",
                "metadata": None,
                "is_recalled": False,
            },
        ]
        _make_conn, store = _make_mock_db_with_messages(messages)

        with patch("src.channels.session.get_db_connection") as mock_get_conn:
            mock_get_conn.return_value.__enter__ = MagicMock(return_value=_make_conn())
            mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
            marked = session_manager.mark_recalled_message("sid1", "m1", "tenant1")

        assert marked == 1
        # 合并消息整条被标记
        assert store["messages"][0]["is_recalled"] is True
        # 配对 assistant 也被标记
        assert store["messages"][1]["is_recalled"] is True

    def test_merged_partial_recall_does_not_mark_assistant(self, session_manager):
        """合并消息部分撤回：user 消息仍存在，assistant 回复不应被标记"""
        merged_segments = [
            {"msgid": "m1", "text": "你好"},
            {"msgid": "m2", "text": "想去天眼"},
        ]
        metadata = {
            "merged_from_msgids": ["m1", "m2"],
            "merged_segments": merged_segments,
        }
        messages = [
            {
                "id": 10,
                "session_id": "sid1",
                "role": "user",
                "content": "你好\n想去天眼",
                "metadata": json.dumps(metadata, ensure_ascii=False),
                "is_recalled": False,
            },
            {
                "id": 11,
                "session_id": "sid1",
                "role": "assistant",
                "content": "好的，天眼在贵州",
                "metadata": None,
                "is_recalled": False,
            },
        ]
        _make_conn, store = _make_mock_db_with_messages(messages)

        with patch("src.channels.session.get_db_connection") as mock_get_conn:
            mock_get_conn.return_value.__enter__ = MagicMock(return_value=_make_conn())
            mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
            marked = session_manager.mark_recalled_message("sid1", "m2", "tenant1")

        assert marked == 1
        # user 合并消息仍存在（仅部分撤回）
        assert store["messages"][0]["is_recalled"] is False
        assert store["messages"][0]["content"] == "你好"
        # assistant 不应被标记（user 消息仍存在，回复仍有效）
        assert store["messages"][1]["is_recalled"] is False

    def test_single_recall_no_following_assistant(self, session_manager):
        """单条 user 撤回：紧随其后没有 assistant 时，仅标记 user，不报错"""
        messages = [
            {
                "id": 1,
                "session_id": "sid1",
                "role": "user",
                "content": "亲子房呢",
                "metadata": json.dumps({"msgid": "m1", "msgtype": "text"}),
                "is_recalled": False,
            },
        ]
        _make_conn, store = _make_mock_db_with_messages(messages)

        with patch("src.channels.session.get_db_connection") as mock_get_conn:
            mock_get_conn.return_value.__enter__ = MagicMock(return_value=_make_conn())
            mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
            marked = session_manager.mark_recalled_message("sid1", "m1", "tenant1")

        assert marked == 1
        assert store["messages"][0]["is_recalled"] is True


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
