# -*- coding: utf-8 -*-
"""PUT /kb/hotels|attractions/{doc_id} 表字段 metadata 化端点单测（Phase 2 D2）

覆盖（独立测试/CR 指出的端点级缺口）：
1. 表字段（price_table / ticket_table / project_table）写入 metadata.raw_payload
   + 业务键镜像同步
2. 过渡期双写：同一事务内同时 UPDATE chunks 1/2（未迁移文档防线）
3. body.metadata 的 raw_payload/images 注入被剥离（LLM 可见通道保护）
4. 未迁移文档仅 PUT 顶层 metadata → 镜像-only payload

测试策略：直接 await 调用 update_hotel_kb / update_attraction_kb，
mock _get_tenant_id / get_db_connection（与 test_patch_attraction_images.py 同模式）。
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from src.api import travel_quote as tq


def _mock_db_connection(metadata_dict, captured):
    """构造 mock get_db_connection：fetchone 返回带 metadata 的行，记录全部 SQL"""
    metadata_str = json.dumps(metadata_dict, ensure_ascii=False) if metadata_dict else None

    def _make_conn():
        cursor = MagicMock()
        cursor.fetchone.return_value = {"id": 1, "metadata": metadata_str}

        def _execute(sql, params=None):
            captured.append((sql, params))

        cursor.execute.side_effect = _execute
        conn = MagicMock()
        conn.cursor.return_value = cursor
        conn.commit = MagicMock()
        return conn

    return _make_conn


class _FakeCtx:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self._conn

    def __exit__(self, *a):
        return False


async def _run_put(handler, doc_id, body, existing_meta):
    captured = []
    make_conn = _mock_db_connection(existing_meta, captured)
    with patch.object(tq, "_get_tenant_id", return_value="t1"), \
         patch.object(tq, "get_db_connection", side_effect=lambda: _FakeCtx(make_conn())):
        result = await handler(doc_id=doc_id, request=MagicMock(), body=body)
    return result, captured


def _metadata_update(captured):
    for sql, params in captured:
        if "UPDATE documents" in sql and "metadata = %s" in sql:
            return json.loads(params[-2]) if params and len(params) >= 2 else None
    return None


class TestUpdateHotelKb:
    @pytest.mark.asyncio
    async def test_price_table_into_raw_payload_with_dual_write(self):
        """表字段写 raw_payload + 同事务双写 chunk 1（迁移 verify 不变量）"""
        existing = {"region": "贵阳", "diamond_level": "4钻",
                    "raw_payload": {"price_table": "旧表", "region": "贵阳"}}
        result, captured = await _run_put(
            tq.update_hotel_kb, 5,
            {"price_table": "新表", "title": "酒店：A"},
            existing,
        )

        assert result["success"] is True
        merged = _metadata_update(captured)
        assert merged["raw_payload"]["price_table"] == "新表"
        assert merged["raw_payload"]["region"] == "贵阳"       # 镜像保留
        assert merged["diamond_level"] == "4钻"                # 顶层不动
        # 过渡期双写：chunk 1 同步 UPDATE
        assert any("chunk_index = 1" in sql for sql, _ in captured)

    @pytest.mark.asyncio
    async def test_body_raw_payload_injection_stripped(self):
        """body.metadata.raw_payload 注入被剥离，存量表数据不被伪造覆盖"""
        existing = {"region": "贵阳", "raw_payload": {"price_table": "真实表", "region": "贵阳"}}
        _, captured = await _run_put(
            tq.update_hotel_kb, 5,
            {"metadata": {"raw_payload": {"price_table": "FAKE", "injection": "x"}}},
            existing,
        )
        merged = _metadata_update(captured)
        assert merged["raw_payload"]["price_table"] == "真实表"
        assert "injection" not in merged["raw_payload"]

    @pytest.mark.asyncio
    async def test_unmigrated_doc_metadata_only_put(self):
        """未迁移文档仅改顶层键 → 镜像-only payload（无表键，迁移 verify 会提示）"""
        _, captured = await _run_put(
            tq.update_hotel_kb, 5, {"metadata": {"diamond_level": "5钻"}}, {"region": "贵阳"},
        )
        merged = _metadata_update(captured)
        assert merged["diamond_level"] == "5钻"
        assert merged["raw_payload"]["diamond_level"] == "5钻"
        assert "price_table" not in merged["raw_payload"]


class TestUpdateAttractionKb:
    @pytest.mark.asyncio
    async def test_both_tables_into_raw_payload_with_dual_write(self):
        existing = {"category": "natural",
                    "raw_payload": {"ticket_table": "旧门票", "project_table": "旧项目", "category": "natural"}}
        result, captured = await _run_put(
            tq.update_attraction_kb, 9,
            {"ticket_table": "新门票", "project_table": "新项目"},
            existing,
        )

        assert result["success"] is True
        merged = _metadata_update(captured)
        assert merged["raw_payload"]["ticket_table"] == "新门票"
        assert merged["raw_payload"]["project_table"] == "新项目"
        assert merged["raw_payload"]["category"] == "natural"
        # 双写：chunk 1 与 chunk 2 都同步 UPDATE
        assert any("chunk_index = 1" in sql for sql, _ in captured)
        assert any("chunk_index = 2" in sql for sql, _ in captured)

    @pytest.mark.asyncio
    async def test_metadata_only_merge_mirrors_business_keys(self):
        """仅 PUT 顶层 metadata：合并后镜像同步，不触发 chunk 双写"""
        existing = {"region": "黔南", "category_cn": "自然风光",
                    "raw_payload": {"region": "黔南", "category_cn": "自然风光"}}
        _, captured = await _run_put(
            tq.update_attraction_kb, 9, {"metadata": {"category_cn": "地质公园"}}, existing,
        )
        merged = _metadata_update(captured)
        assert merged["category_cn"] == "地质公园"
        assert merged["raw_payload"]["category_cn"] == "地质公园"  # 镜像同步
        assert not any("chunk_index = 1" in sql or "chunk_index = 2" in sql
                       for sql, _ in captured)  # 无表字段 → 无双写
