"""
迁移脚本写路径 SQL 功能测试（Phase 1，CodeReview P0 回归锚点）

背景：write_metadata 的乐观锁 SQL 曾把方向写反（IS DISTINCT FROM），单测因
整体桩化 write_metadata 而 SQL 零覆盖、dry-run/verify 复验也不触达写路径，
P0 从盲区漏过。本文件用真实库跑乐观锁 UPDATE 的两个方向 + NULL 原值用例。

数据隔离：专用测试租户 tenant_pytest_migrate_meta，fixture 前后双清
（历史残留 + 本次数据）；只写 documents 行，不触碰 chunks/chunks_vec。

生命周期：随迁移脚本同生共死——真机迁移 + 清场 + verify 复查通过后，
本文件与脚本一并删除（见开发计划 Phase 5 收尾清理）。
"""
import importlib.util
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_spec = importlib.util.spec_from_file_location(
    "_migrate_travel_resource_tables_db",
    Path(__file__).resolve().parents[2] / "scripts" / "migrate_travel_resource_tables_to_metadata.py",
)
migr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migr)

TEST_TENANT = "tenant_pytest_migrate_meta"

_DOC_SQL = """
INSERT INTO documents (user_id, tenant_id, title, source_type, file_type, file_path,
                       total_chunks, embedding_model, metadata, summary)
VALUES (NULL, %s, %s, 'hotel_resource', 'xlsx', '', 2, 'text-embedding-v3', %s, 'pytest 临时摘要')
RETURNING id
"""


def _cleanup(conn):
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM chunks WHERE doc_id IN (SELECT id FROM documents WHERE tenant_id = %s)",
            (TEST_TENANT,),
        )
        cur.execute("DELETE FROM documents WHERE tenant_id = %s", (TEST_TENANT,))
    conn.commit()


@pytest.fixture
def db_conn():
    conn = migr._connect()
    _cleanup(conn)  # 清历史残留
    yield conn
    _cleanup(conn)  # 清本次数据
    conn.close()


def _insert_doc(conn, metadata_value):
    """插入临时文档并提交（write_metadata 内部会 commit，文档必须已持久）"""
    with conn.cursor() as cur:
        cur.execute(_DOC_SQL, (TEST_TENANT, "酒店：pytest 临时", metadata_value))
        doc_id = cur.fetchone()["id"]
    conn.commit()
    return doc_id


def _read_metadata(conn, doc_id):
    with conn.cursor() as cur:
        cur.execute("SELECT metadata FROM documents WHERE id = %s", (doc_id,))
        row = cur.fetchone()
    raw = row["metadata"]
    return raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False) if raw else None


class TestWriteMetadataOptimisticLock:
    def test_unchanged_metadata_writes_successfully(self, db_conn):
        # 正常路径：metadata 仍是 scan 原值 → 命中 1 行、写入生效
        original = json.dumps({"region": "贵阳"}, ensure_ascii=False)
        doc_id = _insert_doc(db_conn, original)

        new_meta = {"region": "贵阳", "raw_payload": {"price_table": "P", "region": "贵阳"}}
        assert migr.write_metadata(db_conn, doc_id, new_meta, original) is True

        written = json.loads(_read_metadata(db_conn, doc_id))
        assert written["raw_payload"]["price_table"] == "P"
        assert written["region"] == "贵阳"  # 顶层键原样保留

    def test_changed_metadata_rejected_without_overwrite(self, db_conn):
        # 并发路径：执行期间 metadata 已被外部更新（原值 ≠ scan 时读到的）→
        # 0 行命中、返回 False、库中保持外部更新值不被覆盖
        actual = json.dumps({"region": "外部已改"}, ensure_ascii=False)
        doc_id = _insert_doc(db_conn, actual)
        stale_expected = json.dumps({"region": "scan 时的旧值"}, ensure_ascii=False)

        assert migr.write_metadata(
            db_conn, doc_id, {"region": "scan 时的旧值", "raw_payload": {}}, stale_expected,
        ) is False
        assert json.loads(_read_metadata(db_conn, doc_id))["region"] == "外部已改"

    def test_null_metadata_matches_null_expected(self, db_conn):
        # NULL 原值（IS NOT DISTINCT FROM 的 NULL 安全相等）：双 NULL 视为未变 → 可写
        doc_id = _insert_doc(db_conn, None)

        assert migr.write_metadata(db_conn, doc_id, {"raw_payload": {"price_table": "P"}}, None) is True
        assert json.loads(_read_metadata(db_conn, doc_id))["raw_payload"]["price_table"] == "P"
