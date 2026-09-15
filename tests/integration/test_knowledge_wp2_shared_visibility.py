"""WP2 独立验证：共享范围 + 软删除的 OR 分支泄漏复核（真实 PG，无 mock）

开发者新增测试只覆盖单租户 deleted；本文件构造「共享文档 deleted + 本租户文档
deleted」双 OR 分支场景，验证 range_sql 整体加括号后 deleted 不经任何分支泄漏，
并验证括号修正的附带行为变化：sub_categories 现在对整个 OR（含共享分支）生效。
"""
import json
import uuid
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.integration

from src.db.database import get_db_connection
from src.saas.db.tenant_db import TenantDB

FTS_TOKEN = "wp2sharedleaktoken"
DIM = 1024


@pytest.fixture
def env():
    owner = TenantDB.create(company_name=f"WP2共享源-{uuid.uuid4().hex[:6]}",
                            tenant_code=f"TO{uuid.uuid4().hex[:6].upper()}",
                            contact_name="t", contact_phone="13800000000")
    consumer = TenantDB.create(company_name=f"WP2共享消费-{uuid.uuid4().hex[:6]}",
                               tenant_code=f"TC{uuid.uuid4().hex[:6].upper()}",
                               contact_name="t", contact_phone="13800000000")
    if not owner or not consumer:
        pytest.skip("DB 不可用")
    oid, cid = owner["tenant_id"], consumer["tenant_id"]

    def _doc(tid, title, source_type, status="active", sub_category=None):
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO documents (tenant_id, title, source_type, sub_category,
                    file_type, file_size, total_chunks, embedding_model, origin, status)
                VALUES (%s, %s, %s, %s, 'txt', 24, 1, 'text-embedding-v3', 'manual_upload', %s)
                RETURNING id
            """, (tid, title, source_type, sub_category, status))
            doc_id = cur.fetchone()["id"]
            cur.execute(
                "INSERT INTO chunks (doc_id, chunk_index, text, tokens, metadata) "
                "VALUES (%s, 0, %s, 5, '{}') RETURNING id",
                (doc_id, f"{title} {FTS_TOKEN}"))
            chunk_id = cur.fetchone()["id"]
            cur.execute("INSERT INTO chunks_vec (chunk_id, embedding) VALUES (%s, %s)",
                        (chunk_id, "[" + ",".join(["1.0"] + ["0.0"] * (DIM - 1)) + "]"))
            conn.commit()
        return doc_id, chunk_id

    data = {
        "owner": oid, "consumer": cid,
        "owner_active": _doc(oid, "共享激活", "policy", sub_category="subA"),
        "owner_deleted": _doc(oid, "共享已删", "policy", status="deleted"),
        "own_active": _doc(cid, "本租户激活", "file"),
        "own_deleted": _doc(cid, "本租户已删", "file", status="deleted"),
    }
    yield data

    with get_db_connection() as conn:
        cur = conn.cursor()
        for tid in (oid, cid):
            cur.execute("DELETE FROM chunks_vec WHERE chunk_id IN "
                        "(SELECT c.id FROM chunks c JOIN documents d ON c.doc_id = d.id "
                        "WHERE d.tenant_id = %s)", (tid,))
            cur.execute("DELETE FROM chunks WHERE doc_id IN "
                        "(SELECT id FROM documents WHERE tenant_id = %s)", (tid,))
            cur.execute("DELETE FROM documents WHERE tenant_id = %s", (tid,))
        conn.commit()
    TenantDB.delete(oid)
    TenantDB.delete(cid)
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM tenants WHERE tenant_id IN (%s, %s)", (oid, cid))
        conn.commit()


def _fts(env, **kw):
    from src.knowledge.retriever.hybrid_retriever import HybridRetriever
    r = HybridRetriever(vector_db=MagicMock(), embedding_client=MagicMock(), conn=None)
    return [cid for cid, _ in r._postgres_fts_search(FTS_TOKEN, 50, **kw)]


class TestSharedDeletedNoLeak:
    def test_fts_deleted_invisible_across_or_branches(self, env):
        """共享 deleted + 本租户 deleted 均不经 OR 分支泄漏；双侧 active 阳性对照"""
        ids = _fts(env, tenant_id=env["consumer"],
                   shared_ranges=[(env["owner"], "policy")])
        assert env["own_active"][1] in ids
        assert env["owner_active"][1] in ids
        assert env["own_deleted"][1] not in ids
        assert env["owner_deleted"][1] not in ids

    @pytest.mark.asyncio
    async def test_vector_deleted_invisible_across_or_branches(self, env):
        """向量检索同样的双 OR 分支场景"""
        # 根 conftest 已把 src.knowledge.vector_db.vector_db 替换为 stub，按文件位置加载真实模块
        import importlib.util
        from pathlib import Path
        fp = Path(__file__).resolve().parents[2] / "src" / "knowledge" / "vector_db" / "vector_db.py"
        spec = importlib.util.spec_from_file_location("_real_vdb_wp2_shared", str(fp))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        vdb = mod.VectorDBPostgreSQL(dimension=DIM, conn=None)
        results = await vdb.search(
            [1.0] + [0.0] * (DIM - 1), top_k=50,
            tenant_id=env["consumer"], shared_ranges=[(env["owner"], "policy")])
        ids = [cid for cid, _ in results]
        assert env["own_active"][1] in ids
        assert env["owner_active"][1] in ids
        assert env["own_deleted"][1] not in ids
        assert env["owner_deleted"][1] not in ids

    def test_sub_categories_now_constrain_shared_branch(self, env):
        """括号修正的附带行为变化：sub_categories 对整个 OR 生效（含共享分支）。
        共享文档 sub_category=subA，用 subB 过滤后共享文档不再返回。"""
        base = _fts(env, tenant_id=env["consumer"],
                    shared_ranges=[(env["owner"], "policy")])
        assert env["owner_active"][1] in base  # 对照：不过滤时可见
        filtered = _fts(env, tenant_id=env["consumer"],
                        shared_ranges=[(env["owner"], "policy")],
                        sub_categories=["subB"])
        assert env["owner_active"][1] not in filtered
