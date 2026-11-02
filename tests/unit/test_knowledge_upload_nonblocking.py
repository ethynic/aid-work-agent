"""
知识库上传落库不得阻塞事件循环

事故背景（诺度 2026-11-02）：1.5MB Excel 解析出 12194 个 chunk，
VectorDBPostgreSQL.insert 在事件循环内同步执行数百 MB 的 psycopg2
批量写入，阻塞超过 gunicorn WORKER_TIMEOUT=120s，worker 被 SIGKILL，
nginx 返回 502/404，前端提示「服务器响应异常」。

回归点：向量写库与文档落库必须通过 asyncio.to_thread 在工作线程执行。
"""

import importlib.util
import threading
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

from src.knowledge.parsers import ParsedChunk, ParseResult


def _load_real_vector_db():
    """按文件位置加载真实 vector_db 模块（根 conftest 已把
    src.knowledge.vector_db.vector_db 替换为 stub，无法常规导入）"""
    file_path = Path(__file__).resolve().parents[2] / "src" / "knowledge" / "vector_db" / "vector_db.py"
    spec = importlib.util.spec_from_file_location("_real_vector_db_for_nonblocking_test", str(file_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_db_mocks(chunk_count: int):
    cursor = MagicMock()
    cursor.fetchone.side_effect = [{"id": 100}] + [{"id": 200 + i} for i in range(chunk_count)]
    cursor.fetchall.return_value = []
    conn = MagicMock()
    conn.cursor.return_value = cursor
    cm = MagicMock()
    cm.__enter__.return_value = conn
    cm.__exit__.return_value = False
    return cm, conn


class TestVectorInsertRunsOffLoop:
    """VectorDBPostgreSQL.insert 内部写入在线程池执行"""

    @pytest.mark.asyncio
    async def test_insert_executes_in_worker_thread(self):
        VectorDBPostgreSQL = _load_real_vector_db().VectorDBPostgreSQL

        # 跳过 __init__ 的建表动作，只验证线程调度
        vdb = VectorDBPostgreSQL.__new__(VectorDBPostgreSQL)
        vdb.dimension = 4
        vdb._external_conn = False
        vdb._pool_conn = None

        seen = {}

        def fake_insert_sync(chunk_ids, embeddings):
            seen["thread"] = threading.get_ident()

        vdb.insert_sync = fake_insert_sync

        await vdb.insert([1], [[0.0] * 4])

        assert seen["thread"] != threading.get_ident()

    @pytest.mark.asyncio
    async def test_insert_empty_skips_thread(self):
        VectorDBPostgreSQL = _load_real_vector_db().VectorDBPostgreSQL

        vdb = VectorDBPostgreSQL.__new__(VectorDBPostgreSQL)
        called = False

        def fake_insert_sync(chunk_ids, embeddings):
            nonlocal called
            called = True

        vdb.insert_sync = fake_insert_sync
        await vdb.insert([], [])
        assert called is False


class TestUploadPersistRunsOffLoop:
    """upload_document 的落库（文档 + chunks + 向量）在线程池执行"""

    @pytest.mark.asyncio
    async def test_persist_executes_in_worker_thread(self, tmp_path):
        from src.knowledge.service import KnowledgeBaseService

        file_path = tmp_path / "大表格.xlsx"
        file_path.write_bytes(b"fake")

        fake_parser = MagicMock()
        fake_parser.parse = AsyncMock(return_value=ParseResult(text="正文", metadata={}))

        cm, _conn = _make_db_mocks(2)
        seen = {}

        def fake_persist(self, **kwargs):
            seen["thread"] = threading.get_ident()
            return 100

        with patch("src.knowledge.service.parser_factory.get_parser", return_value=fake_parser), \
             patch("src.config.settings.get_embedding_api_key", return_value="sk-test"), \
             patch("src.knowledge.service.TextEmbeddingV3Client") as mock_emb, \
             patch("src.knowledge.service.get_vector_db"), \
             patch("src.knowledge.service.ChatRecordDB.create"), \
             patch.object(KnowledgeBaseService, "_validate_sub_category"), \
             patch.object(KnowledgeBaseService, "_get_db_connection", return_value=cm), \
             patch.object(KnowledgeBaseService, "_record_knowledge_embedding_billing"), \
             patch.object(KnowledgeBaseService, "_persist_document_sync", fake_persist), \
             patch.object(KnowledgeBaseService, "generate_summary", new=AsyncMock(return_value="")):
            mock_emb.return_value.embed_batch = AsyncMock(return_value=[[0.0] * 4, [0.1] * 4])
            mock_emb.return_value.reset_usage = MagicMock()
            mock_emb.return_value.last_usage_tokens = 10

            service = KnowledgeBaseService()
            result = await service.upload_document(
                file_path=str(file_path), file_filename="大表格.xlsx",
                tenant_id="t1", source_type="file")

        assert result["success"] is True
        assert seen["thread"] != threading.get_ident()


class TestChunkBatchInsert:
    """chunks 批量写入：单次 execute_values + 按 uuid 对齐 id（RETURNING 顺序不可依赖）"""

    @pytest.mark.asyncio
    async def test_single_batch_and_ids_mapped_by_uuid(self, tmp_path):
        from src.knowledge.service import KnowledgeBaseService

        file_path = tmp_path / "批量.xlsx"
        file_path.write_bytes(b"fake")

        precomputed = [
            ParsedChunk(text=f"行{i}", metadata={"chunk_type": "excel_row", "row_number": i + 2})
            for i in range(3)
        ]
        fake_parser = MagicMock()
        fake_parser.parse = AsyncMock(return_value=ParseResult(
            text="原始文本", metadata={}, precomputed_chunks=precomputed))

        cm, _conn = _make_db_mocks(3)
        captured = {"calls": 0, "rows": [], "ids": None}

        def fake_execute_values(cur, sql, rows, page_size=100, fetch=False):
            captured["calls"] += 1
            captured["rows"] = list(rows)
            # 故意乱序返回（RETURNING 顺序不作保证），验证按 uuid 回查
            return [
                {"id": 300 + i, "uuid": row[5]}
                for i, row in reversed(list(enumerate(rows)))
            ]

        with patch("src.knowledge.service.parser_factory.get_parser", return_value=fake_parser), \
             patch("src.config.settings.get_embedding_api_key", return_value="sk-test"), \
             patch("src.knowledge.service.TextEmbeddingV3Client") as mock_emb, \
             patch("src.knowledge.service.get_vector_db") as mock_vdb, \
             patch("src.knowledge.service.pg_extras.execute_values", side_effect=fake_execute_values), \
             patch("src.knowledge.service.ChatRecordDB.create"), \
             patch.object(KnowledgeBaseService, "_validate_sub_category"), \
             patch.object(KnowledgeBaseService, "_get_db_connection", return_value=cm), \
             patch.object(KnowledgeBaseService, "_record_knowledge_embedding_billing"), \
             patch.object(KnowledgeBaseService, "generate_summary", new=AsyncMock(return_value="")):
            mock_emb.return_value.embed_batch = AsyncMock(return_value=[[0.0] * 4] * 3)
            mock_emb.return_value.reset_usage = MagicMock()
            mock_emb.return_value.last_usage_tokens = 10
            mock_vdb.return_value.insert_sync = MagicMock(
                side_effect=lambda ids, embs: captured.__setitem__("ids", list(ids)))

            service = KnowledgeBaseService()
            result = await service.upload_document(
                file_path=str(file_path), file_filename="批量.xlsx",
                tenant_id="t1", source_type="file")

        assert result["success"] is True
        assert captured["calls"] == 1  # 一次批量语句，不再逐条 INSERT
        assert len(captured["rows"]) == 3
        # 乱序 RETURNING 下仍按输入顺序对齐 chunk id
        assert captured["ids"] == [300, 301, 302]
