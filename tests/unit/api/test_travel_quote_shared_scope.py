# -*- coding: utf-8 -*-
"""
travel_quote 独立 API 跨租户共享检索范围 单元测试

验证 _resolve_travel_shared_tenant_ids 聚合该租户所有子智能体的已启用共享来源租户：
- 枚举 subagent_knowledge_sources 下全部子智能体
- 逐个走 load_shared_ranges（内部已 ∩ 租户级授权 + 按分类过滤）
- 聚合去重，返回含本租户的租户 ID 列表
"""
from unittest.mock import MagicMock, patch

import pytest

from src.api.travel_quote import _resolve_travel_shared_tenant_ids


class _FakeCursor:
    def __init__(self, subagent_names):
        self._names = subagent_names

    def execute(self, sql, args=None):
        pass

    def fetchall(self):
        return [{"subagent_name": n} for n in self._names]


class _FakeConn:
    def __init__(self, subagent_names):
        self._cursor = _FakeCursor(subagent_names)

    def cursor(self):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestResolveTravelSharedTenantIds:
    def test_no_subagent_configs_returns_own_tenant(self):
        """该租户未配置任何共享 → 只返回本租户"""
        with patch("src.api.travel_quote.get_db_connection",
                   return_value=_FakeConn([])) as m_conn:
            result = _resolve_travel_shared_tenant_ids("tenant_B", "hotel_resource")

        assert result == ["tenant_B"]
        m_conn.assert_called_once()

    def test_aggregates_shared_owners_across_subagents(self):
        """多个子智能体启用共享 → 聚合去重共享来源租户（含本租户）"""
        conn = _FakeConn(["travel-consultant", "itinerary-planner"])
        with patch("src.api.travel_quote.get_db_connection", return_value=conn), \
             patch("src.knowledge.retriever.tenant_range.load_shared_ranges",
                   side_effect=[
                       [("tenant_A", "hotel_resource")],
                       [("tenant_A", "hotel_resource"), ("tenant_C", "hotel_resource")],
                   ]) as m_load:
            result = _resolve_travel_shared_tenant_ids("tenant_B", "hotel_resource")

        assert result == ["tenant_B", "tenant_A", "tenant_C"]
        # 每个子智能体都按 source_type 调用一次 load_shared_ranges
        assert m_load.call_count == 2
        assert m_load.call_args_list[0].args == ("tenant_B", "travel-consultant", "hotel_resource")
        assert m_load.call_args_list[1].args == ("tenant_B", "itinerary-planner", "hotel_resource")

    def test_forwards_source_type_to_load_shared_ranges(self):
        """source_type 透传给 load_shared_ranges（由它内部按分类过滤），结果直接采用"""
        conn = _FakeConn(["travel-consultant"])
        with patch("src.api.travel_quote.get_db_connection", return_value=conn), \
             patch("src.knowledge.retriever.tenant_range.load_shared_ranges",
                   return_value=[]) as m_load:
            result = _resolve_travel_shared_tenant_ids("tenant_B", "hotel_resource")

        m_load.assert_called_once_with("tenant_B", "travel-consultant", "hotel_resource")
        assert result == ["tenant_B"]

    def test_db_error_falls_back_to_own_tenant(self):
        """DB 异常 → 不阻断，退回只搜本租户"""
        conn = _FakeConn([])
        with patch("src.api.travel_quote.get_db_connection",
                   side_effect=Exception("db down")) as m_conn:
            result = _resolve_travel_shared_tenant_ids("tenant_B", "hotel_resource")

        assert result == ["tenant_B"]
