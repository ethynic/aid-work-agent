"""
Excel 行级分块（表头注入 + 格式前置判定）测试
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

from src.knowledge.parsers.excel_parser import (
    ExcelParser,
    _detect_layout,
    _header_score,
    _split_long_text,
)
from src.knowledge.parsers import ParsedChunk, ParseResult


def _make_xlsx(tmp_path, sheets: dict, name="test.xlsx"):
    """sheets: {sheet_name: [[cell, ...], ...]}"""
    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)
    for sheet_name, rows in sheets.items():
        ws = wb.create_sheet(title=sheet_name)
        for row in rows:
            ws.append(row)
    file_path = tmp_path / name
    wb.save(file_path)
    return str(file_path)


class TestDetectLayout:
    """格式判定（§3.3 六条规则）"""

    def test_standard_table_with_header(self):
        rows = [
            ["SKU编号", "商品名称", "固定价格（元）"],
            ["YD0001", "速干T恤", "39.9"],
            ["YD0002", "跑步鞋", "129.0"],
        ]
        verdict = _detect_layout(rows)
        assert verdict["layout"] == "table"
        assert verdict["has_header"] is True
        assert verdict["rule"] == 5

    def test_headerless_table_first_row_is_data(self):
        rows = [
            ["红色", "S码"],
            ["蓝色", "M码"],
            ["红色", "S码"],
        ]
        verdict = _detect_layout(rows)
        assert verdict["layout"] == "table"
        assert verdict["has_header"] is False
        assert verdict["rule"] == 4

    def test_two_rows_are_freeform(self):
        """2 行（表头 + 1 数据 / 两行散文）无法区分，一律 freeform"""
        assert _detect_layout([["SKU", "名称"], ["YD0001", "速干T恤"]])["rule"] == 1
        assert _detect_layout([["说明文字第一行"], ["说明文字第二行"]])["rule"] == 1

    def test_single_row_is_freeform(self):
        verdict = _detect_layout([["只有一行说明文字"]])
        assert verdict["layout"] == "freeform"
        assert verdict["rule"] == 1

    def test_title_first_row_is_freeform(self):
        rows = [
            ["商品清单2024（内部资料）"],
            ["SKU编号", "商品名称"],
            ["YD0001", "速干T恤"],
        ]
        verdict = _detect_layout(rows)
        assert verdict["layout"] == "freeform"
        assert verdict["rule"] == 3

    def test_irregular_columns_are_freeform(self):
        rows = [
            ["SKU", "名称", "价格"],
            ["YD0001", "速干T恤", "39.9"],
            ["备注：本表数据截至 2026 年 8 月，包含 A/B/C 三个仓库的库存与价格明细，请勿外传"],
        ]
        verdict = _detect_layout(rows)
        assert verdict["layout"] == "freeform"
        assert verdict["rule"] == 2

    def test_single_column_content_is_freeform(self):
        """单列内容（列表/散文）不是二维表"""
        rows = [
            ["第一条要点"],
            ["第二条要点"],
            ["第三条要点"],
        ]
        assert _detect_layout(rows)["rule"] == 2

    def test_numeric_first_row_table_without_header(self):
        rows = [
            ["1001", "2002", "3003"],
            ["1002", "2003", "3004"],
            ["1003", "2004", "3005"],
        ]
        verdict = _detect_layout(rows)
        assert verdict["layout"] == "table"
        assert verdict["has_header"] is False
        assert verdict["rule"] == 6

    def test_duplicate_header_cells_fall_to_rule6(self):
        rows = [
            ["名称", "名称", "名称"],
            ["a", "b", "c"],
            ["d", "e", "f"],
        ]
        verdict = _detect_layout(rows)
        assert verdict["rule"] == 6
        assert verdict["has_header"] is False

    def test_header_score_digit_heavy_cell_fails(self):
        # 金额/编码类单元格（数字占比高）不应被视作表头单元格
        assert _header_score(["39.9", "YD0001-001"]) < 0.6
        assert _header_score(["商品名称", "固定价格（元）"]) >= 0.6


class TestSplitLongText:
    def test_short_text_single_segment(self):
        assert _split_long_text("短文本") == ["短文本"]

    def test_long_text_split_with_overlap(self):
        text = "x" * 15000
        segments = _split_long_text(text)
        assert len(segments) >= 3
        assert all(len(s) <= 6000 for s in segments)
        # 重叠：分段拼接可还原原文
        rebuilt = segments[0] + "".join(s[64:] for s in segments[1:])
        assert rebuilt == text


class TestExcelParserRowChunking:
    """行级分块：一行数据 = 一个 chunk，表头键值注入"""

    @pytest.mark.asyncio
    async def test_table_sheet_one_chunk_per_row(self, tmp_path):
        file_path = _make_xlsx(tmp_path, {
            "SKU": [
                ["SKU编号", "商品名称", "固定价格（元）"],
                ["YD0001", "速干T恤", "39.9"],
                ["YD0002", "跑步鞋", "129.0"],
            ]
        })
        result = await ExcelParser().parse(file_path)

        assert result.precomputed_chunks is not None
        chunks = result.precomputed_chunks
        assert len(chunks) == 2
        # 表头键值注入
        assert "SKU编号: YD0001" in chunks[0].text
        assert "商品名称: 速干T恤" in chunks[0].text
        assert "固定价格（元）: 39.9" in chunks[0].text
        # metadata 带实际行号（第 1 行是表头，数据从第 2 行起）
        assert chunks[0].metadata["row_number"] == 2
        assert chunks[1].metadata["row_number"] == 3
        assert chunks[0].metadata["chunk_type"] == "excel_row"
        # 单 sheet 无工作表前缀
        assert "[工作表:" not in chunks[0].text
        # 判定报告
        assert result.metadata["layout_report"][0]["sheet"] == "SKU"
        assert result.metadata["layout_report"][0]["rule"] == 5

    @pytest.mark.asyncio
    async def test_multi_sheet_prefix_and_freeform_mixed(self, tmp_path):
        file_path = _make_xlsx(tmp_path, {
            "商品表": [
                ["SKU编号", "商品名称"],
                ["YD0001", "速干T恤"],
                ["YD0002", "跑步鞋"],
            ],
            "说明": [
                ["本工作表为补充说明，只有一段文字描述，"],
                ["不存在二维表结构，应回退文本分块。"],
            ],
        })
        result = await ExcelParser().parse(file_path)

        chunks = result.precomputed_chunks
        assert chunks is not None
        types = {c.metadata["chunk_type"] for c in chunks}
        assert types == {"excel_row", "excel_freeform"}
        # 多 sheet 时行级 chunk 带 sheet 前缀
        row_chunks = [c for c in chunks if c.metadata["chunk_type"] == "excel_row"]
        assert all(c.text.startswith("[工作表: 商品表] ") for c in row_chunks)

    @pytest.mark.asyncio
    async def test_all_freeform_file_uses_old_path(self, tmp_path):
        file_path = _make_xlsx(tmp_path, {
            "说明A": [["第一行只有一段说明文字"], ["第二行也是说明文字"]],
        })
        result = await ExcelParser().parse(file_path)
        assert result.precomputed_chunks is None

    @pytest.mark.asyncio
    async def test_empty_sheet_in_mixed_file_no_garbage_chunk(self, tmp_path):
        """混合文件中的空 sheet 不产生只有标题行的垃圾 chunk"""
        file_path = _make_xlsx(tmp_path, {
            "商品表": [
                ["SKU编号", "商品名称"],
                ["YD0001", "速干T恤"],
                ["YD0002", "跑步鞋"],
            ],
            "空白表": [],
        })
        result = await ExcelParser().parse(file_path)
        chunks = result.precomputed_chunks
        assert chunks is not None
        assert all(c.text.strip() != "## 工作表: 空白表" for c in chunks)
        assert all(c.metadata.get("chunk_type") != "excel_freeform" for c in chunks)

    @pytest.mark.asyncio
    async def test_headerless_table_no_injection(self, tmp_path):
        file_path = _make_xlsx(tmp_path, {
            "数据": [
                ["红色", "S码"],
                ["蓝色", "M码"],
                ["红色", "S码"],
            ]
        })
        result = await ExcelParser().parse(file_path)
        chunks = result.precomputed_chunks
        assert len(chunks) == 3
        # 首行也作为数据行输出，不做键值注入
        assert chunks[0].text == "红色 | S码"
        assert chunks[0].metadata["row_number"] == 1

    @pytest.mark.asyncio
    async def test_empty_cell_keeps_field_name(self, tmp_path):
        file_path = _make_xlsx(tmp_path, {
            "SKU": [
                ["SKU编号", "颜色", "价格"],
                ["YD0001", None, "39.9"],
                ["YD0002", "黑色", "59.9"],
            ]
        })
        result = await ExcelParser().parse(file_path)
        assert "颜色: " in result.precomputed_chunks[0].text

    @pytest.mark.asyncio
    async def test_long_row_split_into_segments(self, tmp_path):
        long_desc = "速干透气面料，不粘身不变形" * 600  # > 6000 字符
        file_path = _make_xlsx(tmp_path, {
            "SKU": [
                ["SKU编号", "卖点描述"],
                ["YD0001", long_desc],
                ["YD0002", "普通短描述"],
            ]
        })
        result = await ExcelParser().parse(file_path)
        chunks = result.precomputed_chunks
        # 超长行切成多个分段，普通行不分段
        assert len(chunks) >= 3
        assert chunks[0].metadata["segment"] == 0
        assert chunks[0].metadata["row_number"] == chunks[1].metadata["row_number"] == 2
        assert "segment" not in chunks[-1].metadata
        assert all(len(c.text) <= 6000 for c in chunks)

    @pytest.mark.asyncio
    async def test_text_field_keeps_legacy_format(self, tmp_path):
        """ParseResult.text 保持老格式（供摘要 / raw_text），不受旁路影响"""
        file_path = _make_xlsx(tmp_path, {
            "SKU": [
                ["SKU编号", "商品名称"],
                ["YD0001", "速干T恤"],
            ]
        })
        result = await ExcelParser().parse(file_path)
        assert "## 工作表: SKU" in result.text
        assert "YD0001 | 速干T恤" in result.text


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


class TestUploadDocumentPrecomputedBypass:
    """service.upload_document 结构化分块旁路"""

    @pytest.mark.asyncio
    async def test_precomputed_chunks_bypass_text_chunker(self, tmp_path):
        from src.knowledge.service import KnowledgeBaseService

        file_path = tmp_path / "商品表.xlsx"
        file_path.write_bytes(b"fake")

        precomputed = [
            ParsedChunk(text="SKU编号: YD0001 | 商品名称: 速干T恤",
                        metadata={"chunk_type": "excel_row", "sheet_name": "SKU", "row_number": 2}),
            ParsedChunk(text="SKU编号: YD0002 | 商品名称: 跑步鞋",
                        metadata={"chunk_type": "excel_row", "sheet_name": "SKU", "row_number": 3}),
        ]
        fake_parser = MagicMock()
        fake_parser.parse = AsyncMock(return_value=ParseResult(
            text="原始文本", metadata={}, precomputed_chunks=precomputed))

        cm, conn = _make_db_mocks(len(precomputed))

        with patch("src.knowledge.service.parser_factory.get_parser", return_value=fake_parser), \
             patch("src.config.settings.get_embedding_api_key", return_value="sk-test"), \
             patch("src.knowledge.service.TextEmbeddingV3Client") as mock_emb, \
             patch("src.knowledge.service.get_vector_db") as mock_vdb, \
             patch("src.knowledge.service.ChatRecordDB.create"), \
             patch.object(KnowledgeBaseService, "_validate_sub_category"), \
             patch.object(KnowledgeBaseService, "_get_db_connection", return_value=cm), \
             patch.object(KnowledgeBaseService, "generate_summary", new=AsyncMock(return_value="")):
            mock_emb.return_value.embed_batch = AsyncMock(return_value=[[0.0] * 4, [0.0] * 4])
            mock_emb.return_value.reset_usage = MagicMock()
            mock_emb.return_value.last_usage_tokens = 10
            mock_vdb.return_value.insert = AsyncMock()

            service = KnowledgeBaseService()
            result = await service.upload_document(
                file_path=str(file_path), file_filename="商品表.xlsx",
                tenant_id="t1", source_type="file")

        assert result["success"] is True
        assert result["total_chunks"] == 2
        cursor = conn.cursor.return_value
        # 找到 chunks INSERT 语句的调用，校验文本前缀与 metadata 合并
        insert_calls = [c for c in cursor.execute.call_args_list
                        if "INSERT INTO chunks" in c.args[0]]
        assert len(insert_calls) == 2
        first_text = insert_calls[0].args[1][2]
        first_meta = json.loads(insert_calls[0].args[1][4])
        assert first_text.startswith("文档标题：商品表.xlsx\n")
        assert "SKU编号: YD0001" in first_text
        assert first_meta["chunk_type"] == "excel_row"
        assert first_meta["row_number"] == 2
        assert "char_count" in first_meta

    @pytest.mark.asyncio
    async def test_legacy_path_metadata_unchanged(self, tmp_path):
        """precomputed_chunks=None 时走老路径，chunk metadata 保持原样（仅 char_count）"""
        from src.knowledge.service import KnowledgeBaseService

        file_path = tmp_path / "普通文档.txt"
        file_path.write_bytes(b"fake")

        fake_parser = MagicMock()
        fake_parser.parse = AsyncMock(return_value=ParseResult(
            text="第一段。\n\n第二段。", metadata={}))

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
                file_path=str(file_path), file_filename="普通文档.txt",
                tenant_id="t1", source_type="file")

        assert result["success"] is True
        cursor = conn.cursor.return_value
        insert_calls = [c for c in cursor.execute.call_args_list
                        if "INSERT INTO chunks" in c.args[0]]
        assert len(insert_calls) == 1
        meta = json.loads(insert_calls[0].args[1][4])
        assert meta == {"char_count": len(insert_calls[0].args[1][2])}
        # 老路径文件名前缀仍在首个 chunk（TextChunker 会把换行归一为空格）
        assert insert_calls[0].args[1][2].startswith("文档标题：普通文档.txt ")
