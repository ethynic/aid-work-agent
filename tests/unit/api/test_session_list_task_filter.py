"""定时任务重构 Phase 5 二轮评审第 3/8 项后端修复回归：

1. SessionDB.list_by_user 前置过滤任务专属会话（metadata->'scheduled_task' IS NULL），
   count 与列表同条件（total 与数据同口径）——普通会话分页不再出现"整页任务会话"；
2. GET /api/sessions/{id} 详情端点透出 scheduled_task（= task_id | null，权威标记），
   兼容 metadata 为 JSONB dict / JSON 字符串 / None 三种形态。
"""

import pytest

from src.db.models import SessionDB

pytestmark = [pytest.mark.unit, pytest.mark.api]


# ==================== 1. list_by_user 前置过滤（评审第 3 项） ====================


@pytest.fixture
def capture_sql(monkeypatch):
    """缓存 miss + 捕获执行的 SQL（count 与列表两查询）"""
    captured = {"sql": []}

    def fake_get_cached(*args, **kwargs):
        return None

    def fake_set_cached(*args, **kwargs):
        pass

    class Cursor:
        def execute(self, sql, params=None):
            captured["sql"].append(sql)
            self._sql = sql

        def fetchone(self):
            return {"cnt": 0} if "COUNT(*)" in self._sql else None

        def fetchall(self):
            return []

    class Conn:
        def cursor(self):
            return Cursor()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("src.db.models.get_cached", fake_get_cached)
    monkeypatch.setattr("src.db.models.set_cached", fake_set_cached)
    monkeypatch.setattr("src.db.models.get_db_connection", lambda: Conn())
    return captured


class TestListByUserFiltersTaskSessions:
    def test_tenant_scoped_queries_filter_scheduled_task(self, capture_sql):
        """租户分支：count 与 SELECT 都必须带任务会话过滤条件（同口径）"""
        SessionDB.list_by_user("u1", page=1, page_size=20, tenant_id="t1")
        statements = capture_sql["sql"]
        assert len(statements) == 2
        for sql in statements:
            assert "metadata->'scheduled_task' IS NULL" in sql
            assert "tenant_id" in sql

    def test_non_tenant_queries_filter_scheduled_task(self, capture_sql):
        """非租户分支（兼容非 SaaS 模式）：两查询同样带过滤条件"""
        SessionDB.list_by_user("u1", page=1, page_size=20, tenant_id=None)
        statements = capture_sql["sql"]
        assert len(statements) == 2
        for sql in statements:
            assert "metadata->'scheduled_task' IS NULL" in sql


# ==================== 2. 会话详情 scheduled_task 标记（评审第 8 项） ====================


async def _call_get_session(monkeypatch, session_row):
    from src.api import session as session_api

    def fake_owned_web_session(authorization, tenant, session_id):
        # 端点经 asyncio.to_thread 调用：同步返回行
        return session_row

    monkeypatch.setattr("src.api.web_subject.owned_web_session", fake_owned_web_session)
    request = type("R", (), {"headers": {}})()
    return await session_api.get_session(request, "sess-1")


class TestSessionDetailScheduledTaskMarker:
    @pytest.mark.asyncio
    async def test_jsonb_metadata_exposes_task_id(self, monkeypatch):
        result = await _call_get_session(monkeypatch, {
            "session_id": "sess-1", "user_id": "u1", "tenant_id": "t1",
            "title": "任务会话", "subagent_id": None, "instance_id": None,
            "created_at": "2026-01-01", "updated_at": "2026-01-01",
            "context_data": None, "metadata": {"scheduled_task": "task-9"},
        })
        assert result["session"]["scheduled_task"] == "task-9"

    @pytest.mark.asyncio
    async def test_metadata_json_string_normalized(self, monkeypatch):
        """缓存路径可能回放 JSON 字符串：按删除守卫同口径归一化后读取标记"""
        import json as _json
        result = await _call_get_session(monkeypatch, {
            "session_id": "sess-1", "user_id": "u1", "tenant_id": "t1",
            "title": "任务会话", "subagent_id": None, "instance_id": None,
            "created_at": "2026-01-01", "updated_at": "2026-01-01",
            "context_data": None,
            "metadata": _json.dumps({"scheduled_task": "task-9"}),
        })
        assert result["session"]["scheduled_task"] == "task-9"

    @pytest.mark.asyncio
    async def test_normal_session_marker_is_null(self, monkeypatch):
        """普通会话（无 metadata / 无标记）：scheduled_task 为 None（权威"非任务"）"""
        result = await _call_get_session(monkeypatch, {
            "session_id": "sess-1", "user_id": "u1", "tenant_id": "t1",
            "title": "普通会话", "subagent_id": None, "instance_id": None,
            "created_at": "2026-01-01", "updated_at": "2026-01-01",
            "context_data": None, "metadata": None,
        })
        assert result["session"]["scheduled_task"] is None
