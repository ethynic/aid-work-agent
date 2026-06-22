"""
HotelRetriever.search_by_name 单元测试

验证方案B：名称检索同时匹配 chunk0 文本（info_text）和 document 标题，
避免 info_text 漏写酒店名称行时按酒店名搜不到（标题兜底）。

不连真实 DB：mock `_get_conn` 返回记录 SQL/参数的 FakeConn。
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = PROJECT_ROOT / 'src' / 'skills' / 'travel-quote' / 'scripts'
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

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
