"""知识库对象级租户防护集成测试（真实 PG）

安全加固设计 §2.4（对象级租户保护）：
- delete_document：跨租户 → 视为不存在（不执行删除）；本租户 → 正常删除（doc+chunks+文件）；
  无租户上下文 → 真实租户文档不可见（不删除），仅可删无主文档（与检索/下载侧口径一致）
- get_document_chunks：跨租户 → 空（JOIN documents 租户条件）；无租户上下文 → 真实租户文档为空
- list/count：无租户上下文（匿名/租户解析失败）→ 收窄到无主文档，真实租户文档不可见
- download 路由：跨租户无共享授权 → 403；本租户 → 正常返回文件；无租户上下文 → 403
- DELETE/chunks 路由：跨租户与不存在统一 404，不泄漏存在性

DB 不可达时整模块 pytest.skip（tests/integration/conftest.py init_db_pool）。
"""

import uuid

import pytest

pytestmark = pytest.mark.integration

from src.saas.context import set_tenant_context, clear_tenant_context


@pytest.fixture
def env(tmp_path):
    """双租户 + 租户 A 文档（带真实临时文件）+ 无主文档 + 各一条 chunk"""
    from src.db.database import get_db_connection
    from src.saas.db.tenant_db import TenantDB

    code = uuid.uuid4().hex[:6].upper()
    tenant_a = TenantDB.create(
        company_name=f"知识库测试租户-A-{code}", tenant_code=f"T{code}",
        contact_name="测试联系人", contact_phone="13800000000")
    tenant_b = TenantDB.create(
        company_name=f"知识库测试租户-B-{code}", tenant_code=f"U{code}",
        contact_name="测试联系人", contact_phone="13800000001")
    if not tenant_a or not tenant_b:
        pytest.skip("无法创建测试租户（DB 不可用）")
    tenant_a_id, tenant_b_id = tenant_a["tenant_id"], tenant_b["tenant_id"]

    # 真实临时文件（download 成功路径与 delete 文件清理断言用）
    doc_file = tmp_path / "kb_doc.txt"
    doc_file.write_text("知识库租户测试正文", encoding="utf-8")
    unowned_file = tmp_path / "kb_doc_unowned.txt"
    unowned_file.write_text("知识库无主文档正文", encoding="utf-8")

    def _insert_doc(tenant_id, file_path):
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO documents
                    (tenant_id, title, source_type, file_type, file_path,
                     file_size, total_chunks, embedding_model)
                VALUES (%s, '租户测试文档', 'file', 'txt', %s, 24, 1, 'text-embedding-v3')
                RETURNING id
            """, (tenant_id, str(file_path)))
            doc_id = cur.fetchone()["id"]
            conn.commit()
        return doc_id

    doc_a = _insert_doc(tenant_a_id, doc_file)
    doc_unowned = _insert_doc(None, unowned_file)

    def _insert_chunk(doc_id):
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO chunks (doc_id, chunk_index, text, tokens, metadata)
                VALUES (%s, 0, '租户测试分块', 5, '{}')
                RETURNING id
            """, (doc_id,))
            chunk_id = cur.fetchone()["id"]
            conn.commit()
        return chunk_id

    chunk_a = _insert_chunk(doc_a)
    chunk_unowned = _insert_chunk(doc_unowned)

    yield {
        "tenant_a": tenant_a_id, "tenant_b": tenant_b_id,
        "doc_a": doc_a, "chunk_a": chunk_a, "doc_file": str(doc_file),
        "doc_unowned": doc_unowned, "chunk_unowned": chunk_unowned,
        "unowned_file": str(unowned_file),
    }

    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM chunks WHERE doc_id = ANY(%s)", ([doc_a, doc_unowned],))
            cur.execute("DELETE FROM documents WHERE id = ANY(%s)", ([doc_a, doc_unowned],))
            conn.commit()
    except Exception:
        pass
    for f in (doc_file, unowned_file):
        try:
            f.unlink()
        except OSError:
            pass
    for tid in (tenant_a_id, tenant_b_id):
        TenantDB.delete(tid)
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM tenants WHERE tenant_id = ANY(%s)",
                ([tenant_a_id, tenant_b_id],))
            conn.commit()
    except Exception:
        pass


def _doc_exists(doc_id) -> bool:
    from src.db.database import get_db_connection
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM documents WHERE id = %s", (doc_id,))
        return cur.fetchone() is not None


class TestKnowledgeTenantGuard:
    # ===== service 层：delete_document =====

    async def test_delete_cross_tenant_not_found(self, env):
        """跨租户删除 → 视为不存在，文档仍存在"""
        from src.knowledge.service import KnowledgeBaseService
        svc = KnowledgeBaseService()

        result = await svc.delete_document(env["doc_a"], tenant_id=env["tenant_b"])

        assert result["success"] is False
        assert result["error"] == "文档不存在"
        assert _doc_exists(env["doc_a"]) is True

    async def test_delete_none_tenant_real_doc_invisible(self, env):
        """无租户上下文删除真实租户文档 → 按不存在处理，文档保留"""
        from src.knowledge.service import KnowledgeBaseService
        svc = KnowledgeBaseService()

        result = await svc.delete_document(env["doc_a"], tenant_id=None)

        assert result["success"] is False
        assert _doc_exists(env["doc_a"]) is True

    async def test_delete_none_tenant_unowned_doc_allowed(self, env):
        """无租户上下文删除无主文档 → 允许（口径同检索/下载侧）"""
        from unittest.mock import AsyncMock, patch
        from src.db.database import get_db_connection
        from src.knowledge.service import KnowledgeBaseService
        svc = KnowledgeBaseService()

        with patch("src.knowledge.service.get_vector_db", return_value=AsyncMock()):
            result = await svc.delete_document(env["doc_unowned"], tenant_id=None)

        assert result["success"] is True
        assert _doc_exists(env["doc_unowned"]) is False
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) AS c FROM chunks WHERE doc_id = %s", (env["doc_unowned"],))
            assert cur.fetchone()["c"] == 0

    async def test_delete_own_tenant_success(self, env):
        """本租户删除：doc + chunks 级联清除（向量库走 mock，验证 SQL 链路）"""
        from unittest.mock import AsyncMock, patch
        from src.db.database import get_db_connection
        from src.knowledge.service import KnowledgeBaseService
        svc = KnowledgeBaseService()

        with patch("src.knowledge.service.get_vector_db", return_value=AsyncMock()):
            result = await svc.delete_document(env["doc_a"], tenant_id=env["tenant_a"])

        assert result["success"] is True
        assert _doc_exists(env["doc_a"]) is False
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) AS c FROM chunks WHERE doc_id = %s", (env["doc_a"],))
            assert cur.fetchone()["c"] == 0

    # ===== service 层：get_document_chunks =====

    def test_get_chunks_cross_tenant_empty(self, env):
        """跨租户获取分块 → 空（JOIN documents 租户条件）"""
        from src.knowledge.service import KnowledgeBaseService
        svc = KnowledgeBaseService()

        cross = svc.get_document_chunks(env["doc_a"], tenant_id=env["tenant_b"])
        own = svc.get_document_chunks(env["doc_a"], tenant_id=env["tenant_a"])

        assert cross == []
        assert len(own) == 1
        assert own[0]["chunk_id"] == env["chunk_a"]

    def test_get_chunks_none_tenant_real_doc_empty(self, env):
        """无租户上下文获取真实租户文档分块 → 空（fail-closed，不泄露数据）"""
        from src.knowledge.service import KnowledgeBaseService
        svc = KnowledgeBaseService()

        assert svc.get_document_chunks(env["doc_a"], tenant_id=None) == []

    # ===== service 层：list/count 租户收窄 =====

    def test_list_count_none_tenant_narrowed(self, env):
        """无租户上下文（匿名/租户解析失败）list/count → 收窄到无主文档，
        真实租户文档不可见（§2.4：数据访问点不依赖上游是否传租户）"""
        from src.knowledge.service import KnowledgeBaseService
        svc = KnowledgeBaseService()

        docs = svc.list_documents(tenant_id=None, limit=1000)
        ids = [d["id"] for d in docs]
        assert env["doc_a"] not in ids          # 真实租户文档不可见
        assert env["doc_unowned"] in ids        # 无主文档可见（None 口径 = tenant_id IS NULL）

        assert svc.count_documents(tenant_id=None) >= 1
        assert svc.count_documents(tenant_id=env["tenant_a"]) >= 1

    # ===== API 路由层 =====

    async def test_download_route_cross_tenant_403(self, env):
        """download 路由跨租户且无共享授权 → 403（文件存在也拿不到；现有共享下载
        特性以 403 区分「无权」，语义为拒绝而非泄漏内容）"""
        from fastapi import HTTPException
        from src.knowledge import api as kb_api

        try:
            set_tenant_context(env["tenant_b"], "user_other")
            with pytest.raises(HTTPException) as exc_info:
                await kb_api.download_document(env["doc_a"])
            assert exc_info.value.status_code == 403
        finally:
            clear_tenant_context()

    async def test_download_route_own_tenant_returns_file(self, env):
        """download 路由本租户 → 正常返回 FileResponse"""
        from fastapi.responses import FileResponse
        from src.knowledge import api as kb_api

        try:
            set_tenant_context(env["tenant_a"], "user_a")
            resp = await kb_api.download_document(env["doc_a"])
            assert isinstance(resp, FileResponse)
        finally:
            clear_tenant_context()

    async def test_download_route_none_tenant_403(self, env):
        """download 路由无租户上下文访问真实租户文档 → 403"""
        from fastapi import HTTPException
        from src.knowledge import api as kb_api

        try:
            clear_tenant_context()
            with pytest.raises(HTTPException) as exc_info:
                await kb_api.download_document(env["doc_a"])
            assert exc_info.value.status_code == 403
        finally:
            clear_tenant_context()

    async def test_delete_route_cross_tenant_404_json(self, env):
        """DELETE 路由跨租户 → 404 JSONResponse（与不存在统一，文档保留）"""
        from src.knowledge import api as kb_api

        try:
            set_tenant_context(env["tenant_b"], "user_other")
            resp = await kb_api.delete_document(env["doc_a"])
            assert resp.status_code == 404
        finally:
            clear_tenant_context()
        assert _doc_exists(env["doc_a"]) is True

    async def test_chunks_route_cross_tenant_404(self, env):
        """chunks 路由跨租户 → 404（与不存在统一，不泄漏存在性）"""
        from fastapi import HTTPException
        from src.knowledge import api as kb_api

        try:
            set_tenant_context(env["tenant_b"], "user_other")
            with pytest.raises(HTTPException) as exc_info:
                await kb_api.get_document_chunks(env["doc_a"])
            assert exc_info.value.status_code == 404
        finally:
            clear_tenant_context()

    async def test_list_route_none_tenant_excludes_real_tenant(self, env):
        """list 路由无租户上下文（匿名请求）→ 不返回真实租户文档，仅无主"""
        from src.knowledge import api as kb_api

        try:
            clear_tenant_context()
            resp = await kb_api.list_documents(limit=1000)
            ids = [item.id for item in resp["items"]]
            assert env["doc_a"] not in ids
            assert env["doc_unowned"] in ids
            assert resp["total"] >= 1
        finally:
            clear_tenant_context()
