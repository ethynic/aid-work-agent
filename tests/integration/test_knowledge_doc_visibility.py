"""知识库文档可见性与外部文档边界集成测试（真实 PG）

公众号内容入知识库 WP2（设计 §7.3/§7.4），覆盖验收矩阵「软删除」与
「兼容回归」在知识库侧的部分：
- deleted / 过期文档检索（FTS 真实查询）不可见；active 外部文档可见
- 列表/计数默认隐藏 deleted；include_deleted 放行；过期文档管理端可见
- 外部文档移动/物理删除被拒绝；手动上传文档移动/删除回归
- 外部无文件文档下载/票据 400 + 原文 URL；deleted 文档 chunks 租户 404、
  platform_admin 可审计

DB 不可达时整模块 pytest.skip（tests/integration/conftest.py init_db_pool）。
"""

import json
import uuid
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest
from starlette.requests import Request

pytestmark = pytest.mark.integration

from src.saas.context import set_tenant_context, clear_tenant_context

EXT_URL = "https://mp.weixin.qq.com/s/wp2integ"
# FTS 用 distinctive token（to_tsvector('simple') 中文不分词，整串精确匹配）
FTS_TOKEN = "wxmpvisibilitytoken"


def _make_api_request(user_role="unset", path="/api/knowledge/documents", method="GET"):
    scope = {
        "type": "http", "method": method, "path": path, "raw_path": path.encode(),
        "headers": [], "query_string": b"", "server": ("testserver", 80),
        "scheme": "http", "http_version": "1.1",
    }
    request = Request(scope)
    if user_role != "unset":
        request.state.user_role = user_role
    return request


@pytest.fixture
def env(tmp_path):
    """单租户 + 四类文档：手动 active / 手动 deleted / 过期外部 / active 外部（无文件）"""
    from src.db.database import get_db_connection
    from src.saas.db.tenant_db import TenantDB

    code = uuid.uuid4().hex[:6].upper()
    tenant = TenantDB.create(
        company_name=f"知识库测试租户-WP2-{code}", tenant_code=f"T{code}",
        contact_name="测试联系人", contact_phone="13800000000")
    if not tenant:
        pytest.skip("无法创建测试租户（DB 不可用）")
    tid = tenant["tenant_id"]

    manual_file = tmp_path / "kb_wp2_manual.txt"
    manual_file.write_text("手动上传正文", encoding="utf-8")

    def _insert_doc(title, origin="manual_upload", status="active",
                    expires_at=None, file_path=None, metadata=None):
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO documents
                    (tenant_id, title, source_type, file_type, file_path,
                     file_size, total_chunks, embedding_model,
                     origin, status, expires_at, metadata)
                VALUES (%s, %s, 'file', 'txt', %s, 24, 1, 'text-embedding-v3',
                        %s, %s, %s, %s)
                RETURNING id
            """, (tid, title, file_path, origin, status, expires_at,
                  json.dumps(metadata) if metadata else None))
            doc_id = cur.fetchone()["id"]
            cur.execute(
                "INSERT INTO chunks (doc_id, chunk_index, text, tokens, metadata) "
                "VALUES (%s, 0, %s, 5, '{}') RETURNING id",
                (doc_id, f"{title} 正文 {FTS_TOKEN}"))
            chunk_id = cur.fetchone()["id"]
            conn.commit()
        return doc_id, chunk_id

    doc_manual, chunk_manual = _insert_doc("手动文档", file_path=str(manual_file))
    doc_deleted, chunk_deleted = _insert_doc("已删除文档", status="deleted")
    doc_expired, chunk_expired = _insert_doc(
        "过期外部文档", origin="wechat_mp",
        expires_at=datetime.now() - timedelta(hours=1))
    doc_external, chunk_external = _insert_doc(
        "外部文档", origin="wechat_mp", metadata={"original_url": EXT_URL})

    yield {
        "tenant": tid,
        "doc_manual": doc_manual, "chunk_manual": chunk_manual,
        "doc_deleted": doc_deleted, "chunk_deleted": chunk_deleted,
        "doc_expired": doc_expired, "chunk_expired": chunk_expired,
        "doc_external": doc_external, "chunk_external": chunk_external,
        "manual_file": str(manual_file),
    }

    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM chunks WHERE doc_id IN (SELECT id FROM documents WHERE tenant_id = %s)", (tid,))
            cur.execute("DELETE FROM documents WHERE tenant_id = %s", (tid,))
            conn.commit()
    except Exception:
        pass
    TenantDB.delete(tid)
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM tenants WHERE tenant_id = %s", (tid,))
            conn.commit()
    except Exception:
        pass


def _fts_search(env, tenant_id="use-env"):
    """真实 FTS 检索（连接池，无 mock），返回 chunk_id 列表"""
    from src.knowledge.retriever.hybrid_retriever import HybridRetriever
    retriever = HybridRetriever(vector_db=MagicMock(), embedding_client=MagicMock(), conn=None)
    tid = env["tenant"] if tenant_id == "use-env" else tenant_id
    return [cid for cid, _ in retriever._postgres_fts_search(FTS_TOKEN, 50, tenant_id=tid)]


class TestRetrievalVisibility:
    def test_deleted_and_expired_invisible_in_fts(self, env):
        """deleted 与过期文档 FTS 检索不可见；手动与 active 外部文档可见（阳性对照）"""
        ids = _fts_search(env)
        assert env["chunk_manual"] in ids
        assert env["chunk_external"] in ids
        assert env["chunk_deleted"] not in ids
        assert env["chunk_expired"] not in ids

    def test_deleted_invisible_in_global_view(self, env):
        """platform_admin 全局检索同样不返回 deleted/过期文档"""
        ids = _fts_search(env, tenant_id=None)
        # 全局视图走 global_view 分支（tenant None 时收窄到无主，此处用 global_view=True）
        from src.knowledge.retriever.hybrid_retriever import HybridRetriever
        retriever = HybridRetriever(vector_db=MagicMock(), embedding_client=MagicMock(), conn=None)
        ids = [cid for cid, _ in retriever._postgres_fts_search(
            FTS_TOKEN, 50, tenant_id=None, global_view=True)]
        assert env["chunk_manual"] in ids
        assert env["chunk_deleted"] not in ids
        assert env["chunk_expired"] not in ids


class TestListCountVisibility:
    def test_default_hides_deleted_keeps_expired(self, env):
        """默认列表隐藏 deleted；过期文档仍可管理查看（expires_at 只限检索）"""
        from src.knowledge.service import KnowledgeBaseService
        svc = KnowledgeBaseService()
        ids = [d["id"] for d in svc.list_documents(tenant_id=env["tenant"])]
        assert env["doc_manual"] in ids
        assert env["doc_external"] in ids
        assert env["doc_expired"] in ids
        assert env["doc_deleted"] not in ids
        assert svc.count_documents(tenant_id=env["tenant"]) == 3

    def test_include_deleted_shows_deleted(self, env):
        from src.knowledge.service import KnowledgeBaseService
        svc = KnowledgeBaseService()
        ids = [d["id"] for d in svc.list_documents(tenant_id=env["tenant"], include_deleted=True)]
        assert env["doc_deleted"] in ids
        assert svc.count_documents(tenant_id=env["tenant"], include_deleted=True) == 4

    def test_category_count_excludes_deleted(self, env):
        """分类 document_count 不计 deleted 文档（与列表/计数口径一致，WP2 P2）；
        过期文档（status 仍 active）计入"""
        from src.knowledge.service import KnowledgeBaseService
        svc = KnowledgeBaseService()
        svc.create_category(env["tenant"], "file", "默认分类")
        cats = svc.list_categories(env["tenant"])
        cnt = {c["source_type"]: c["document_count"] for c in cats}
        # fixture 4 篇同属 source_type='file'：active 手动 + active 外部 + 过期外部计入，deleted 不计
        assert cnt["file"] == 3

    def test_origin_filter(self, env):
        from src.knowledge.service import KnowledgeBaseService
        svc = KnowledgeBaseService()
        docs = svc.list_documents(tenant_id=env["tenant"], origin="wechat_mp")
        ids = {d["id"] for d in docs}
        assert ids == {env["doc_expired"], env["doc_external"]}
        # 响应模型字段：origin/status/expires_at 透出
        ext = next(d for d in docs if d["id"] == env["doc_external"])
        assert ext["origin"] == "wechat_mp"
        assert ext["status"] == "active"
        assert ext["expires_at"] is None
        exp = next(d for d in docs if d["id"] == env["doc_expired"])
        assert exp["expires_at"]  # 过期时间已序列化透出

    async def test_api_list_route_default_and_admin(self, env):
        """API 层：默认隐藏 deleted；include_deleted 仅 platform_admin 生效"""
        from src.knowledge import api as kb_api

        try:
            set_tenant_context(env["tenant"], "u1")
            resp = await kb_api.list_documents(
                include_deleted=True,
                http_request=_make_api_request(user_role="employee"))
            ids = [item.id for item in resp["items"]]
            assert env["doc_deleted"] not in ids  # 非管理员 include_deleted 被忽略
            assert resp["total"] == 3
        finally:
            clear_tenant_context()


class TestExternalDocBoundary:
    def test_move_external_rejected(self, env):
        """外部文档移动被拒绝；同批含手动文档也整批拒绝"""
        from src.knowledge.service import KnowledgeBaseService
        svc = KnowledgeBaseService()
        svc.create_category(env["tenant"], "other", "其他")
        result = svc.move_documents(env["tenant"], [env["doc_external"], env["doc_manual"]], "other", None)
        assert result["success"] is False
        assert result["status"] == 400
        assert "同步任务管理" in result["error"]
        # 手动文档未被波及（整批拒绝，source_type 未变）
        from src.db.database import get_db_connection
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT source_type FROM documents WHERE id = %s", (env["doc_manual"],))
            assert cur.fetchone()["source_type"] == "file"

    @pytest.mark.asyncio
    async def test_delete_external_rejected(self, env):
        """外部文档物理删除被拒绝，文档保留"""
        from src.knowledge.service import KnowledgeBaseService
        svc = KnowledgeBaseService()
        result = await svc.delete_document(env["doc_external"], tenant_id=env["tenant"])
        assert result["success"] is False
        assert result["status"] == 400
        from src.db.database import get_db_connection
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM documents WHERE id = %s", (env["doc_external"],))
            assert cur.fetchone() is not None

    @pytest.mark.asyncio
    async def test_delete_manual_regression(self, env):
        """手动上传文档删除行为零变化（回归）"""
        from unittest.mock import AsyncMock, patch
        from src.knowledge.service import KnowledgeBaseService
        svc = KnowledgeBaseService()
        with patch("src.knowledge.service.get_vector_db", return_value=AsyncMock()):
            result = await svc.delete_document(env["doc_manual"], tenant_id=env["tenant"])
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_download_external_400_with_original_url(self, env):
        """外部无文件文档下载返回明确错误 + 原文 URL"""
        from fastapi import HTTPException
        from src.knowledge import api as kb_api

        try:
            set_tenant_context(env["tenant"], "u1")
            with pytest.raises(HTTPException) as exc_info:
                await kb_api.download_document(env["doc_external"])
            assert exc_info.value.status_code == 400
            assert exc_info.value.detail["original_url"] == EXT_URL

            resp = await kb_api.create_download_ticket(env["doc_external"])
            assert resp.status_code == 400
            assert json.loads(bytes(resp.body))["original_url"] == EXT_URL
        finally:
            clear_tenant_context()

    @pytest.mark.asyncio
    async def test_chunks_deleted_visibility(self, env):
        """deleted 文档 chunks：租户 404；platform_admin 可审计"""
        from fastapi import HTTPException
        from src.knowledge import api as kb_api

        try:
            set_tenant_context(env["tenant"], "u1")
            with pytest.raises(HTTPException) as exc_info:
                await kb_api.get_document_chunks(
                    env["doc_deleted"], http_request=_make_api_request(user_role="employee"))
            assert exc_info.value.status_code == 404
        finally:
            clear_tenant_context()

        # platform_admin 全局视图（无租户上下文）可审计 deleted 文档分块
        try:
            set_tenant_context(None, "admin-1")
            resp = await kb_api.get_document_chunks(
                env["doc_deleted"], http_request=_make_api_request(user_role="platform_admin"))
            body = json.loads(bytes(resp.body))
            assert body["count"] == 1
            assert body["chunks"][0]["chunk_id"] == env["chunk_deleted"]
        finally:
            clear_tenant_context()
