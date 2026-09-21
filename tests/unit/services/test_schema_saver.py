"""schema_saver 去重放宽测试（2026-09-20 设计 §4.4）

表文档被用户移动到其他栏目后 source_type 被覆盖，判重改为
title = '[数据表] {table_name}' + metadata table_name/source_info，不再限定 source_type。
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.data_analysis.schema_saver import save_schema_to_knowledge


pytestmark = pytest.mark.tools


def _dedup_db_mock(existing_id=None):
    """构造 DB mock：fetchone 返回判重命中行（None = 未命中）；捕获 SQL 与参数"""
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = {"id": existing_id} if existing_id else None
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_cm = MagicMock()
    mock_cm.__enter__ = MagicMock(return_value=mock_conn)
    mock_cm.__exit__ = MagicMock(return_value=None)
    return mock_cm, mock_cursor


def _patch_embedding():
    """mock embedding 客户端（0 token，跳过计费分支）"""
    client = MagicMock()
    client.embed_batch = AsyncMock(return_value=[[0.0] * 8])
    client.last_usage_tokens = 0
    client.model = "text-embedding-v3"
    return patch(
        "src.services.data_analysis.schema_saver.TextEmbeddingV3Client",
        return_value=client,
    )


@pytest.mark.asyncio
async def test_dedup_hits_moved_document_without_source_type():
    """同名同源表被移动（source_type 已覆盖）后再次上传：仍判重跳过，不重复注册"""
    mock_cm, mock_cursor = _dedup_db_mock(existing_id=2341)
    with _patch_embedding(), \
            patch("src.services.data_analysis.schema_saver.get_db_connection",
                  return_value=mock_cm):
        result = await save_schema_to_knowledge(
            tenant_id="tenant_t1",
            table_name="渠道销售",
            description="渠道维度销售",
            columns=[{"name": "amount", "data_type": "number"}],
            source_info="storage/tenants/tenant_t1/knowledge/sales.xlsx",
        )

    assert result["success"] is True
    assert result["doc_id"] == 2341
    assert "已存在" in result["message"]
    # 判重命中直接返回，不触发 INSERT / 向量写入
    insert_calls = [
        c for c in mock_cursor.execute.call_args_list
        if "INSERT INTO documents" in c[0][0]
    ]
    assert not insert_calls

    sql = mock_cursor.execute.call_args_list[0][0][0]
    params = mock_cursor.execute.call_args_list[0][0][1]
    # 判重不再限定 source_type（被移动的表 source_type 已被覆盖）
    assert "source_type" not in sql
    # title 判重值与 INSERT 写入格式一致
    assert params[1] == "[数据表] 渠道销售"
    assert params[2] == "渠道销售"
    assert params[3] == "storage/tenants/tenant_t1/knowledge/sales.xlsx"


@pytest.mark.asyncio
async def test_dedup_miss_proceeds_to_insert():
    """未命中判重：继续走 INSERT 注册流程"""
    mock_cm, mock_cursor = _dedup_db_mock(existing_id=None)
    # fetchone 序列：判重未命中 -> INSERT documents RETURNING -> INSERT chunks RETURNING
    mock_cursor.fetchone.side_effect = [None, {"id": 9001}, {"id": 5001}]
    with _patch_embedding(), \
            patch("src.services.data_analysis.schema_saver.get_db_connection",
                  return_value=mock_cm), \
            patch("src.services.data_analysis.schema_saver.get_vector_db") as vec_mock:
        vec_client = MagicMock()
        vec_client.insert = AsyncMock()
        vec_mock.return_value = vec_client
        result = await save_schema_to_knowledge(
            tenant_id="tenant_t1",
            table_name="新表",
            description="",
            columns=[{"name": "a"}],
            source_info="storage/tenants/tenant_t1/knowledge/new.xlsx",
        )

    assert result["success"] is True
    assert result["doc_id"] == 9001
