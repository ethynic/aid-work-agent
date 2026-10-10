"""
专用搜索工具取表 raw_payload 回退正例（Phase 2 部署窗口保护，设计 §5 风险 #7）

Phase 2 起新导入文档不插 chunks 1/2，表文本只在 documents.metadata.raw_payload；
存量工具在 chunk 查询缺行时回退读 metadata。验证回退真实填充（非仅「不崩」）。
工具 Phase 4 下线后本文件随之删除。

不连真实 DB / Embedding API：mock get_db_connection 按 fetchall 序列返回，
mock _embed / ImageRegistry。
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tools.context import ToolExecutionContext, tool_execution_scope
from src.tools.knowledge.attraction_search_tool import AttractionSearchTool
from src.tools.knowledge.hotel_search_tool import HotelSearchTool

pytestmark = pytest.mark.tools

TENANT_ID = "tenant_test_001"


def _make_conn(fetchall_seq):
    cursor = MagicMock()
    cursor.fetchall.side_effect = list(fetchall_seq)
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    return conn


class TestHotelSearchFallback:
    @pytest.mark.asyncio
    async def test_price_table_filled_from_raw_payload_when_chunk_missing(self):
        """新导入文档（无 chunk 1）：chunk 查询空 → 回退 metadata.raw_payload 填充价格表"""
        import json
        # 查询序列：① 名称 ILIKE（空）→ ② 向量搜索 → ③ chunk 1 取表（空，新导入）→ ④ documents 回退
        vector_rows = [{
            "distance": 0.1, "doc_id": 300, "text": "酒店名称：新酒店\n4钻",
            "title": "酒店：新酒店", "metadata": {}, "file_path": "x.xlsx",
        }]
        payload_rows = [{
            "id": 300,
            "metadata": json.dumps({"region": "贵阳", "raw_payload": {"price_table": "回退价格表", "region": "贵阳"}}, ensure_ascii=False),
        }]
        conn = _make_conn([[], vector_rows, [], payload_rows])

        with patch("src.tools.knowledge.hotel_search_tool.get_db_connection", return_value=conn), \
             patch.object(HotelSearchTool, "_embed", return_value=[0.1] * 8), \
             tool_execution_scope(ToolExecutionContext(tenant_id=TENANT_ID)):
            result = await HotelSearchTool().execute(query="新酒店", top_k=5)

        assert result["success"] is True
        assert result["count"] == 1
        assert result["results"][0]["price_table"] == "回退价格表"

    @pytest.mark.asyncio
    async def test_chunk_hit_takes_precedence_over_payload(self):
        """存量文档（chunk 1 有行）：chunk 文本优先，不读 metadata 回退"""
        import json
        vector_rows = [{
            "distance": 0.1, "doc_id": 301, "text": "酒店名称：老酒店",
            "title": "酒店：老酒店", "metadata": {}, "file_path": "x.xlsx",
        }]
        chunk_rows = [{"doc_id": 301, "text": "chunk 权威表"}]
        conn = _make_conn([[], vector_rows, chunk_rows])

        with patch("src.tools.knowledge.hotel_search_tool.get_db_connection", return_value=conn), \
             patch.object(HotelSearchTool, "_embed", return_value=[0.1] * 8), \
             tool_execution_scope(ToolExecutionContext(tenant_id=TENANT_ID)):
            result = await HotelSearchTool().execute(query="老酒店", top_k=5)

        assert result["success"] is True
        assert result["results"][0]["price_table"] == "chunk 权威表"
        # chunk 全命中 → 不发起 documents 回退查询（仅 3 次 execute：名称/向量/取表）
        assert conn.cursor.return_value.execute.call_count == 3


class TestAttractionSearchFallback:
    @pytest.mark.asyncio
    async def test_project_table_filled_from_raw_payload_when_chunk_missing(self):
        """新导入景点（无 chunk 2）：回退 metadata.raw_payload 填充项目表"""
        import json
        # 查询序列：① 向量搜索 → ② chunk 2 取项目表（空）→ ③ documents 回退
        vector_rows = [{
            "distance": 0.2, "doc_id": 400, "text": "新景点摘要",
            "title": "景点：新景点", "metadata": {}, "file_path": "x.xlsx",
        }]
        payload_rows = [{
            "id": 400,
            "metadata": json.dumps({"raw_payload": {"ticket_table": "T", "project_table": "回退项目表"}}, ensure_ascii=False),
        }]
        conn = _make_conn([vector_rows, [], payload_rows])

        with patch("src.tools.knowledge.attraction_search_tool.get_db_connection", return_value=conn), \
             patch("src.tools.knowledge.attraction_search_tool.get_image_registry"), \
             patch.object(AttractionSearchTool, "_embed", return_value=[0.1] * 8), \
             tool_execution_scope(ToolExecutionContext(tenant_id=TENANT_ID)):
            result = await AttractionSearchTool().execute(query="新景点", top_k=5)

        assert result["success"] is True
        assert result["results"][0]["project_table"] == "回退项目表"
