"""数据分析数据表按智能体配置 API 单测（Phase 1，设计 §18.5）

覆盖权限矩阵全部分支：
- POST /schemas：普通用户非空名单 403；null/[] 按共享创建；管理员带名单创建；非法 ID 422
- PUT /schemas/{doc_id}：普通用户未提交保留原值；显式名单（含 null/[]）403；
  管理员显式清空恢复共享 / 显式替换 / 未提交保留锁内最新值（并发合并）
- PUT /schemas/{doc_id}/agent-scope：401/403/404/422/成功、单条 UPDATE 原子写
- GET /schemas：顶层标准化名单、has_outbound_knowledge_share、移动表识别条件
- DELETE /schemas/{doc_id}：有效数据表条件定位

测试策略：直接 await 端点函数，mock get_db_connection / require_admin /
embedding（与 test_put_travel_kb_metadata.py 同模式）。真实 PostgreSQL 的
jsonb 行为验证按计划属后续阶段；mock 保持 SQL/参数断言强度。
"""

import json
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api import data_analysis as da
from src.api.data_analysis import AgentScopeUpdate, SchemaSave

pytestmark = pytest.mark.api

TENANT = "tenant_t1"


# ============== 通用 mock 工具 ==============


class FakeCursor:
    """按 execute 次序消费结果的游标 mock；记录全部 SQL 与参数"""

    def __init__(self, results=None):
        self.results = list(results or [])
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchone(self):
        if not self.results:
            return None
        item = self.results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def fetchall(self):
        if not self.results:
            return []
        item = self.results.pop(0)
        return item if isinstance(item, list) else []

    def __iter__(self):
        return iter([])


def _conn_cm(cursor):
    conn = MagicMock()
    conn.cursor.return_value = cursor
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=conn)
    cm.__exit__ = MagicMock(return_value=None)
    return cm, conn


@contextmanager
def _patch_embedding():
    """mock API 层 embedding 客户端（0 token，跳过计费分支），并固定 key 读取，
    使测试不依赖 EMBEDDING_API_KEY 环境变量（容器/宿主机均可运行）"""
    client = MagicMock()
    client.embed_batch = AsyncMock(return_value=[[0.0] * 8])
    client.last_usage_tokens = 0
    client.model = "text-embedding-v3"
    with patch(
        "src.api.data_analysis.TextEmbeddingV3Client",
        return_value=client,
    ), patch(
        "src.config.settings.get_embedding_api_key",
        return_value="test-embedding-key",
    ):
        yield


def _request():
    """构造带认证上下文的 request mock（租户/身份由 require_admin mock 决定）"""
    request = MagicMock()
    request.headers = {}
    return request


def _admin(role="tenant_admin", tenant_id=TENANT):
    return {"user_id": "u1", "role": role, "tenant_id": tenant_id}


def _patch_require_admin(admin):
    return patch("src.saas.api.tenant_auth.require_admin", return_value=admin)


def _patch_catalog(subscribed=("sales-assistant",), expand_extra=()):
    """目录 mock：订阅查询返回 subscribed（DISTINCT 由真实库保证），副本实例附加 expand_extra"""
    cursor = MagicMock()
    cursor.fetchall.return_value = [{"subagent_type": t} for t in subscribed]
    cm, _conn = _conn_cm(cursor)
    db_patch = patch(
        "src.services.data_analysis.access_scope.get_db_connection",
        return_value=cm,
    )
    expand_patch = patch(
        "src.saas.permissions.checker.expand_with_tenant_instances",
        side_effect=lambda tid, ids: ids + list(expand_extra),
    )
    return db_patch, expand_patch


def _json_body(resp):
    return json.loads(resp.body)


# ============== PUT /schemas/{doc_id}/agent-scope ==============


class TestAgentScopeEndpoint:
    def _sql_calls(self, cursor):
        return [(sql, params) for sql, params in cursor.calls if "UPDATE documents" in sql]

    async def test_missing_field_is_rejected_by_model(self):
        """字段必传：缺失 422（pydantic 校验），显式 null 合法（恢复共享）"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            AgentScopeUpdate()
        assert AgentScopeUpdate(allowed_agent_ids=None).allowed_agent_ids is None
        assert AgentScopeUpdate(allowed_agent_ids=[]).allowed_agent_ids == []

    async def test_ordinary_user_forbidden_403(self):
        """普通用户（role=user）403，不产生任何写库动作"""
        cursor = FakeCursor()
        cm, _ = _conn_cm(cursor)
        with patch("src.api.data_analysis.get_current_tenant_id", return_value=TENANT), \
                _patch_require_admin(_admin(role="user")), \
                patch("src.api.data_analysis.get_db_connection", return_value=cm):
            resp = await da.update_schema_agent_scope(
                5, AgentScopeUpdate(allowed_agent_ids=["main"]), _request()
            )
        assert resp.status_code == 403
        assert not self._sql_calls(cursor)

    async def test_platform_admin_without_target_tenant_403(self):
        """platform_admin 无代管目标租户（admin tenant 为空）→ 403，禁止全局配置"""
        cursor = FakeCursor()
        cm, _ = _conn_cm(cursor)
        with patch("src.api.data_analysis.get_current_tenant_id", return_value=None), \
                _patch_require_admin(_admin(role="platform_admin", tenant_id=None)), \
                patch("src.api.data_analysis.get_db_connection", return_value=cm):
            resp = await da.update_schema_agent_scope(
                5, AgentScopeUpdate(allowed_agent_ids=[]), _request()
            )
        assert resp.status_code == 403

    async def test_platform_admin_target_tenant_mismatch_403(self):
        """代管目标租户与解析租户不一致 → 403（防跨租户写入）"""
        cursor = FakeCursor()
        cm, _ = _conn_cm(cursor)
        with patch("src.api.data_analysis.get_current_tenant_id", return_value=TENANT), \
                _patch_require_admin(_admin(role="platform_admin", tenant_id="tenant_other")), \
                patch("src.api.data_analysis.get_db_connection", return_value=cm):
            resp = await da.update_schema_agent_scope(
                5, AgentScopeUpdate(allowed_agent_ids=[]), _request()
            )
        assert resp.status_code == 403

    async def test_unauthenticated_raises_401(self):
        """未登录由 require_admin 抛 401"""
        with patch("src.api.data_analysis.get_current_tenant_id", return_value=TENANT), \
                _patch_require_admin(None):
            with pytest.raises(HTTPException) as exc_info:
                await da.update_schema_agent_scope(
                    5, AgentScopeUpdate(allowed_agent_ids=[]), _request()
                )
        assert exc_info.value.status_code == 401

    async def test_update_scope_success_atomic_jsonb_set(self):
        """管理员设置名单：单条 UPDATE jsonb_set 原子写，仅改名单，响应带回名单"""
        cursor = FakeCursor(results=[{"id": 5}])
        cm, conn = _conn_cm(cursor)
        db_patch, expand_patch = _patch_catalog(subscribed=("sales-assistant",))
        with patch("src.api.data_analysis.get_current_tenant_id", return_value=TENANT), \
                _patch_require_admin(_admin()), \
                patch("src.api.data_analysis.get_db_connection", return_value=cm), \
                db_patch, expand_patch:
            result = await da.update_schema_agent_scope(
                5,
                AgentScopeUpdate(allowed_agent_ids=["sales-assistant", "sales-assistant", "main"]),
                _request(),
            )

        assert result == {
            "success": True,
            "doc_id": 5,
            "allowed_agent_ids": ["sales-assistant", "main"],
        }
        updates = self._sql_calls(cursor)
        assert len(updates) == 1
        sql, params = updates[0]
        # 单条原子 UPDATE：jsonb_set 只改名单字段并转回 text 保留其余 metadata
        assert "SET metadata = jsonb_set(" in sql
        assert "'{allowed_agent_ids}'" in sql
        assert "%s::jsonb" in sql
        assert ")::text" in sql
        assert "FOR UPDATE" not in sql
        # 按当前解析租户 + 有效数据表条件定位（含移动表兜底）
        assert "source_type = %s AND tenant_id = %s" in sql
        assert "title ILIKE %s" in sql
        # 参数序：名单 JSON、doc_id、识别条件四参（source_type、租户、租户、标题前缀）
        assert params[0] == json.dumps(["sales-assistant", "main"], ensure_ascii=False)
        assert params[1] == 5
        assert params[3] == TENANT and params[4] == TENANT
        conn.commit.assert_called_once()

    async def test_restore_shared_with_empty_array(self):
        """显式 [] 恢复共享：写回空数组，免目录校验（不触发订阅查询）"""
        cursor = FakeCursor(results=[{"id": 5}])
        cm, _ = _conn_cm(cursor)
        db_patch, expand_patch = _patch_catalog()
        with patch("src.api.data_analysis.get_current_tenant_id", return_value=TENANT), \
                _patch_require_admin(_admin()), \
                patch("src.api.data_analysis.get_db_connection", return_value=cm), \
                db_patch, expand_patch as expand_mock:
            result = await da.update_schema_agent_scope(
                5, AgentScopeUpdate(allowed_agent_ids=[]), _request()
            )
        assert result["allowed_agent_ids"] == []
        expand_mock.assert_not_called()
        sql, params = self._sql_calls(cursor)[0]
        assert params[0] == "[]"

    async def test_restore_shared_with_explicit_null(self):
        """显式 null 与 [] 同义：恢复共享"""
        cursor = FakeCursor(results=[{"id": 5}])
        cm, _ = _conn_cm(cursor)
        with patch("src.api.data_analysis.get_current_tenant_id", return_value=TENANT), \
                _patch_require_admin(_admin()), \
                patch("src.api.data_analysis.get_db_connection", return_value=cm):
            result = await da.update_schema_agent_scope(
                5, AgentScopeUpdate(allowed_agent_ids=None), _request()
            )
        assert result["allowed_agent_ids"] == []

    async def test_invalid_new_agent_id_422(self):
        """新增非法 ID（未订阅）→ 422，不产生 UPDATE"""
        cursor = FakeCursor(results=[{"id": 5, "metadata": {}}])
        cm, _ = _conn_cm(cursor)
        db_patch, expand_patch = _patch_catalog(subscribed=("sales-assistant",))
        with patch("src.api.data_analysis.get_current_tenant_id", return_value=TENANT), \
                _patch_require_admin(_admin()), \
                patch("src.api.data_analysis.get_db_connection", return_value=cm), \
                db_patch, expand_patch:
            resp = await da.update_schema_agent_scope(
                5, AgentScopeUpdate(allowed_agent_ids=["ghost"]), _request()
            )
        assert resp.status_code == 422
        assert "ghost" in _json_body(resp)["error"]
        assert not self._sql_calls(cursor)

    async def test_copy_instance_id_accepted(self):
        """租户副本实例 ID 经目录附加后可用"""
        cursor = FakeCursor(results=[{"id": 5}])
        cm, _ = _conn_cm(cursor)
        db_patch, expand_patch = _patch_catalog(
            subscribed=("sales-assistant",), expand_extra=("sales-assistant#copy1",)
        )
        with patch("src.api.data_analysis.get_current_tenant_id", return_value=TENANT), \
                _patch_require_admin(_admin()), \
                patch("src.api.data_analysis.get_db_connection", return_value=cm), \
                db_patch, expand_patch:
            result = await da.update_schema_agent_scope(
                5,
                AgentScopeUpdate(allowed_agent_ids=["sales-assistant#copy1"]),
                _request(),
            )
        assert result["success"] is True

    async def test_not_found_404_indistinguishable(self):
        """未命中（不存在/其他租户/非有效数据表）统一 404，无副作用"""
        cursor = FakeCursor(results=[None])
        cm, conn = _conn_cm(cursor)
        db_patch, expand_patch = _patch_catalog(subscribed=("sales-assistant",))
        with patch("src.api.data_analysis.get_current_tenant_id", return_value=TENANT), \
                _patch_require_admin(_admin()), \
                patch("src.api.data_analysis.get_db_connection", return_value=cm), \
                db_patch, expand_patch:
            resp = await da.update_schema_agent_scope(
                999, AgentScopeUpdate(allowed_agent_ids=["main"]), _request()
            )
        assert resp.status_code == 404
        assert "不存在" in _json_body(resp)["error"]
        conn.commit.assert_not_called()

    async def test_db_error_uses_error_response(self):
        """数据库异常走现有 _error_response（500，脱敏 debug）"""
        cursor = FakeCursor(results=[RuntimeError("connection refused password=secret123")])
        cm, _ = _conn_cm(cursor)
        db_patch, expand_patch = _patch_catalog(subscribed=("sales-assistant",))
        with patch("src.api.data_analysis.get_current_tenant_id", return_value=TENANT), \
                _patch_require_admin(_admin()), \
                patch("src.api.data_analysis.get_db_connection", return_value=cm), \
                db_patch, expand_patch:
            resp = await da.update_schema_agent_scope(
                5, AgentScopeUpdate(allowed_agent_ids=["main"]), _request()
            )
        assert resp.status_code == 500
        body = _json_body(resp)
        assert body["success"] is False
        assert "secret123" not in body["debug"]


# ============== POST /schemas 名单权限 ==============


class TestSaveSchemaScope:
    def _req(self, allowed=None, omit=False):
        payload = {
            "table_name": "销售",
            "description": "",
            "source_info": "a.xlsx",
            "columns": [{"name": "a"}],
        }
        if not omit:
            payload["allowed_agent_ids"] = allowed
        return SchemaSave(**payload)

    async def test_user_nonempty_scope_403(self):
        """普通用户提交非空名单 → 403，不触达保存服务"""
        saver = AsyncMock(return_value={"success": True})
        with patch("src.api.data_analysis.get_current_tenant_id", return_value=TENANT), \
                _patch_require_admin(_admin(role="user")), \
                patch("src.api.data_analysis.save_schema_to_knowledge", saver):
            resp = await da.save_schema(self._req(["main"]), _request())
        assert resp.status_code == 403
        saver.assert_not_called()

    async def test_user_null_or_omitted_creates_shared(self):
        """普通用户不传/null/[]：按共享创建，saver 收到 None"""
        saver = AsyncMock(return_value={"success": True, "existing": False})
        for value, omit in ((None, False), (None, True), ([], False)):
            saver.reset_mock()
            with patch("src.api.data_analysis.get_current_tenant_id", return_value=TENANT), \
                    _patch_require_admin(_admin(role="user")), \
                    patch("src.api.data_analysis.save_schema_to_knowledge", saver):
                result = await da.save_schema(self._req(value, omit=omit), _request())
            assert result["success"] is True
            assert saver.call_args.kwargs["allowed_agent_ids"] is None

    async def test_admin_nonempty_scope_creates_restricted(self):
        """管理员带非空名单创建：名单传给保存服务"""
        saver = AsyncMock(return_value={"success": True, "existing": False})
        db_patch, expand_patch = _patch_catalog(subscribed=("sales-assistant",))
        with patch("src.api.data_analysis.get_current_tenant_id", return_value=TENANT), \
                _patch_require_admin(_admin()), \
                patch("src.api.data_analysis.save_schema_to_knowledge", saver), \
                db_patch, expand_patch:
            result = await da.save_schema(self._req(["main", "sales-assistant"]), _request())
        assert result["success"] is True
        assert saver.call_args.kwargs["allowed_agent_ids"] == ["main", "sales-assistant"]

    async def test_admin_invalid_scope_422(self):
        """管理员提交未订阅 ID → 422"""
        saver = AsyncMock()
        db_patch, expand_patch = _patch_catalog(subscribed=("sales-assistant",))
        with patch("src.api.data_analysis.get_current_tenant_id", return_value=TENANT), \
                _patch_require_admin(_admin()), \
                patch("src.api.data_analysis.save_schema_to_knowledge", saver), \
                db_patch, expand_patch:
            resp = await da.save_schema(self._req(["ghost"]), _request())
        assert resp.status_code == 422
        saver.assert_not_called()

    async def test_user_invalid_scope_shape_422_not_403(self):
        """普通用户提交形态非法名单 → 422（先于权限判断暴露参数错误一致性）"""
        saver = AsyncMock()
        with patch("src.api.data_analysis.get_current_tenant_id", return_value=TENANT), \
                _patch_require_admin(_admin(role="user")), \
                patch("src.api.data_analysis.save_schema_to_knowledge", saver):
            # pydantic 已挡非字符串元素；空串元素由标准化层 422
            resp = await da.save_schema(self._req([""]), _request())
        assert resp.status_code == 422
        saver.assert_not_called()


# ============== PUT /schemas/{doc_id} 名单权限与并发合并 ==============


def _schema_meta_json(allowed=None):
    meta = {
        "connector_id": None,
        "source_info": "a.xlsx",
        "table_name": "销售",
        "columns": [{"name": "a"}],
    }
    if allowed is not None:
        meta["allowed_agent_ids"] = allowed
    return json.dumps(meta, ensure_ascii=False)


class TestUpdateSchemaScope:
    async def _run(self, req, row_metadata, admin_role="tenant_admin"):
        """执行 update_schema：锁内 SELECT 返回 row_metadata；返回 (结果, cursor, conn)"""
        cursor = FakeCursor(results=[{"id": 5, "metadata": row_metadata}, {"id": 77}])
        cm, conn = _conn_cm(cursor)
        vec_client = MagicMock()
        vec_client.insert = AsyncMock()
        vec_client.delete_by_doc = AsyncMock()
        with patch("src.api.data_analysis.get_current_tenant_id", return_value=TENANT), \
                _patch_require_admin(_admin(role=admin_role)), \
                patch("src.api.data_analysis.get_db_connection", return_value=cm), \
                _patch_embedding(), \
                patch("src.api.data_analysis.get_vector_db", return_value=vec_client):
            result = await da.update_schema(5, req, _request())
        return result, cursor, conn

    def _update_metadata_param(self, cursor):
        for sql, params in cursor.calls:
            if sql.strip().startswith("UPDATE documents"):
                return json.loads(params[2])
        raise AssertionError("未找到 UPDATE documents")

    async def test_user_explicit_scope_403_even_null(self):
        """普通用户显式提交名单字段（含 null/[]）→ 403，无任何写库"""
        for value in (None, [], ["main"]):
            req = SchemaSave(
                table_name="销售",
                columns=[{"name": "a"}],
                allowed_agent_ids=value,
            )
            cursor = FakeCursor()
            cm, conn = _conn_cm(cursor)
            with patch("src.api.data_analysis.get_current_tenant_id", return_value=TENANT), \
                    _patch_require_admin(_admin(role="user")), \
                    patch("src.api.data_analysis.get_db_connection", return_value=cm):
                resp = await da.update_schema(5, req, _request())
            assert resp.status_code == 403
            assert not [c for c in cursor.calls if "UPDATE documents" in c[0]]
            conn.commit.assert_not_called()

    async def test_user_without_scope_preserves_latest_value(self):
        """普通用户未提交名单：锁内最新名单原样保留（含失效 ID）"""
        req = SchemaSave(table_name="销售", columns=[{"name": "a"}])
        _, cursor, conn = await self._run(
            req,
            _schema_meta_json(allowed=["sales-assistant", "gone-agent"]),
        )
        metadata = self._update_metadata_param(cursor)
        assert metadata["allowed_agent_ids"] == ["sales-assistant", "gone-agent"]
        conn.commit.assert_called_once()

    async def test_user_without_scope_absent_field_stays_absent(self):
        """普通用户未提交名单且原行无该字段：metadata 不新增字段"""
        req = SchemaSave(table_name="销售", columns=[{"name": "a"}])
        _, cursor, _ = await self._run(req, _schema_meta_json())
        metadata = self._update_metadata_param(cursor)
        assert "allowed_agent_ids" not in metadata

    async def test_admin_explicit_empty_restores_shared(self):
        """管理员显式 [] 恢复共享：metadata 写入空数组"""
        req = SchemaSave(table_name="销售", columns=[{"name": "a"}], allowed_agent_ids=[])
        _, cursor, _ = await self._run(req, _schema_meta_json(allowed=["main"]))
        metadata = self._update_metadata_param(cursor)
        assert metadata["allowed_agent_ids"] == []

    async def test_admin_explicit_null_restores_shared(self):
        """管理员显式 null 与 [] 同义"""
        req = SchemaSave(table_name="销售", columns=[{"name": "a"}], allowed_agent_ids=None)
        _, cursor, _ = await self._run(req, _schema_meta_json(allowed=["main"]))
        metadata = self._update_metadata_param(cursor)
        assert metadata["allowed_agent_ids"] == []

    async def test_admin_explicit_nonempty_replaces(self):
        """管理员显式非空名单替换原名单（目录校验通过）"""
        req = SchemaSave(
            table_name="销售", columns=[{"name": "a"}], allowed_agent_ids=["sales-assistant"]
        )
        db_patch, expand_patch = _patch_catalog(subscribed=("sales-assistant",))
        cursor = FakeCursor(results=[{"id": 5, "metadata": _schema_meta_json(allowed=["main"])}, {"id": 77}])
        cm, conn = _conn_cm(cursor)
        vec_client = MagicMock()
        vec_client.insert = AsyncMock()
        vec_client.delete_by_doc = AsyncMock()
        with patch("src.api.data_analysis.get_current_tenant_id", return_value=TENANT), \
                _patch_require_admin(_admin()), \
                patch("src.api.data_analysis.get_db_connection", return_value=cm), \
                _patch_embedding(), \
                patch("src.api.data_analysis.get_vector_db", return_value=vec_client), \
                db_patch, expand_patch:
            result = await da.update_schema(5, req, _request())
        assert result["success"] is True
        metadata = self._update_metadata_param(cursor)
        assert metadata["allowed_agent_ids"] == ["sales-assistant"]

    async def test_admin_explicit_invalid_422(self):
        """管理员显式提交未订阅 ID → 422，不写库"""
        req = SchemaSave(table_name="销售", columns=[{"name": "a"}], allowed_agent_ids=["ghost"])
        cursor = FakeCursor(results=[{"id": 5, "metadata": {}}])
        cm, conn = _conn_cm(cursor)
        db_patch, expand_patch = _patch_catalog(subscribed=())
        with patch("src.api.data_analysis.get_current_tenant_id", return_value=TENANT), \
                _patch_require_admin(_admin()), \
                patch("src.api.data_analysis.get_db_connection", return_value=cm), \
                _patch_embedding(), db_patch, expand_patch:
            resp = await da.update_schema(5, req, _request())
        assert resp.status_code == 422
        assert not [c for c in cursor.calls if "UPDATE documents" in c[0]]

    async def test_concurrent_scope_write_preserved_by_lock_and_merge(self):
        """并发合并回归：锁内 SELECT 读到并发 agent-scope 写入的最新名单，
        普通字段编辑合并该名单写回，不覆盖为共享/旧值。

        SQL 侧由 FOR UPDATE 行锁保证读到锁定时点最新已提交值；mock 验证
        「读锁内值 → 合并 → 写回」契约（真实锁行为属真实库验证阶段）。
        """
        req = SchemaSave(table_name="销售", columns=[{"name": "a"}])
        concurrent = _schema_meta_json(allowed=["sales-assistant"])
        _, cursor, _ = await self._run(req, concurrent)
        # 锁读 SQL 存在
        select_sqls = [sql for sql, _ in cursor.calls if "FOR UPDATE" in sql]
        assert len(select_sqls) == 1
        assert "SELECT id, metadata FROM documents" in select_sqls[0]
        metadata = self._update_metadata_param(cursor)
        # 并发写入的名单不被覆盖
        assert metadata["allowed_agent_ids"] == ["sales-assistant"]
        # 本次普通字段编辑正常写入
        assert metadata["table_name"] == "销售"

    async def test_scope_endpoint_interleaves_safely_with_general_edit(self):
        """反向交错：普通编辑持锁后 agent-scope 的 jsonb_set 单字段更新仍然安全
        ——范围更新只改 allowed_agent_ids 且不带 FOR UPDATE（等待行锁即可）"""
        cursor = FakeCursor(results=[{"id": 5}])
        cm, _ = _conn_cm(cursor)
        db_patch, expand_patch = _patch_catalog(subscribed=("sales-assistant",))
        with patch("src.api.data_analysis.get_current_tenant_id", return_value=TENANT), \
                _patch_require_admin(_admin()), \
                patch("src.api.data_analysis.get_db_connection", return_value=cm), \
                db_patch, expand_patch:
            result = await da.update_schema_agent_scope(
                5, AgentScopeUpdate(allowed_agent_ids=["sales-assistant"]), _request()
            )
        assert result["success"] is True
        sql, _ = [(s, p) for s, p in cursor.calls if "UPDATE documents" in s][0]
        # 单字段原子更新：不整行覆盖 metadata（title/raw_text/summary 均不动），
        # 避免与持锁的普通编辑互相覆盖
        assert "jsonb_set" in sql
        assert "title = %s" not in sql and "raw_text = %s" not in sql

    async def test_not_found_404_for_update(self):
        """非有效数据表/其他租户 → 404"""
        req = SchemaSave(table_name="销售", columns=[{"name": "a"}])
        cursor = FakeCursor(results=[None])
        cm, _ = _conn_cm(cursor)
        with patch("src.api.data_analysis.get_current_tenant_id", return_value=TENANT), \
                _patch_require_admin(_admin()), \
                patch("src.api.data_analysis.get_db_connection", return_value=cm), \
                _patch_embedding():
            resp = await da.update_schema(999, req, _request())
        assert resp.status_code == 404

    async def test_moved_table_locatable_by_valid_table_condition(self):
        """已移动表（source_type 被覆盖）：凭有效数据表条件仍可编辑"""
        moved_meta = json.dumps(
            {"table_name": "销售", "columns": [{"name": "a"}], "allowed_agent_ids": ["main"]},
            ensure_ascii=False,
        )
        req = SchemaSave(table_name="销售", columns=[{"name": "a"}])
        _, cursor, _ = await self._run(req, moved_meta)
        select_sql, select_params = cursor.calls[0]
        assert "source_type = %s AND tenant_id = %s" in select_sql
        assert "title ILIKE %s" in select_sql
        # 参数序：doc_id + 识别条件四参（source_type、租户、租户、标题前缀）
        assert select_params[0] == 5
        assert select_params[2] == TENANT
        assert select_params[4] == "[数据表] %"


# ============== GET /schemas 增强 ==============


class TestListSchemas:
    async def _run(self, rows, has_share):
        cursor = FakeCursor(
            results=[
                [],  # data_connectors（fetchall）
                {"cnt": len(rows)},  # COUNT（fetchone）
                rows,  # 行结果（fetchall）
                {"has_share": has_share},  # EXISTS（fetchone）
            ]
        )
        cm, _ = _conn_cm(cursor)
        with patch("src.api.data_analysis.get_current_tenant_id", return_value=TENANT), \
                patch("src.api.data_analysis.get_db_connection", return_value=cm):
            result = await da.list_schemas(_request())
        return result, cursor

    async def test_top_level_allowed_agent_ids_and_no_meta_nesting(self):
        """条目顶层返回标准化名单；metadata 不再嵌套名单字段"""
        row = {
            "id": 5,
            "title": "[数据表] 销售",
            "source_type": "data-analysis-metadata",
            "metadata": _schema_meta_json(allowed=["main", "main", "sales-assistant"]),
            "summary": "",
            "created_at": None,
        }
        result, cursor = await self._run([row], has_share=True)
        item = result["schemas"][0]
        assert item["allowed_agent_ids"] == ["main", "sales-assistant"]
        assert "allowed_agent_ids" not in item["metadata"]
        assert result["has_outbound_knowledge_share"] is True

    async def test_shared_table_standardizes_to_empty(self):
        """共享表（无字段/null/[]/异常配置）标准化为空名单"""
        rows = [
            {"id": 1, "title": "[数据表] A", "source_type": "data-analysis-metadata",
             "metadata": _schema_meta_json(), "summary": "", "created_at": None},
            {"id": 2, "title": "[数据表] B", "source_type": "data-analysis-metadata",
             "metadata": _schema_meta_json(allowed=[]), "summary": "", "created_at": None},
            {"id": 3, "title": "[数据表] C", "source_type": "data-analysis-metadata",
             "metadata": _schema_meta_json(allowed=None), "summary": "", "created_at": None},
            {"id": 4, "title": "[数据表] D", "source_type": "data-analysis-metadata",
             "metadata": _schema_meta_json(allowed="bad"), "summary": "", "created_at": None},
        ]
        result, _ = await self._run(rows, has_share=False)
        assert all(item["allowed_agent_ids"] == [] for item in result["schemas"])
        assert result["has_outbound_knowledge_share"] is False

    async def test_valid_table_condition_used_for_list_and_count(self):
        """列表与计数共用有效数据表识别条件（含移动兜底），不单看 source_type"""
        row = {"id": 1, "title": "[数据表] 销售", "source_type": "kb-doc",
               "metadata": _schema_meta_json(), "summary": "", "created_at": None}
        result, cursor = await self._run([row], has_share=False)
        count_sql = cursor.calls[1][0]
        list_sql = cursor.calls[2][0]
        for sql in (count_sql, list_sql):
            assert "source_type = %s AND tenant_id = %s" in sql
            assert "title ILIKE %s" in sql
            assert "jsonb_typeof(metadata::jsonb->'table_name') = 'string'" in sql

    async def test_outbound_share_exists_uses_single_query(self):
        """has_outbound_knowledge_share：同一连接一次 EXISTS（from_tenant_id=当前租户）"""
        row = {"id": 1, "title": "[数据表] 销售", "source_type": "data-analysis-metadata",
               "metadata": _schema_meta_json(), "summary": "", "created_at": None}
        _, cursor = await self._run([row], has_share=True)
        share_calls = [
            (sql, params) for sql, params in cursor.calls if "tenant_knowledge_shares" in sql
        ]
        assert len(share_calls) == 1
        sql, params = share_calls[0]
        assert "EXISTS(SELECT 1 FROM tenant_knowledge_shares WHERE from_tenant_id = %s)" in sql
        assert params == (TENANT,)


# ============== DELETE 有效数据表定位 ==============


class TestDeleteSchema:
    async def test_delete_uses_valid_table_condition(self):
        """删除定位改用有效数据表条件（移动表可删），其余流程不变"""
        cursor = FakeCursor(results=[{"id": 5}])
        cm, conn = _conn_cm(cursor)
        vec_client = MagicMock()
        vec_client.delete_by_doc = AsyncMock()
        with patch("src.api.data_analysis.get_current_tenant_id", return_value=TENANT), \
                patch("src.api.data_analysis.get_db_connection", return_value=cm), \
                patch("src.api.data_analysis.get_vector_db", return_value=vec_client):
            result = await da.delete_schema(5, _request())
        assert result["success"] is True
        select_sql, select_params = cursor.calls[0]
        assert "source_type = %s AND tenant_id = %s" in select_sql
        assert "title ILIKE %s" in select_sql
        assert select_params[0] == 5
        vec_client.delete_by_doc.assert_awaited_once_with(5)
        conn.commit.assert_called_once()

    async def test_delete_missing_404(self):
        cursor = FakeCursor(results=[None])
        cm, conn = _conn_cm(cursor)
        with patch("src.api.data_analysis.get_current_tenant_id", return_value=TENANT), \
                patch("src.api.data_analysis.get_db_connection", return_value=cm):
            resp = await da.delete_schema(999, _request())
        assert resp.status_code == 404
        conn.commit.assert_not_called()


class TestReviewRegressions:
    _run = TestUpdateSchemaScope._run
    _update_metadata_param = TestUpdateSchemaScope._update_metadata_param
    @pytest.mark.parametrize("legacy_source", [False, True])
    async def test_edit_preserves_excel_location_and_can_load_selected_sheet(self, tmp_path, legacy_source):
        import pandas as pd
        from src.tools.data_analysis.data_analyzer import DataAnalyzer
        from src.tools.data_analysis.analysis_agent import fetch_table_metadata_from_db
        path = tmp_path / "input.xlsx"
        with pd.ExcelWriter(path) as writer:
            pd.DataFrame({"value": [1]}).to_excel(writer, sheet_name="other", index=False)
            pd.DataFrame({"value": [42]}).to_excel(writer, sheet_name="selected", index=False)
        metadata = {"table_name": "sales", "columns": [{"name": "value"}], "source_info": str(path), "allowed_agent_ids": ["legacy"]}
        if not legacy_source:
            metadata["source"] = {"type": "excel", "file_path": str(path), "sheet_name": "selected"}
        req = SchemaSave(table_name="sales", description="updated", columns=[{"name": "value"}])
        result, cursor, _ = await self._run(req, metadata)
        assert result["success"]
        updated = self._update_metadata_param(cursor)
        assert updated["source_info"] == str(path)
        assert updated["allowed_agent_ids"] == ["legacy"]
        row = {"id": 5, "source_type": "data-analysis-metadata", "tenant_id": TENANT, "metadata": updated}
        db_cursor = FakeCursor([row])
        cm, _ = _conn_cm(db_cursor)
        with patch("src.db.database.get_db_connection", return_value=cm):
            load_metadata = fetch_table_metadata_from_db("5", TENANT, None, "legacy")
        analyzer = DataAnalyzer(tenant_id=TENANT)
        await analyzer.load_table(load_metadata)
        assert analyzer._tables["5"]["value"].tolist() == ([1] if legacy_source else [42])

    async def test_edit_preserves_database_connector_and_can_load(self):
        import pandas as pd
        from src.tools.data_analysis.data_analyzer import DataAnalyzer
        from src.tools.data_analysis.analysis_agent import fetch_table_metadata_from_db
        metadata = {"table_name": "sales", "columns": [{"name": "value"}], "connector_id": "connector-1"}
        result, cursor, _ = await self._run(SchemaSave(table_name="sales", description="updated", columns=[{"name": "value"}]), metadata)
        assert result["success"]
        updated = self._update_metadata_param(cursor)
        assert updated["connector_id"] == "connector-1"
        row = {"id": 5, "source_type": "data-analysis-metadata", "tenant_id": TENANT, "metadata": updated}
        cm, _ = _conn_cm(FakeCursor([row]))
        with patch("src.db.database.get_db_connection", return_value=cm):
            load_metadata = fetch_table_metadata_from_db("5", TENANT, None, "main")
        connector = {"tenant_id": TENANT, "db_type": "mysql", "host": "localhost", "port": 3306, "database_name": "test", "username": "test", "password_encrypted": "test"}
        cm, _ = _conn_cm(FakeCursor([connector]))
        engine = MagicMock()
        with patch("src.db.database.get_db_connection", return_value=cm), patch("src.tools.data_analysis.data_analyzer.decrypt_password", return_value="test"), patch("sqlalchemy.create_engine", return_value=engine), patch("pandas.read_sql", return_value=pd.DataFrame({"value": [42]})) as read_sql:
            analyzer = DataAnalyzer(tenant_id=TENANT)
            await analyzer.load_table(load_metadata)
        assert "SELECT * FROM sales" in read_sql.call_args.args[0]
        assert analyzer._tables["5"]["value"].tolist() == [42]
        engine.dispose.assert_called_once()

    @pytest.mark.parametrize("endpoint", ["scope", "schema"])
    async def test_existing_unavailable_id_can_be_retained_but_new_invalid_id_cannot(self, endpoint):
        for requested, expected in [(["legacy", "sales-assistant"], 200), (["legacy"], 200), (["legacy", "ghost"], 422), (["sales-assistant"], 200)]:
            db_patch, expand_patch = _patch_catalog()
            with db_patch, expand_patch:
                if endpoint == "schema":
                    result, cursor, _ = await self._run(SchemaSave(table_name="sales", columns=[], allowed_agent_ids=requested), {"allowed_agent_ids": ["legacy"]})
                else:
                    cursor = FakeCursor([{"id": 5, "metadata": {"allowed_agent_ids": ["legacy"]}}])
                    cm, _ = _conn_cm(cursor)
                    with patch("src.api.data_analysis.get_current_tenant_id", return_value=TENANT), _patch_require_admin(_admin()), patch("src.api.data_analysis.get_db_connection", return_value=cm):
                        result = await da.update_schema_agent_scope(5, AgentScopeUpdate(allowed_agent_ids=requested), _request())
            if expected == 200:
                assert result["success"]
            else:
                assert result.status_code == 422
                assert not [sql for sql, _ in cursor.calls if "UPDATE documents" in sql]
