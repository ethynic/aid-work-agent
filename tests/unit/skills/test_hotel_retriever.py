"""
HotelRetriever.search_by_name 单元测试

验证方案B：名称检索同时匹配 chunk0 文本（info_text）和 document 标题，
避免 info_text 漏写酒店名称行时按酒店名搜不到（标题兜底）。

不连真实 DB：mock `_get_conn` 返回记录 SQL/参数的 FakeConn。
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = PROJECT_ROOT / 'src' / 'skills' / 'travel-quote' / 'scripts'
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import hotel_retriever  # noqa: E402


class FakeConn:
    """模拟 DB 连接上下文：记录最后一次 execute 的 SQL/参数，返回预设行"""

    def __init__(self, rows=None):
        self.last_sql = None
        self.last_args = None
        self._rows = rows or []

    def execute(self, sql, args=None):
        self.last_sql = sql
        self.last_args = args
        return None

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestSearchByNameTitleFallback:
    """search_by_name 必须同时匹配 c.text 和 d.title（标题兜底）"""

    def test_sql_matches_both_text_and_title(self):
        """SQL 应含 (c.text ILIKE %s OR d.title ILIKE %s)，且传入两个相同的 pattern"""
        retriever = hotel_retriever.HotelRetriever()
        fake = FakeConn()
        with patch.object(retriever, '_get_conn', return_value=fake):
            retriever.search_by_name("tenant_1", "天合盛景", top_k=5)

        sql = fake.last_sql.upper()
        assert "C.TEXT ILIKE" in sql
        assert "D.TITLE ILIKE" in sql
        assert " OR " in sql
        # 参数：source_type, tenant_id, text_pattern, title_pattern, top_k
        pattern = "%天合盛景%"
        assert fake.last_args.count(pattern) == 2

    def test_title_only_match_is_returned(self):
        """info_text 不含名称、但 title 含名称时，靠 title 兜底能命中"""
        retriever = hotel_retriever.HotelRetriever()
        fake = FakeConn(rows=[{
            "doc_id": 973,
            "text": "所在区域：雷山县",  # info_text 不含"天合盛景"
            "title": "酒店：西江天合盛景民宿（观景台店）",
            "metadata": {},
            "file_path": "",
            "created_at": None,
        }])
        with patch.object(retriever, '_get_conn', return_value=fake):
            results = retriever.search_by_name("tenant_1", "天合盛景")

        assert len(results) == 1
        assert results[0]["doc_id"] == 973
        assert results[0]["title"] == "酒店：西江天合盛景民宿（观景台店）"

    def test_result_metadata_string_is_parsed(self):
        """metadata 为 JSON 字符串时应被解析为 dict（结果格式化逻辑）"""
        retriever = hotel_retriever.HotelRetriever()
        fake = FakeConn(rows=[{
            "doc_id": 1,
            "text": "酒店名称：A酒店",
            "title": "酒店：A酒店",
            "metadata": '{"sub_region": "云岩区"}',
            "file_path": "",
            "created_at": None,
        }])
        with patch.object(retriever, '_get_conn', return_value=fake):
            results = retriever.search_by_name("t1", "A酒店")

        assert results[0]["metadata"] == {"sub_region": "云岩区"}


class TestSearchByNameSharedRange:
    """跨租户知识库共享：search_by_name 传 subagent_id 时，租户范围须含共享源租户。

    复现修复前 bug：检索只按本租户 d.tenant_id 过滤，租户 B 接入租户 A 知识库后
    读不到 A 的酒店文档（返回 0 条）。
    """

    def test_with_subagent_uses_any_with_shared_tenants(self):
        """传 subagent_id 时用 d.tenant_id = ANY(%s)，参数含本租户 + 共享源租户"""
        retriever = hotel_retriever.HotelRetriever()
        fake = FakeConn()
        with patch.object(retriever, '_get_conn', return_value=fake), \
             patch('src.knowledge.retriever.tenant_range.load_shared_ranges',
                   return_value=[('tenant_A', 'hotel_resource')]):
            retriever.search_by_name("tenant_B", "天合盛景", top_k=5,
                                     subagent_id="travel-consultant")

        sql = fake.last_sql
        assert "d.tenant_id = ANY(%s)" in sql
        # 参数：source_type, [本租户, 共享源], text_pattern, title_pattern, top_k
        assert fake.last_args[0] == "hotel_resource"
        assert fake.last_args[1] == ["tenant_B", "tenant_A"]
        assert fake.last_args.count("%天合盛景%") == 2
        assert fake.last_args[-1] == 5

    def test_shared_ranges_filtered_by_source_type(self):
        """load_shared_ranges 以本检索器 source_type 为参数调用，由它内部按分类过滤"""
        retriever = hotel_retriever.HotelRetriever()
        fake = FakeConn()
        with patch.object(retriever, '_get_conn', return_value=fake), \
             patch('src.knowledge.retriever.tenant_range.load_shared_ranges',
                   return_value=[('tenant_A', 'hotel_resource')]) as m_load:
            retriever.search_by_name("tenant_B", "天合盛景", subagent_id="travel-consultant")

        m_load.assert_called_once_with("tenant_B", "travel-consultant", "hotel_resource")
        # load_shared_ranges 返回的即过滤后共享源，全部加入租户范围
        assert fake.last_args[1] == ["tenant_B", "tenant_A"]

    def test_without_subagent_keeps_own_tenant_only(self):
        """不传 subagent_id（主智能体直接调用）时保持原行为：只搜本租户 d.tenant_id = %s"""
        retriever = hotel_retriever.HotelRetriever()
        fake = FakeConn()
        with patch.object(retriever, '_get_conn', return_value=fake):
            retriever.search_by_name("tenant_B", "天合盛景", top_k=5)

        sql = fake.last_sql
        assert "d.tenant_id = %s" in sql
        assert "ANY(" not in sql
        # 参数：source_type, tenant_id, text_pattern, title_pattern, top_k
        assert fake.last_args[0] == "hotel_resource"
        assert fake.last_args[1] == "tenant_B"


class TestSharedTenantIdsOverride:
    """独立 API（无子智能体上下文）场景：调用方传已聚合好的 shared_tenant_ids 列表，
    检索直接使用该列表（含本租户），不再走 load_shared_ranges。"""

    def test_search_by_name_uses_aggregated_list(self):
        retriever = hotel_retriever.HotelRetriever()
        fake = FakeConn()
        with patch.object(retriever, '_get_conn', return_value=fake):
            retriever.search_by_name("tenant_B", "天合盛景", top_k=5,
                                     shared_tenant_ids=["tenant_B", "tenant_A"])

        sql = fake.last_sql
        assert "d.tenant_id = ANY(%s)" in sql
        # 参数：source_type, [本租户, 共享源...], text_pattern, title_pattern, top_k
        assert fake.last_args[0] == "hotel_resource"
        assert fake.last_args[1] == ["tenant_B", "tenant_A"]

    def test_search_forwards_to_both_paths(self):
        """组合 search 把 shared_tenant_ids 透传给 name/vector 两个子方法"""
        retriever = hotel_retriever.HotelRetriever()
        fake = FakeConn()
        client = MagicMock()
        client.embed_sync = MagicMock(return_value=[0.1] * 128)
        client.reset_usage = MagicMock()
        client.last_usage_tokens = 0
        with patch.object(retriever, '_get_conn', return_value=fake), \
             patch.object(retriever, '_get_embedding_client', return_value=client):
            retriever.search("tenant_B", "天合盛景", top_k=5,
                             shared_tenant_ids=["tenant_B", "tenant_A"])

        # 名称无结果 → 落入向量路径，参数为 (embedding, source_type, tenant_ids, top_k)
        assert "d.tenant_id = ANY(%s)" in fake.last_sql
        assert ["tenant_B", "tenant_A"] in fake.last_args

    def test_list_all_uses_aggregated_list(self):
        retriever = hotel_retriever.HotelRetriever()
        fake = FakeConn(rows=[{
            "cnt": 2,
            "doc_id": 1,
            "title": "酒店：A酒店",
            "file_path": "",
            "metadata": {},
            "created_at": None,
            "info": "",
        }])
        with patch.object(retriever, '_get_conn', return_value=fake):
            retriever.list_all("tenant_B", limit=10, offset=0,
                               shared_tenant_ids=["tenant_B", "tenant_A"])

        assert "d.tenant_id = ANY(%s)" in fake.last_sql
        assert fake.last_args[1] == ["tenant_B", "tenant_A"]
