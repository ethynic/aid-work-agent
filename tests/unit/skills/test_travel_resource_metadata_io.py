"""
旅游资源 retriever 写读路径 metadata 化单测（Phase 2 D2/D3）

覆盖：
1. import_hotel / import_attraction 新写路径——表文本入 metadata.raw_payload、
   total_chunks=1、不再插 chunks 1/2、业务键镜像；
2. get_price_table / get_ticket_table / get_project_table——优先 raw_payload、
   未迁移存量回退 chunk 位次（过渡期兼容）。

不连真实 DB / Embedding API：FakeConn 记录 SQL/参数，SeqConn 按序返回行。
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = PROJECT_ROOT / 'src' / 'skills' / 'travel-quote' / 'scripts'
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import hotel_retriever  # noqa: E402
import attraction_retriever  # noqa: E402


class FakeConn:
    """模拟 DB 连接上下文：记录所有 execute 调用，按序返回 fetchone 结果"""

    def __init__(self, fetchone_seq=None):
        self._fetchone_seq = list(fetchone_seq or [])
        self._fetchone_idx = 0
        self.calls = []

    def execute(self, sql, args=None):
        self.calls.append((sql, args))

    def fetchone(self):
        if self._fetchone_idx < len(self._fetchone_seq):
            r = self._fetchone_seq[self._fetchone_idx]
            self._fetchone_idx += 1
            return r
        return None

    def fetchall(self):
        return []

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class SeqConn:
    """按 FIFO 返回 fetchone 行（读路径专用：每次 execute 后 pop 一个预设行）"""

    def __init__(self, rows):
        self._rows = list(rows)
        self.calls = []

    def execute(self, sql, args=None):
        self.calls.append((sql, args))

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _mock_embedding(retriever):
    """mock 向量化链路：_get_embedding_client 返回零用量 mock，_embed 返回定长向量"""
    from unittest.mock import MagicMock
    client = MagicMock()
    client.reset_usage.return_value = None
    client.last_usage_tokens = 0
    return patch.object(retriever, '_get_embedding_client', return_value=client), \
        patch.object(retriever, '_embed', return_value=[0.1] * 8)


# ============================================================
# import_hotel 新写路径
# ============================================================

class TestImportHotelMetadata:
    def test_table_text_into_raw_payload_and_single_chunk(self):
        r = hotel_retriever.HotelRetriever()
        conn = FakeConn(fetchone_seq=[{"id": 77}, {"id": 901}])
        patches = _mock_embedding(r)
        with patches[0], patches[1], patch.object(r, '_get_conn', return_value=conn):
            doc_id = r.import_hotel(
                tenant_id="tenant_1", hotel_name="花溪酒店", region="贵阳",
                info_text="酒店名称：花溪酒店\n4钻", price_table_text="房型A | 580",
                metadata={"sub_region": "花溪", "diamond_level": "4钻"},
                source_file="hotels.xlsx", user_id="u1",
            )

        assert doc_id == 77
        insert_doc_sql, insert_doc_args = conn.calls[0]
        # total_chunks=1（仅 chunk 0 摘要，表数据在 metadata）
        assert ", 1, 'text-embedding-v3'," in insert_doc_sql
        meta = json.loads(insert_doc_args[5])
        assert meta["sub_region"] == "花溪"                    # 顶层业务键保留
        assert meta["region"] == "贵阳"                         # region 补入顶层（设计 §3）
        assert meta["raw_payload"]["price_table"] == "房型A | 580"
        assert meta["raw_payload"]["region"] == "贵阳"          # 业务键镜像
        assert meta["raw_payload"]["sub_region"] == "花溪"

        # 仅 1 个 chunk INSERT（index 0 为 SQL 字面量），不再插 chunk 1
        chunk_inserts = [(sql, args) for sql, args in conn.calls
                         if "INSERT INTO chunks" in sql and "chunks_vec" not in sql]
        assert len(chunk_inserts) == 1
        assert "VALUES (%s, 0," in chunk_inserts[0][0]
        # chunk 0 的 metadata 保留 region 提示（现状不变）
        assert json.loads(chunk_inserts[0][1][3])["region"] == "贵阳"
        # 向量化 chunk 0 仍在
        assert any("INSERT INTO chunks_vec" in sql for sql, _ in conn.calls)

    def test_empty_price_table_still_writes_key(self):
        # 与旧契约一致：表行存在但内容为空 → 键在值为空串（区别于「无表」）
        r = hotel_retriever.HotelRetriever()
        conn = FakeConn(fetchone_seq=[{"id": 78}, {"id": 902}])
        patches = _mock_embedding(r)
        with patches[0], patches[1], patch.object(r, '_get_conn', return_value=conn):
            r.import_hotel("tenant_1", "某酒店", "贵阳", "摘要", "")

        meta = json.loads(conn.calls[0][1][5])
        assert meta["raw_payload"]["price_table"] == ""

    def test_embedding_billing_recorded_on_import(self):
        # 计费回归：导入向量化消耗独立落库（billing_audit §3.6 工具入口统一计费）
        from unittest.mock import MagicMock as _MM
        r = hotel_retriever.HotelRetriever()
        conn = FakeConn(fetchone_seq=[{"id": 79}, {"id": 903}])
        client = _MM()
        client.last_usage_tokens = 100  # 非 0 → 触发计费分支
        with patch.object(r, '_get_embedding_client', return_value=client), \
             patch.object(r, '_embed', return_value=[0.1] * 8), \
             patch.object(r, '_get_conn', return_value=conn), \
             patch("src.services.session_record.record_admin_embedding_usage") as billing:
            r.import_hotel("tenant_1", "某酒店", "贵阳", "摘要", "P", user_id="u1")

        billing.assert_called_once()
        assert billing.call_args.args[0] is client  # client 为位置参数
        kwargs = billing.call_args.kwargs
        assert kwargs["tenant_id"] == "tenant_1"
        assert kwargs["user_id"] == "u1"
        assert kwargs["source_label"] == "travel_quote_import_hotel"


# ============================================================
# import_attraction 新写路径（图片镜像同步见 test_attraction_retriever_async）
# ============================================================

class TestImportAttractionMetadata:
    @pytest.mark.asyncio
    async def test_tables_into_raw_payload_and_single_chunk(self):
        r = attraction_retriever.AttractionRetriever()
        conn = FakeConn(fetchone_seq=[{"id": 88}, {"id": 1001}])
        patches = _mock_embedding(r)
        with patches[0], patches[1], patch.object(r, '_get_conn', return_value=conn):
            doc_id = await r.import_attraction(
                tenant_id="tenant_1", attraction_name="黄果树", region="安顺",
                info_text="景点摘要", ticket_table_text="门票表", project_table_text="项目表",
                metadata={"category": "natural", "category_cn": "自然风光"},
                source_file="attractions.xlsx",
            )

        assert doc_id == 88
        insert_doc_sql, insert_doc_args = conn.calls[0]
        assert ", 1, 'text-embedding-v3'," in insert_doc_sql  # total_chunks=1
        meta = json.loads(insert_doc_args[5])
        assert meta["category"] == "natural"
        assert meta["raw_payload"]["ticket_table"] == "门票表"
        assert meta["raw_payload"]["project_table"] == "项目表"
        assert meta["raw_payload"]["category_cn"] == "自然风光"
        assert meta["raw_payload"]["region"] == "安顺"   # region 参数确定性补入顶层并镜像

        chunk_inserts = [(sql, args) for sql, args in conn.calls
                         if "INSERT INTO chunks" in sql and "chunks_vec" not in sql]
        assert len(chunk_inserts) == 1
        assert "VALUES (%s, 0," in chunk_inserts[0][0]


# ============================================================
# 读路径：优先 raw_payload，回退 chunk
# ============================================================

class TestGetPriceTable:
    def _run(self, rows):
        r = hotel_retriever.HotelRetriever()
        conn = SeqConn(rows)
        with patch.object(r, '_get_conn', return_value=conn):
            return r.get_price_table(1), conn

    def test_payload_hit_skips_chunk_query(self):
        text, conn = self._run([{"metadata": '{"raw_payload": {"price_table": "payload表"}}'}])
        assert text == "payload表"
        assert len(conn.calls) == 1  # 未发起 chunk 回退查询

    def test_empty_payload_string_still_wins(self):
        text, conn = self._run([{"metadata": '{"raw_payload": {"price_table": ""}}'}])
        assert text == ""
        assert len(conn.calls) == 1

    def test_fallback_to_chunk_when_payload_absent(self):
        # 未迁移存量：raw_payload 无键 → 回退 chunk 1
        text, conn = self._run([{"metadata": "{}"}, {"text": "chunk旧表"}])
        assert text == "chunk旧表"
        assert "chunk_index" in conn.calls[1][0]

    def test_no_metadata_row_falls_back(self):
        text, _ = self._run([None, {"text": "chunk表"}])
        assert text == "chunk表"

    def test_nothing_anywhere_returns_none(self):
        text, _ = self._run([None, None])
        assert text is None


class TestGetAttractionTables:
    def _run(self, rows, method, doc_id=1):
        r = attraction_retriever.AttractionRetriever()
        conn = SeqConn(rows)
        with patch.object(r, '_get_conn', return_value=conn):
            return getattr(r, method)(doc_id), conn

    def test_ticket_from_payload(self):
        text, conn = self._run(
            [{"metadata": '{"raw_payload": {"ticket_table": "门票", "project_table": "项目"}}'}],
            "get_ticket_table")
        assert text == "门票"
        assert len(conn.calls) == 1

    def test_project_falls_back_to_chunk2(self):
        # raw_payload 只有 ticket（部分迁移），project 回退 chunk 2
        text, conn = self._run(
            [{"metadata": '{"raw_payload": {"ticket_table": "门票"}}'}, {"text": "chunk项目表"}],
            "get_project_table")
        assert text == "chunk项目表"
        assert "%s" in conn.calls[1][0] and conn.calls[1][1] == (1, 2)

    def test_ticket_falls_back_to_chunk1(self):
        text, conn = self._run([{"metadata": None}, {"text": "chunk门票"}], "get_ticket_table")
        assert text == "chunk门票"
        assert conn.calls[1][1] == (1, 1)
