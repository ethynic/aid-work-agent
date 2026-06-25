"""上下文压缩管理后台 API 路由测试（Phase 7 §7.3）

覆盖：
- list_summaries 默认按租户过滤；平台管理员可看所有
- get_summary_detail 含完整 summary_text 和被压缩原消息
- rollback_summary 事务：消息 compacted=false + summary status=rolled_back
- manual_compress 调用 compress_session(force=True)
- get_stats 返回 CompressionMetrics.get_stats()
- 租户隔离：租户 A 不能查看租户 B 的 summary

策略：mock require_admin + get_db_connection + ContextSummaryDB + 压缩服务，
让路由层逻辑可独立测试，不依赖真实 DB / Redis。
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

import src.saas.api.context_compression_routes as routes_mod


# ============== 公共 fixtures ==============

@pytest.fixture
def platform_admin():
    return {"user_id": "u_platform", "role": "platform_admin", "tenant_id": "t_platform"}


@pytest.fixture
def tenant_a_admin():
    return {"user_id": "u_a", "role": "tenant_admin", "tenant_id": "t_a"}


@pytest.fixture
def tenant_b_admin():
    return {"user_id": "u_b", "role": "tenant_admin", "tenant_id": "t_b"}


@pytest.fixture
def fake_request():
    """构造一个最小可用的 Request mock。"""
    req = MagicMock()
    req.headers = {}
    return req


@pytest.fixture
def patch_admin(platform_admin, monkeypatch):
    """默认按平台管理员 patch；测试中可重写。"""
    monkeypatch.setattr(routes_mod, "require_admin", lambda req: platform_admin)
    return platform_admin


@pytest.fixture
def patch_db(monkeypatch):
    """patch get_db_connection，返回 mock conn + cursor。"""
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


# ============== list_summaries ==============

class TestListSummaries:
    @pytest.mark.asyncio
    async def test_list_basic(self, patch_admin, patch_db, fake_request):
        conn, cursor = patch_db
        cursor.fetchall.return_value = [
            {
                "summary_id": "csum_1",
                "session_id": "sess_1",
                "source_type": "chat",
                "tenant_id": "t_a",
                "status": "active",
                "summary_version": 1,
                "compressed_message_count": 10,
                "original_token_count": 1000,
                "compressed_token_count": 300,
                "compression_ratio": 0.3,
                "fallback_used": False,
                "llm_provider": "deepseek",
                "llm_model": "deepseek-chat",
                "created_at": datetime(2026, 6, 25, 12, 0, 0),
                "superseded_at": None,
            }
        ]
        result = await routes_mod.list_summaries(
            fake_request, session_id=None, source_type=None, limit=50
        )
        assert result["success"] is True
        assert result["total"] == 1
        assert result["items"][0]["summary_id"] == "csum_1"
        assert result["items"][0]["created_at"].startswith("2026-06-25")

    @pytest.mark.asyncio
    async def test_tenant_filter_for_tenant_admin(
        self, monkeypatch, patch_db, fake_request, tenant_a_admin
    ):
        # 租户管理员 → SQL 应包含 tenant_id 过滤
        monkeypatch.setattr(routes_mod, "require_admin", lambda req: tenant_a_admin)
        conn, cursor = patch_db
        cursor.fetchall.return_value = []
        await routes_mod.list_summaries(fake_request, session_id=None, source_type=None, limit=20)
        sql = cursor.execute.call_args.args[0]
        assert "tenant_id =" in sql

    @pytest.mark.asyncio
    async def test_no_tenant_filter_for_platform_admin(
        self, patch_admin, patch_db, fake_request
    ):
        # 平台管理员 → 不加 tenant_id 过滤
        conn, cursor = patch_db
        cursor.fetchall.return_value = []
        await routes_mod.list_summaries(fake_request, session_id=None, source_type=None, limit=20)
        sql = cursor.execute.call_args.args[0]
        assert "tenant_id =" not in sql

    @pytest.mark.asyncio
    async def test_session_id_filter(self, patch_admin, patch_db, fake_request):
        conn, cursor = patch_db
        cursor.fetchall.return_value = []
        await routes_mod.list_summaries(
            fake_request, session_id="sess_x", source_type=None, limit=20
        )
        sql = cursor.execute.call_args.args[0]
        assert "session_id =" in sql

    @pytest.mark.asyncio
    async def test_source_type_filter(self, patch_admin, patch_db, fake_request):
        conn, cursor = patch_db
        cursor.fetchall.return_value = []
        await routes_mod.list_summaries(
            fake_request, session_id=None, source_type="wecom_kf", limit=20
        )
        sql = cursor.execute.call_args.args[0]
        assert "source_type =" in sql

    @pytest.mark.asyncio
    async def test_db_error_returns_error_response(self, patch_admin, monkeypatch, fake_request):
        """DB 异常不抛 HTTPException，返回结构化错误响应。"""
        from contextlib import contextmanager

        @contextmanager
        def _ctx():
            raise RuntimeError("db dead")

        monkeypatch.setattr(routes_mod, "get_db_connection", _ctx)
        result = await routes_mod.list_summaries(
            fake_request, session_id=None, source_type=None, limit=20
        )
        assert result["success"] is False
        assert "debug" in result


# ============== get_stats ==============

class TestGetStats:
    @pytest.mark.asyncio
    async def test_get_stats_returns_dict(self, patch_admin, fake_request):
        with patch.object(routes_mod, "get_compression_metrics") as mock_metrics:
            mock_metrics.return_value.get_stats.return_value = {
                "counts": {"chat:success": 5},
                "duration": {"count": 5},
                "ratio": {"count": 5},
                "fallback_duration": {"count": 0},
            }
            result = await routes_mod.get_stats(fake_request)
            assert result["success"] is True
            assert result["stats"]["counts"]["chat:success"] == 5


# ============== get_summary_detail ==============

class TestGetSummaryDetail:
    @pytest.mark.asyncio
    async def test_detail_404_when_missing(self, patch_admin, monkeypatch, fake_request):
        monkeypatch.setattr(routes_mod.ContextSummaryDB, "get_by_id", lambda sid, tenant_id=None: None)
        with pytest.raises(HTTPException) as exc:
            await routes_mod.get_summary_detail("csum_unknown", fake_request)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_detail_includes_summary_text(self, patch_admin, monkeypatch, fake_request):
        summary = {
            "summary_id": "csum_1",
            "session_id": "sess_1",
            "source_type": "chat",
            "tenant_id": "t_a",
            "status": "active",
            "summary_text": "## 用户与背景\n- test",
            "compressed_message_ids": [101, 102],
            "created_at": datetime(2026, 6, 25),
            "superseded_at": None,
        }
        monkeypatch.setattr(routes_mod.ContextSummaryDB, "get_by_id", lambda sid, tenant_id=None: summary)
        monkeypatch.setattr(routes_mod, "_fetch_compressed_messages", AsyncMock(return_value=[
            {"id": 101, "role": "user", "content": "hi", "compacted": True, "created_at": datetime(2026, 6, 25)},
            {"id": 102, "role": "assistant", "content": "hello", "compacted": True, "created_at": datetime(2026, 6, 25)},
        ]))
        result = await routes_mod.get_summary_detail("csum_1", fake_request)
        assert result["success"] is True
        assert result["summary"]["summary_text"] == "## 用户与背景\n- test"
        assert len(result["messages"]) == 2
        assert result["messages"][0]["id"] == 101

    @pytest.mark.asyncio
    async def test_tenant_isolation_tenant_a_cannot_see_tenant_b(
        self, monkeypatch, patch_db, fake_request, tenant_a_admin
    ):
        """租户 A 的管理员传 tenant_id 过滤，ContextSummaryDB.get_by_id 拿不到租户 B 的 summary。"""
        monkeypatch.setattr(routes_mod, "require_admin", lambda req: tenant_a_admin)
        # 模拟 DB 在 tenant_id=t_a 过滤下找不到 t_b 的 summary
        monkeypatch.setattr(routes_mod.ContextSummaryDB, "get_by_id", lambda sid, tenant_id=None: None)
        with pytest.raises(HTTPException) as exc:
            await routes_mod.get_summary_detail("csum_of_t_b", fake_request)
        assert exc.value.status_code == 404


# ============== rollback_summary ==============

class TestRollbackSummary:
    @pytest.mark.asyncio
    async def test_rollback_404_when_missing(self, patch_admin, monkeypatch, fake_request):
        monkeypatch.setattr(routes_mod.ContextSummaryDB, "get_by_id", lambda sid, tenant_id=None: None)
        with pytest.raises(HTTPException) as exc:
            await routes_mod.rollback_summary("csum_unknown", fake_request)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_rollback_chat_source(self, patch_admin, monkeypatch, patch_db, fake_request):
        summary = {
            "summary_id": "csum_1",
            "source_type": "chat",
            "tenant_id": "t_a",
            "status": "active",
        }
        monkeypatch.setattr(routes_mod.ContextSummaryDB, "get_by_id", lambda sid, tenant_id=None: summary)
        conn, cursor = patch_db
        cursor.rowcount = 5  # 假装恢复 5 条消息

        result = await routes_mod.rollback_summary("csum_1", fake_request)
        assert result["success"] is True
        assert result["restored_message_count"] == 5
        # 验证两条 SQL 都执行了
        executed_sqls = [c.args[0] for c in cursor.execute.call_args_list]
        assert any("UPDATE chat_messages SET compacted = FALSE" in s for s in executed_sqls)
        # v3.2.1 P1-2：status 用占位符 + 枚举常量，不再硬编码 'rolled_back'
        assert any(
            "UPDATE chat_context_summaries SET status =" in s and "rolled_back" not in s
            for s in executed_sqls
        )
        # 参数中应包含 'rolled_back'（来自 ContextSummaryStatus.ROLLED_BACK.value）
        executed_params = [c.args[1] for c in cursor.execute.call_args_list if len(c.args) > 1]
        all_params = [p for params in executed_params for p in (params if isinstance(params, (list, tuple)) else [params])]
        assert "rolled_back" in all_params
        conn.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_rollback_channel_source(self, patch_admin, monkeypatch, patch_db, fake_request):
        summary = {
            "summary_id": "csum_2",
            "source_type": "wecom_kf",
            "tenant_id": "t_a",
        }
        monkeypatch.setattr(routes_mod.ContextSummaryDB, "get_by_id", lambda sid, tenant_id=None: summary)
        conn, cursor = patch_db
        cursor.rowcount = 3

        result = await routes_mod.rollback_summary("csum_2", fake_request)
        assert result["success"] is True
        executed_sqls = [c.args[0] for c in cursor.execute.call_args_list]
        assert any("UPDATE channel_messages SET compacted = FALSE" in s for s in executed_sqls)


# ============== manual_compress ==============

class TestManualCompress:
    @pytest.mark.asyncio
    async def test_manual_compress_success(self, patch_admin, monkeypatch, fake_request):
        """手动压缩调用 compress_session(force=True) 并返回 result 字典。

        v3.2.1 P0-1：平台管理员（tenant_filter=None）跳过租户校验，
        compress_session 直接被调用。"""
        fake_result = MagicMock()
        fake_result.summary_id = "csum_new"
        fake_result.compressed_message_count = 10
        fake_result.original_token_count = 1000
        fake_result.compressed_token_count = 300
        fake_result.compression_ratio = 0.3
        fake_result.fallback_used = False
        fake_result.trigger_reason = "force"
        fake_result.llm_provider = "deepseek"
        fake_result.llm_model = "deepseek-chat"

        mock_service = MagicMock()
        mock_service.compress_session = AsyncMock(return_value=fake_result)
        monkeypatch.setattr("src.memory.mid_term.get_compression_service", lambda: mock_service)

        result = await routes_mod.manual_compress("sess_1", fake_request, source_type="chat")
        assert result["success"] is True
        assert result["result"]["summary_id"] == "csum_new"
        # 验证 force=True
        _, kwargs = mock_service.compress_session.call_args
        assert kwargs.get("force") is True

    @pytest.mark.asyncio
    async def test_manual_compress_no_trigger(self, patch_admin, monkeypatch, fake_request):
        """compress_session 返回 None（消息数不足）→ 返回 result=None 友好提示。"""
        mock_service = MagicMock()
        mock_service.compress_session = AsyncMock(return_value=None)
        monkeypatch.setattr("src.memory.mid_term.get_compression_service", lambda: mock_service)

        result = await routes_mod.manual_compress("sess_1", fake_request, source_type="chat")
        assert result["success"] is True
        assert result["result"] is None

    @pytest.mark.asyncio
    async def test_manual_compress_tenant_isolation(
        self, monkeypatch, fake_request, tenant_a_admin
    ):
        """租户 A 管理员压缩租户 B 的 session → 403。

        v3.2.1 P0-1：租户校验**前置**（在 compress_session 调用之前）。
        service.get_session_tenant_id 返回 "t_b"，与 tenant_a_admin 的 tenant_filter="t_a"
        不匹配 → 抛 403。**且 compress_session 不应被调用**（避免脏数据）。
        """
        monkeypatch.setattr(routes_mod, "require_admin", lambda req: tenant_a_admin)
        mock_service = MagicMock()
        # session 实际归属 t_b
        mock_service.get_session_tenant_id = AsyncMock(return_value="t_b")
        mock_service.compress_session = AsyncMock(return_value=MagicMock())
        monkeypatch.setattr("src.memory.mid_term.get_compression_service", lambda: mock_service)

        with pytest.raises(HTTPException) as exc:
            await routes_mod.manual_compress("sess_of_b", fake_request, source_type="chat")
        assert exc.value.status_code == 403
        # 关键：校验失败时 compress_session 绝不能被调用
        mock_service.compress_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_manual_compress_tenant_match_proceeds(
        self, monkeypatch, fake_request, tenant_a_admin
    ):
        """租户 A 管理员压缩自己租户的 session → 校验通过，compress_session 被调用。

        v3.2.1 P0-1：tenant_filter="t_a"，get_session_tenant_id 返回 "t_a" →
        匹配，进入压缩流程。
        """
        monkeypatch.setattr(routes_mod, "require_admin", lambda req: tenant_a_admin)
        fake_result = MagicMock()
        fake_result.summary_id = "csum_ok"
        fake_result.compressed_message_count = 5
        fake_result.original_token_count = 100
        fake_result.compressed_token_count = 30
        fake_result.compression_ratio = 0.3
        fake_result.fallback_used = False
        fake_result.trigger_reason = "force"
        fake_result.llm_provider = "deepseek"
        fake_result.llm_model = "deepseek-chat"
        mock_service = MagicMock()
        mock_service.get_session_tenant_id = AsyncMock(return_value="t_a")
        mock_service.compress_session = AsyncMock(return_value=fake_result)
        monkeypatch.setattr("src.memory.mid_term.get_compression_service", lambda: mock_service)

        result = await routes_mod.manual_compress("sess_of_a", fake_request, source_type="chat")
        assert result["success"] is True
        assert result["result"]["summary_id"] == "csum_ok"
        mock_service.compress_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_manual_compress_session_not_found_returns_403(
        self, monkeypatch, fake_request, tenant_a_admin
    ):
        """租户管理员压缩不存在的 session → get_session_tenant_id 返回 None → 403。

        v3.2.1 P0-1：session 不存在（None）也走 403 路径，避免 compress_session
        内部去写脏数据。
        """
        monkeypatch.setattr(routes_mod, "require_admin", lambda req: tenant_a_admin)
        mock_service = MagicMock()
        mock_service.get_session_tenant_id = AsyncMock(return_value=None)
        mock_service.compress_session = AsyncMock(return_value=None)
        monkeypatch.setattr("src.memory.mid_term.get_compression_service", lambda: mock_service)

        with pytest.raises(HTTPException) as exc:
            await routes_mod.manual_compress("sess_ghost", fake_request, source_type="chat")
        assert exc.value.status_code == 403
        mock_service.compress_session.assert_not_called()
