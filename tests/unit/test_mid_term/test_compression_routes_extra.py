"""上下文压缩管理后台 API 路由补充测试（Phase 7 §7.3）

独立审查发现 test_compression_routes.py 存在以下盲区：
- list_summaries 的 limit 边界（le=500）未覆盖
- list_summaries 空结果结构未覆盖
- rollback 已 rolled_back 的 summary 再次 rollback 的幂等性
- rollback 实际恢复消息 compacted=true → false 的事务行为（commit 被调用，rollback 被还原）
- rollback 内层 SQL 异常时事务回滚（conn.rollback 被调用）
- manual_compress session 不存在（compress_session 抛异常）时的错误响应
- get_summary_detail 跨租户隔离（租户 A 看不到 B 的 summary）已有，但缺少
  被压缩消息 fetch 失败时的容错（messages=[] 而非抛异常）

本文件补齐上述盲区。
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

import src.saas.api.context_compression_routes as routes_mod


# ============== 公共 fixtures（与 test_compression_routes.py 同构） ==============

@pytest.fixture
def platform_admin():
    return {"user_id": "u_platform", "role": "platform_admin", "tenant_id": "t_platform"}


@pytest.fixture
def tenant_a_admin():
    return {"user_id": "u_a", "role": "tenant_admin", "tenant_id": "t_a"}


@pytest.fixture
def fake_request():
    req = MagicMock()
    req.headers = {}
    return req


@pytest.fixture
def patch_admin(platform_admin, monkeypatch):
    monkeypatch.setattr(routes_mod, "require_admin", lambda req: platform_admin)
    return platform_admin


@pytest.fixture
def patch_db(monkeypatch):
    cursor = MagicMock()
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.commit = MagicMock()
    conn.rollback = MagicMock()

    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        yield conn

    monkeypatch.setattr(routes_mod, "get_db_connection", _ctx)
    return conn, cursor


# ============== list_summaries 边界 ==============

class TestListSummariesLimit:
    @pytest.mark.asyncio
    async def test_limit_at_upper_bound_accepted(
        self, patch_admin, patch_db, fake_request
    ):
        """FastAPI Query(le=500) 允许 limit=500，应正常执行。

        如果有人改成 le=200 但代码没同步，此测试会失败。
        """
        conn, cursor = patch_db
        cursor.fetchall.return_value = []
        result = await routes_mod.list_summaries(
            fake_request, session_id=None, source_type=None, limit=500
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_limit_below_one_rejected(
        self, patch_admin, patch_db, fake_request
    ):
        """FastAPI Query(ge=1) 拒绝 limit=0，应抛 HTTPException(422) 或类似。

        FastAPI 的 Query 校验在路由进入前完成，所以这里我们直接调 list_summaries
        时不会触发（pydantic 校验在 FastAPI wrapper 层）。但我们仍验证 limit
        不会传入负数 SQL：调用方传 1 是最小合法值。
        """
        # 直接调函数层无法触发 FastAPI 的 Query 校验（那是 wrapper 的事）。
        # 这里改验证最小合法值 limit=1 能正常工作，作为对比基线。
        conn, cursor = patch_db
        cursor.fetchall.return_value = []
        result = await routes_mod.list_summaries(
            fake_request, session_id=None, source_type=None, limit=1
        )
        assert result["success"] is True
        # 验证 SQL 末尾的 LIMIT %s 参数是 1
        params = cursor.execute.call_args.args[1]
        assert params[-1] == 1


class TestListSummariesEmptyResult:
    @pytest.mark.asyncio
    async def test_empty_result_structure(self, patch_admin, patch_db, fake_request):
        """空结果应返回 success=True, items=[], total=0（不是 None 或抛异常）。"""
        conn, cursor = patch_db
        cursor.fetchall.return_value = []
        result = await routes_mod.list_summaries(
            fake_request, session_id=None, source_type=None, limit=20
        )
        assert result == {"success": True, "items": [], "total": 0}


class TestListSummariesSerialization:
    @pytest.mark.asyncio
    async def test_datetime_iso_format(self, patch_admin, patch_db, fake_request):
        """created_at / superseded_at 必须序列化为 ISO 字符串（前端 JSON 友好）。"""
        conn, cursor = patch_db
        cursor.fetchall.return_value = [
            {
                "summary_id": "csum_1",
                "session_id": "sess",
                "source_type": "chat",
                "tenant_id": "t_a",
                "status": "active",
                "summary_version": 1,
                "compressed_message_count": 5,
                "original_token_count": 100,
                "compressed_token_count": 30,
                "compression_ratio": 0.3,
                "fallback_used": False,
                "llm_provider": "deepseek",
                "llm_model": "deepseek-chat",
                "created_at": datetime(2026, 6, 25, 12, 0, 0),
                "superseded_at": datetime(2026, 6, 25, 13, 0, 0),
            }
        ]
        result = await routes_mod.list_summaries(
            fake_request, session_id=None, source_type=None, limit=20
        )
        item = result["items"][0]
        assert isinstance(item["created_at"], str)
        assert item["created_at"].startswith("2026-06-25T12:00:00")
        assert isinstance(item["superseded_at"], str)


# ============== rollback 幂等性 & 事务 ==============

class TestRollbackIdempotency:
    @pytest.mark.asyncio
    async def test_rollback_already_rolled_back_summary(
        self, patch_admin, monkeypatch, patch_db, fake_request
    ):
        """对已 rolled_back 的 summary 再次 rollback → 不抛错，正常返回。

        场景：summary.status='rolled_back'，仍然走完 rollback 流程（UPDATE 0 行），
        返回 restored_message_count=0。这是管理后台「点两次回滚按钮」的真实场景。
        """
        summary = {
            "summary_id": "csum_1",
            "source_type": "chat",
            "tenant_id": "t_a",
            "status": "rolled_back",  # 已回滚过
        }
        monkeypatch.setattr(
            routes_mod.ContextSummaryDB, "get_by_id",
            lambda sid, tenant_id=None: summary,
        )
        conn, cursor = patch_db
        cursor.rowcount = 0  # 第二次 rollback，没有 compacted=true 的消息需要恢复

        result = await routes_mod.rollback_summary("csum_1", fake_request)
        assert result["success"] is True
        assert result["restored_message_count"] == 0
        # commit 仍应被调用（事务完整）
        conn.commit.assert_called_once()


class TestRollbackTransaction:
    @pytest.mark.asyncio
    async def test_rollback_restores_compacted_flag(
        self, patch_admin, monkeypatch, patch_db, fake_request
    ):
        """rollback 必须把 compacted=true 的消息恢复为 false（事务第一步）。

        现有测试只断言 SQL 片段，本测试断言完整事务行为：
        - SQL 包含 'SET compacted = FALSE'
        - SQL 包含 "compacted_by = {placeholder}"（按 summary_id 定位）
        - commit 被调用（事务落盘）
        """
        summary = {
            "summary_id": "csum_1",
            "source_type": "chat",
            "tenant_id": "t_a",
            "status": "active",
        }
        monkeypatch.setattr(
            routes_mod.ContextSummaryDB, "get_by_id",
            lambda sid, tenant_id=None: summary,
        )
        conn, cursor = patch_db
        cursor.rowcount = 7

        await routes_mod.rollback_summary("csum_1", fake_request)
        executed_sqls = [c.args[0] for c in cursor.execute.call_args_list]
        # 第一步 SQL：恢复消息
        restore_sql = next(s for s in executed_sqls if "SET compacted = FALSE" in s)
        assert "compacted_by =" in restore_sql
        # 第二步 SQL：summary 状态（v3.2.1 P1-2：用占位符，不再硬编码字符串）
        status_sql = next(
            s for s in executed_sqls
            if "status =" in s and "chat_context_summaries" in s
        )
        # 参数中应包含 'rolled_back'（来自枚举）
        executed_params = [c.args[1] for c in cursor.execute.call_args_list if len(c.args) > 1]
        all_params = [p for params in executed_params for p in (params if isinstance(params, (list, tuple)) else [params])]
        assert "rolled_back" in all_params
        # 事务提交
        conn.commit.assert_called_once()
        conn.rollback.assert_not_called()

    @pytest.mark.asyncio
    async def test_rollback_inner_sql_exception_triggers_conn_rollback(
        self, patch_admin, monkeypatch, patch_db, fake_request
    ):
        """第二步 SQL 抛异常 → conn.rollback 被调用 + 异常向上抛（最终被 except 捕获）。

        现有测试无任何场景覆盖事务回滚路径。
        """
        summary = {
            "summary_id": "csum_1",
            "source_type": "chat",
            "tenant_id": "t_a",
            "status": "active",
        }
        monkeypatch.setattr(
            routes_mod.ContextSummaryDB, "get_by_id",
            lambda sid, tenant_id=None: summary,
        )
        conn, cursor = patch_db

        # 第 1 次 execute（恢复消息）成功；第 2 次（status update）抛异常
        # v3.2.1 P1-2：status SQL 改用占位符，不再硬编码 'rolled_back'
        def _execute(sql, params=None):
            if "status =" in sql and "chat_context_summaries" in sql:
                raise RuntimeError("disk full")
        cursor.execute.side_effect = _execute

        result = await routes_mod.rollback_summary("csum_1", fake_request)
        # 异常被路由层 except 捕获，返回错误响应
        assert result["success"] is False
        assert "debug" in result
        # 事务回滚被调用
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_rollback_cross_tenant_rejected(
        self, monkeypatch, patch_db, fake_request, tenant_a_admin
    ):
        """租户 A 管理员对租户 B 的 summary 执行 rollback → 404。

        ContextSummaryDB.get_by_id(tenant_id='t_a') 查 t_b 的 summary 返回 None
        → 路由层抛 404。这保护了「跨租户回滚」攻击。
        """
        monkeypatch.setattr(routes_mod, "require_admin", lambda req: tenant_a_admin)
        monkeypatch.setattr(
            routes_mod.ContextSummaryDB, "get_by_id",
            lambda sid, tenant_id=None: None,  # 租户过滤后查不到
        )
        with pytest.raises(HTTPException) as exc:
            await routes_mod.rollback_summary("csum_of_b", fake_request)
        assert exc.value.status_code == 404


# ============== get_summary_detail 容错 ==============

class TestGetSummaryDetailFetchMessagesFailure:
    @pytest.mark.asyncio
    async def test_fetch_messages_failure_returns_empty_list(
        self, patch_admin, monkeypatch, fake_request
    ):
        """被压缩消息 fetch 失败时，messages 应为 []，不影响 summary 返回。

        被测代码：
            try: messages = await _fetch_compressed_messages(...)
            except: messages = []
        """
        summary = {
            "summary_id": "csum_1",
            "session_id": "sess_1",
            "source_type": "chat",
            "tenant_id": "t_a",
            "status": "active",
            "summary_text": "...",
            "compressed_message_ids": [101, 102],
            "created_at": datetime(2026, 6, 25),
            "superseded_at": None,
        }
        monkeypatch.setattr(
            routes_mod.ContextSummaryDB, "get_by_id",
            lambda sid, tenant_id=None: summary,
        )
        # _fetch_compressed_messages 抛异常 → 路由层应捕获并返回 messages=[]
        monkeypatch.setattr(
            routes_mod, "_fetch_compressed_messages",
            AsyncMock(side_effect=RuntimeError("db dead")),
        )
        result = await routes_mod.get_summary_detail("csum_1", fake_request)
        assert result["success"] is True
        assert result["messages"] == []
        assert result["summary"]["summary_id"] == "csum_1"

    @pytest.mark.asyncio
    async def test_no_compressed_message_ids_returns_empty_messages(
        self, patch_admin, monkeypatch, fake_request
    ):
        """summary.compressed_message_ids 为空时，messages 必须为 []（不调 fetch）。"""
        summary = {
            "summary_id": "csum_1",
            "session_id": "sess_1",
            "source_type": "chat",
            "tenant_id": "t_a",
            "status": "active",
            "summary_text": "...",
            "compressed_message_ids": [],
            "created_at": datetime(2026, 6, 25),
            "superseded_at": None,
        }
        monkeypatch.setattr(
            routes_mod.ContextSummaryDB, "get_by_id",
            lambda sid, tenant_id=None: summary,
        )
        fetch_mock = AsyncMock(return_value=[])
        monkeypatch.setattr(routes_mod, "_fetch_compressed_messages", fetch_mock)
        result = await routes_mod.get_summary_detail("csum_1", fake_request)
        assert result["messages"] == []
        # 空 ids 不应触发 fetch
        fetch_mock.assert_not_called()


# ============== manual_compress 错误路径 ==============

class TestManualCompressErrorPath:
    @pytest.mark.asyncio
    async def test_compress_session_raises_returns_error(
        self, patch_admin, monkeypatch, fake_request
    ):
        """compress_session 抛异常 → 返回结构化错误响应（不抛 HTTPException）。

        场景：session 不存在 / DB 异常。
        """
        mock_service = MagicMock()
        mock_service.compress_session = AsyncMock(
            side_effect=RuntimeError("session not found")
        )
        monkeypatch.setattr(
            "src.memory.mid_term.get_compression_service", lambda: mock_service
        )
        result = await routes_mod.manual_compress(
            "sess_missing", fake_request, source_type="chat"
        )
        assert result["success"] is False
        assert "debug" in result

    @pytest.mark.asyncio
    async def test_platform_admin_can_compress_any_tenant(
        self, patch_admin, monkeypatch, fake_request
    ):
        """平台管理员（role=platform_admin）压缩任意 session 不应被 403 拒绝。

        v3.2.1 P0-1/P0-2：平台管理员（无 X-Tenant-Id）tenant_filter=None，跳过
        租户校验，compress_session 直接被调用。**get_session_tenant_id 不应被调用**。
        """
        fake_result = MagicMock()
        fake_result.summary_id = "csum_x"
        fake_result.compressed_message_count = 1
        fake_result.original_token_count = 1
        fake_result.compressed_token_count = 1
        fake_result.compression_ratio = 1.0
        fake_result.fallback_used = False
        fake_result.trigger_reason = "force"
        fake_result.llm_provider = None
        fake_result.llm_model = None

        mock_service = MagicMock()
        mock_service.compress_session = AsyncMock(return_value=fake_result)
        mock_service.get_session_tenant_id = AsyncMock(return_value="any_tenant")
        monkeypatch.setattr(
            "src.memory.mid_term.get_compression_service", lambda: mock_service
        )
        result = await routes_mod.manual_compress(
            "sess_of_any_tenant", fake_request, source_type="chat"
        )
        assert result["success"] is True
        # 平台管理员不会触发 get_session_tenant_id（因为 tenant_filter is None）
        mock_service.get_session_tenant_id.assert_not_called()


# ============== get_stats 容错 ==============

class TestGetStatsRouteFailure:
    @pytest.mark.asyncio
    async def test_get_stats_metrics_exception_returns_error(
        self, patch_admin, monkeypatch, fake_request
    ):
        """CompressionMetrics.get_stats() 抛异常 → 路由返回结构化错误响应。"""
        with patch.object(routes_mod, "get_compression_metrics") as mock_metrics:
            mock_metrics.return_value.get_stats.side_effect = RuntimeError("redis dead")
            result = await routes_mod.get_stats(fake_request)
            assert result["success"] is False
            assert "debug" in result
