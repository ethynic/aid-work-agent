"""
酒店知识库搜索工具集成测试

连真实 DB（.env 的 DATABASE_URL），验证 hotel_search 对真实酒店数据的检索。
按 testing.md 分层属于集成测试：真实组件（真实 PG + 真实酒店数据），
仅在向量路径 stub `_embed`（外边界，避免依赖 DashScope 网络调用）。

运行：venv/Scripts/python.exe -m pytest tests/integration/test_hotel_search_tool.py -v
"""

from unittest.mock import patch

import pytest

# 先导入 src.db.database 触发应用完整引导：master_agent 在导入期构造并注册工具，
# 若直接先 import hotel_search_tool 会形成循环（工具 → src.db → master_agent 构造 → 再 import 工具）。
import src.db.database  # noqa: F401
from src.tools.knowledge.hotel_search_tool import HotelSearchTool
from src.tools.context import ToolExecutionContext, tool_execution_scope
from src.db.database import get_db_connection

pytestmark = pytest.mark.integration


# ============================================================
# 动态发现有 hotel_resource 数据的租户 + 一条真实酒店名（避免硬编码）
# 库无数据或 DB 不可达 → 返回 None，测试整体 skip
# ============================================================

def _discover_hotel_fixture():
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT d.tenant_id, c.text
                FROM documents d
                JOIN chunks c ON c.doc_id = d.id
                WHERE d.source_type = 'hotel_resource' AND c.chunk_index = 0
                ORDER BY d.id
                LIMIT 1
            """)
            row = cur.fetchone()
    except Exception:
        return None
    if not row:
        return None
    hotel_name = ""
    for line in (row["text"] or "").split("\n"):
        if "酒店名称" in line and "：" in line:
            hotel_name = line.split("：", 1)[-1].strip()
            break
    return {"tenant_id": row["tenant_id"], "hotel_name": hotel_name}


_FIXTURE = _discover_hotel_fixture()
_skip_no_data = pytest.mark.skipif(
    _FIXTURE is None or not _FIXTURE.get("hotel_name"),
    reason="库中无 hotel_resource 数据或 DB 不可达",
)
TENANT_ID = _FIXTURE["tenant_id"] if _FIXTURE else None
HOTEL_NAME = _FIXTURE["hotel_name"] if _FIXTURE else None


# ============================================================
# 1. 工具元信息 / 错误分支（不依赖 DB）
# ============================================================

class TestHotelSearchDefinition:
    def test_name_and_source_type(self):
        t = HotelSearchTool()
        assert t.name == "hotel_search"
        assert t.SOURCE_TYPE == "hotel_resource"

    def test_schema(self):
        assert "input_schema" in HotelSearchTool().to_tool_definition()

    def test_display_name_with_query(self):
        assert "亚朵" in HotelSearchTool().get_display_name({"query": "亚朵"})

    @pytest.mark.asyncio
    async def test_empty_query_returns_error(self):
        r = await HotelSearchTool().execute(query="")
        assert r["success"] is False and r["count"] == 0

    @pytest.mark.asyncio
    async def test_missing_tenant_returns_error(self):
        """无 tenant_id → 拒绝检索，防止跨租户泄露"""
        r = HotelSearchTool().execute  # 未 set_tenant_id，且测试无请求上下文
        # ContextVar 在测试进程里通常未设置 → 解析为 None → 报错
        result = await r(query="亚朵")
        assert result["success"] is False
        assert "租户" in result["error"]


# ============================================================
# 2. 真实库检索（核心：mock 拿不到的就是这部分）
# ============================================================

@_skip_no_data
class TestHotelSearchRealDB:
    """连真实库，验证名称命中 + 价格明细表关联"""

    @pytest.mark.asyncio
    async def test_name_match_finds_real_hotel_with_price_table(self):
        """按真实酒店名检索 → 命中，且 price_table 非空。

        price_table（chunk_index=1）是通用 knowledge_base_search 拿不到的核心数据，
        本测试正是验证这一能力在真实库上成立。
        """
        tool = HotelSearchTool()
        # 用酒店名前半段做模糊检索，验证 ILIKE 命中
        q = HOTEL_NAME[:4] if len(HOTEL_NAME) > 4 else HOTEL_NAME
        with tool_execution_scope(ToolExecutionContext(tenant_id=TENANT_ID)):
            result = await tool.execute(query=q, top_k=10)

        assert result["success"] is True
        assert result["count"] >= 1
        titles = [r["title"] for r in result["results"]]
        assert any(HOTEL_NAME in t for t in titles), f"{HOTEL_NAME} 未命中: {titles}"

        hit = next(r for r in result["results"] if HOTEL_NAME in r["title"])
        assert hit["score"] is None, "名称命中不应有相似度分"
        assert hit["price_table"], "price_table 不应为空"
        assert "|" in hit["price_table"], "price_table 应为 markdown 表"

        # 租户隔离校验：命中文档必须属于当前租户（防止跨租户泄露）
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT tenant_id FROM documents WHERE id = %s", (hit["doc_id"],))
            assert cur.fetchone()["tenant_id"] == TENANT_ID

    @pytest.mark.asyncio
    async def test_price_table_associated_by_doc_id(self):
        """多条结果时每条都带 price_table 字段（按 doc_id 关联，缺失为空串）"""
        tool = HotelSearchTool()
        with tool_execution_scope(ToolExecutionContext(tenant_id=TENANT_ID)):
            result = await tool.execute(query="酒店", top_k=5)

        assert result["success"] is True
        for r in result["results"]:
            assert "price_table" in r
            assert isinstance(r["price_table"], str)
            assert "doc_id" in r and "score" in r and "info" in r

    @pytest.mark.asyncio
    async def test_vector_fallback_executes_real_sql(self):
        """名称无命中 → 向量兜底：_embed stub 为常量向量（外边界），
        验证真实向量 SQL 执行 + 返回结构 + score 非 None。

        注：假常量向量排序无意义，本用例只覆盖向量分支 SQL 可执行 + 结构正确。
        """
        tool = HotelSearchTool()
        with (
            patch.object(tool, "_embed", return_value=[0.1] * 1024),
            tool_execution_scope(ToolExecutionContext(tenant_id=TENANT_ID)),
        ):
            result = await tool.execute(query="zzzqqqxx非酒店随机词", top_k=5)

        assert result["success"] is True
        if result["results"]:
            score = result["results"][0]["score"]
            assert score is not None, "向量路径 score 应非空"
            # 余弦相似度 = 1 - cosine_distance，范围 [-1, 1]
            assert -1 <= score <= 1
