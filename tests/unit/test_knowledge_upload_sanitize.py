"""
知识库上传孤立代理字符清洗回归测试

背景：PDF ToUnicode 缺陷可产生孤立代理字符（如 \ud83c），写 documents/chunks
表时 psycopg2 UTF-8 编码报 UnicodeEncodeError。upload_document 必须在解析后
源头清洗（复用 src/core/text_sanitizer.sanitize_text）。
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

from src.knowledge.parsers import ParsedChunk, ParseResult


def _make_db_mocks(chunk_count: int):
    """构造 upload_document 依赖的数据库 mock，fetchone 依次返回 doc_id + chunk_id"""
    cursor = MagicMock()
    ids = [{"id": 100}] + [{"id": 200 + i} for i in range(chunk_count)]
    cursor.fetchone.side_effect = ids
    cursor.fetchall.return_value = []
    conn = MagicMock()
    conn.cursor.return_value = cursor
    cm = MagicMock()
    cm.__enter__.return_value = conn
    cm.__exit__.return_value = False
    return cm, conn


def _assert_utf8_encodable(text: str):
    """孤立代理字符无法 UTF-8 编码，正是线上事故的直接报错点"""
    text.encode("utf-8")
    assert not any("\ud800" <= ch <= "\udfff" for ch in text)


class TestUploadDocumentSanitizeSurrogates:
    """upload_document 写库前清洗孤立代理字符"""

    @pytest.mark.asyncio
    async def test_legacy_path_raw_text_and_chunks_sanitized(self, tmp_path):
        from src.knowledge.service import KnowledgeBaseService

        file_path = tmp_path / "故障文档.pdf"
        file_path.write_bytes(b"fake")

        dirty_text = "正常开头\ud83c脏字符结尾" * 3
        fake_parser = MagicMock()
        fake_parser.parse = AsyncMock(return_value=ParseResult(text=dirty_text, metadata={}))

        cm, conn = _make_db_mocks(1)

        with patch("src.knowledge.service.parser_factory.get_parser", return_value=fake_parser), \
             patch("src.config.settings.get_embedding_api_key", return_value="sk-test"), \
             patch("src.knowledge.service.TextEmbeddingV3Client") as mock_emb, \
             patch("src.knowledge.service.get_vector_db") as mock_vdb, \
             patch("src.knowledge.service.ChatRecordDB.create"), \
             patch.object(KnowledgeBaseService, "_validate_sub_category"), \
             patch.object(KnowledgeBaseService, "_get_db_connection", return_value=cm), \
             patch.object(KnowledgeBaseService, "generate_summary", new=AsyncMock(return_value="")):
            mock_emb.return_value.embed_batch = AsyncMock(return_value=[[0.0] * 4])
            mock_emb.return_value.reset_usage = MagicMock()
            mock_emb.return_value.last_usage_tokens = 5
            mock_vdb.return_value.insert = AsyncMock()

            service = KnowledgeBaseService()
            result = await service.upload_document(
                file_path=str(file_path), file_filename="故障文档.pdf",
                tenant_id="t1", source_type="file")

        assert result["success"] is True
        cursor = conn.cursor.return_value
        # documents 表的 raw_text（线上事故报错点）可 UTF-8 编码
        doc_insert = [c for c in cursor.execute.call_args_list
                      if "INSERT INTO documents" in c.args[0]][0]
        raw_text = doc_insert.args[1][10]
        _assert_utf8_encodable(raw_text)
        assert "脏字符结尾" in raw_text
        # chunks 文本同样清洗
        chunk_insert = [c for c in cursor.execute.call_args_list
                        if "INSERT INTO chunks" in c.args[0]][0]
        _assert_utf8_encodable(chunk_insert.args[1][2])
        # 传给 embedding 的文本也已清洗
        emb_texts = mock_emb.return_value.embed_batch.await_args.args[0]
        for t in emb_texts:
            _assert_utf8_encodable(t)

    @pytest.mark.asyncio
    async def test_precomputed_chunks_sanitized(self, tmp_path):
        from src.knowledge.service import KnowledgeBaseService

        file_path = tmp_path / "结构化表.xlsx"
        file_path.write_bytes(b"fake")

        precomputed = [
            ParsedChunk(text="字段A: 值\ud83c一",
                        metadata={"chunk_type": "excel_row", "sheet_name": "S1", "row_number": 2}),
        ]
        fake_parser = MagicMock()
        fake_parser.parse = AsyncMock(return_value=ParseResult(
            text=None, metadata={}, precomputed_chunks=precomputed))

        cm, conn = _make_db_mocks(1)

        with patch("src.knowledge.service.parser_factory.get_parser", return_value=fake_parser), \
             patch("src.config.settings.get_embedding_api_key", return_value="sk-test"), \
             patch("src.knowledge.service.TextEmbeddingV3Client") as mock_emb, \
             patch("src.knowledge.service.get_vector_db") as mock_vdb, \
             patch("src.knowledge.service.ChatRecordDB.create"), \
             patch.object(KnowledgeBaseService, "_validate_sub_category"), \
             patch.object(KnowledgeBaseService, "_get_db_connection", return_value=cm), \
             patch.object(KnowledgeBaseService, "generate_summary", new=AsyncMock(return_value="")):
            mock_emb.return_value.embed_batch = AsyncMock(return_value=[[0.0] * 4])
            mock_emb.return_value.reset_usage = MagicMock()
            mock_emb.return_value.last_usage_tokens = 5
            mock_vdb.return_value.insert = AsyncMock()

            service = KnowledgeBaseService()
            result = await service.upload_document(
                file_path=str(file_path), file_filename="结构化表.xlsx",
                tenant_id="t1", source_type="file")

        assert result["success"] is True
        cursor = conn.cursor.return_value
        chunk_insert = [c for c in cursor.execute.call_args_list
                        if "INSERT INTO chunks" in c.args[0]][0]
        first_text = chunk_insert.args[1][2]
        _assert_utf8_encodable(first_text)
        assert "字段A: 值一" in first_text
