"""
知识库检索工具单元测试

验证 execute() 返回结构：results 为片段级（text/doc_id/score/chunk_index/metadata），
documents 按 doc_id 归并文档级信息（title/file_path/summary/doc_metadata 等，
同一文档命中多片段只输出一次）。
"""
import json
from unittest.mock import MagicMock, AsyncMock, patch

from src.tools.knowledge.knowledge_base_tool import KnowledgeBaseTool


def _make_tool(tenant_id="tenant_test1"):
    """构造一个 mock 好 retriever 的工具实例"""
    tool = KnowledgeBaseTool()
    tool._tenant_id = tenant_id
    return tool


def _mock_db(rows):
    """构造返回给定 rows 的 DB 连接 mock"""
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = rows
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_cm = MagicMock()
    mock_cm.__enter__ = MagicMock(return_value=mock_conn)
    mock_cm.__exit__ = MagicMock(return_value=None)
    return mock_cm, mock_cursor


async def test_kb_search_returns_doc_id_and_file_path():
    """execute 返回结构应包含 doc_id 和 file_path，SQL 应查询 file_path 列"""
    tool = _make_tool()
    mock_retriever = MagicMock()
    mock_retriever.retrieve = AsyncMock(return_value=[
        {"doc_id": 1, "text": "报价模板内容", "score": 0.9, "metadata": {"k": "v"}},
    ])
    tool._retriever = mock_retriever

    mock_cm, mock_cursor = _mock_db([
        {"id": 1, "title": "报价模板.xlsx", "file_path": "storage/tenants/tenant_test1/knowledge/kb_xxx.xlsx"},
    ])

    with patch("src.tools.knowledge.knowledge_base_tool.get_db_connection", return_value=mock_cm):
        result = await tool.execute(query="报价模板", top_k=5)

    assert result["success"] is True
    assert result["count"] == 1

    item = result["results"][0]
    assert item["doc_id"] == 1
    assert item["text"] == "报价模板内容"

    doc = result["documents"]["1"]
    assert doc["title"] == "报价模板.xlsx"
    assert doc["file_path"] == "storage/tenants/tenant_test1/knowledge/kb_xxx.xlsx"

    # 关键：SQL 必须查询了 file_path 列
    sql_called = mock_cursor.execute.call_args[0][0]
    assert "file_path" in sql_called


async def test_kb_search_returns_chunk_position_and_doc_metadata():
    """结果应附带片段位置（chunk_index 1-based / total_chunks）与文档级元数据，
    供 LLM 判断片段是否被切断并获取原始信息"""
    from datetime import datetime

    tool = _make_tool()
    mock_retriever = MagicMock()
    mock_retriever.retrieve = AsyncMock(return_value=[
        {"doc_id": 1, "chunk_index": 2, "text": "片段", "score": 0.9,
         "metadata": {"sheet_name": "Sheet1"}},
    ])
    tool._retriever = mock_retriever

    mock_cm, mock_cursor = _mock_db([
        {
            "id": 1,
            "title": "数据表.xlsx",
            "file_path": "/x.xlsx",
            "source_type": "数据分析",
            "file_type": "xlsx",
            "total_chunks": 12,
            "metadata": json.dumps({
                "sheet_count": 3, "layout_report": [{"sheet": "A"}],
                "raw_payload": {"rows": 3},
            }),
            "summary": "三张工作表的汇总数据",
            "created_at": datetime(2026, 9, 21, 10, 0, 0),
        },
    ])

    with patch("src.tools.knowledge.knowledge_base_tool.get_db_connection", return_value=mock_cm):
        result = await tool.execute(query="数据表", top_k=5)

    item = result["results"][0]
    # chunk_index 由 0-based 转为 1-based
    assert item["chunk_index"] == 3

    doc = result["documents"]["1"]
    assert doc["total_chunks"] == 12
    assert doc["source_type"] == "数据分析"
    assert doc["file_type"] == "xlsx"
    assert doc["created_at"] == "2026-09-21T10:00:00"
    assert doc["summary"] == "三张工作表的汇总数据"
    # doc_metadata 白名单：只保留 raw_payload（业务原始数据）
    assert doc["doc_metadata"] == {"raw_payload": {"rows": 3}}
    # SQL 必须查询了元数据相关列
    sql_called = mock_cursor.execute.call_args[0][0]
    for col in ("total_chunks", "summary", "created_at"):
        assert col in sql_called


async def test_kb_search_same_doc_multiple_chunks_doc_info_once():
    """同一文档命中多个片段时，文档级信息（含 raw_payload）只在 documents 出现
    一次，片段条目不携带文档级字段（否则 raw_payload ≤32KB 随片段数成倍进上下文）"""
    tool = _make_tool()
    mock_retriever = MagicMock()
    mock_retriever.retrieve = AsyncMock(return_value=[
        {"doc_id": 7, "chunk_index": 0, "text": "片段一", "score": 0.9, "metadata": {}},
        {"doc_id": 7, "chunk_index": 1, "text": "片段二", "score": 0.8, "metadata": {}},
        {"doc_id": 9, "chunk_index": 0, "text": "其他文档片段", "score": 0.7, "metadata": {}},
    ])
    tool._retriever = mock_retriever

    mock_cm, _ = _mock_db([
        {"id": 7, "title": "宏陶商品.json", "file_path": None, "source_type": "hongtao",
         "file_type": "json", "total_chunks": 2, "summary": None, "created_at": None,
         "metadata": json.dumps({"raw_payload": {"pics": ["https://x/1.jpg"]}})},
        {"id": 9, "title": "制度.docx", "file_path": "/y.docx", "source_type": "policy",
         "file_type": "docx", "total_chunks": 5, "summary": None, "created_at": None,
         "metadata": None},
    ])

    with patch("src.tools.knowledge.knowledge_base_tool.get_db_connection", return_value=mock_cm):
        result = await tool.execute(query="瓷砖", top_k=5)

    assert result["count"] == 3
    # 文档表按 doc_id 归并：7 命中两个片段但只出现一次
    assert set(result["documents"].keys()) == {"7", "9"}
    assert result["documents"]["7"]["doc_metadata"] == {
        "raw_payload": {"pics": ["https://x/1.jpg"]}}
    # 片段条目只含片段级字段
    for item in result["results"]:
        assert set(item.keys()) == {"text", "doc_id", "score", "chunk_index", "metadata"}
    assert len([i for i in result["results"] if i["doc_id"] == 7]) == 2


async def test_kb_search_doc_metadata_whitelist_raw_payload_only():
    """doc_metadata 白名单：只保留业务原始数据，系统内部痕迹（溯源 code/id、
    管线版本、入库时间、解析诊断、file_type/original_url 等）一律不进 LLM 上下文"""
    tool = _make_tool()
    mock_retriever = MagicMock()
    mock_retriever.retrieve = AsyncMock(return_value=[
        {"doc_id": 5, "chunk_index": 0, "text": "文章", "score": 0.9, "metadata": {}},
        {"doc_id": 8, "chunk_index": 0, "text": "文本", "score": 0.85, "metadata": {}},
        {"doc_id": 7, "chunk_index": 0, "text": "瓷砖", "score": 0.8, "metadata": {}},
    ])
    tool._retriever = mock_retriever

    mock_cm, _ = _mock_db([
        {"id": 5, "title": "公众号文章", "file_path": "https://mp.weixin.qq.com/s/abc",
         "file_type": "md", "total_chunks": 1, "summary": None, "created_at": None,
         "metadata": json.dumps({
             "original_url": "https://mp.weixin.qq.com/s/abc", "fetch_url": "https://x",
             "publish_time": "2026-09-01T08:00:00", "account_name": "某号",
             "sync_run_id": 42, "pipeline_version": "wp13", "ingested_at": "2026-09-01T09:00:00",
             "content_md": "# 标题", "list_source": {"aid": 1},
         })},  # 无白名单键（公众号文档）→ doc_metadata 为空对象
        {"id": 8, "title": "说明.txt", "file_path": "/storage/说明.txt",
         "file_type": "txt", "total_chunks": 1, "summary": None, "created_at": None,
         "metadata": json.dumps({
             "file_type": ".txt", "encoding": "utf-8",
             "source_code": "ht_mall", "native_id": 123, "run_id": "run_9",
             "pipeline_version": "v1.8", "ingested_at": "2026-09-20T00:00:00",
             "raw_payload": {"name": "瓷砖", "pics": ["https://x/1.jpg"], "sales": 30},
         })},
        # 宏陶专用模块（hts-render v1.9）：业务数据嵌套在 raw_payload，
        # 顶层只留 pipeline_version/sync_run_id/trace 等系统痕迹
        {"id": 7, "title": "8-TPG2680D046ABCD瑞峰", "file_path": "",
         "file_type": "markdown", "total_chunks": 1, "summary": None, "created_at": None,
         "metadata": json.dumps({
             "raw_payload": {
                 "pics": ["https://oss/p1.jpg"], "detail_images": ["https://oss/d1.jpg"],
                 "forum_media": [], "video": "", "listing_date": "2025-07-10",
                 "sales": "0", "stock": "1000", "comment_score": "5.0", "comment_num": "0",
                 "name": "瑞峰", "model": "8-TPG2680D046ABCD", "procode": "8-TPG2680D046ABCD",
                 "cid": "10", "sellpoint": "",
             },
             "pipeline_version": "hts-render-v3", "sync_run_id": 1242,
             "trace": {"native_id": "83", "content_hash": "abc", "ingested_at": "2026-09-22"},
         })},
    ])

    with patch("src.tools.knowledge.knowledge_base_tool.get_db_connection", return_value=mock_cm):
        result = await tool.execute(query="瓷砖", top_k=5)

    # 无业务键的文档：痕迹字段全部不输出
    assert result["documents"]["5"]["doc_metadata"] == {}
    # 通用 api-ingest 形态：仅 raw_payload，业务数据（图片链接/销量）完整可达
    assert result["documents"]["8"]["doc_metadata"] == {
        "raw_payload": {"name": "瓷砖", "pics": ["https://x/1.jpg"], "sales": 30}}
    # 宏陶 v1.9 形态：raw_payload 整包（含正文已有字段也整包保留，取数语义统一），
    # 顶层痕迹键不输出
    assert result["documents"]["7"]["doc_metadata"] == {"raw_payload": {
        "pics": ["https://oss/p1.jpg"], "detail_images": ["https://oss/d1.jpg"],
        "forum_media": [], "video": "", "listing_date": "2025-07-10",
        "sales": "0", "stock": "1000", "comment_score": "5.0", "comment_num": "0",
        "name": "瑞峰", "model": "8-TPG2680D046ABCD", "procode": "8-TPG2680D046ABCD",
        "cid": "10", "sellpoint": ""}}


async def test_kb_search_doc_metadata_invalid_json_returns_empty():
    """documents.metadata 为非法 JSON 时 doc_metadata 回退为空 dict，不影响主流程"""
    tool = _make_tool()
    mock_retriever = MagicMock()
    mock_retriever.retrieve = AsyncMock(return_value=[
        {"doc_id": 1, "chunk_index": 0, "text": "x", "score": 0.9, "metadata": {}},
    ])
    tool._retriever = mock_retriever

    mock_cm, _ = _mock_db([
        {"id": 1, "title": "t.txt", "file_path": None, "metadata": "not-json{",
         "total_chunks": None, "summary": None, "created_at": None,
         "source_type": None, "file_type": None},
    ])

    with patch("src.tools.knowledge.knowledge_base_tool.get_db_connection", return_value=mock_cm):
        result = await tool.execute(query="test")

    item = result["results"][0]
    doc = result["documents"]["1"]
    assert doc["doc_metadata"] == {}
    assert doc["total_chunks"] is None
    # 缺失 chunk_index 时兜底为第 1 块
    assert item["chunk_index"] == 1


async def test_kb_search_file_path_fallback_empty_when_none():
    """file_path 为 None 时回退为空字符串（row.get(...) or "" 兜底）"""
    tool = _make_tool()
    mock_retriever = MagicMock()
    mock_retriever.retrieve = AsyncMock(return_value=[
        {"doc_id": 2, "text": "x", "score": 0.5, "metadata": {}},
    ])
    tool._retriever = mock_retriever

    mock_cm, _ = _mock_db([{"id": 2, "title": "无路径.doc", "file_path": None}])

    with patch("src.tools.knowledge.knowledge_base_tool.get_db_connection", return_value=mock_cm):
        result = await tool.execute(query="test")

    assert result["success"] is True
    assert result["documents"]["2"]["file_path"] == ""


async def test_kb_search_empty_results():
    """无检索结果时正常返回空列表，不触发 DB 查询"""
    tool = _make_tool()
    mock_retriever = MagicMock()
    mock_retriever.retrieve = AsyncMock(return_value=[])
    tool._retriever = mock_retriever

    with patch("src.db.database.get_db_connection") as db_shared, \
            patch("src.tools.knowledge.knowledge_base_tool.get_db_connection") as db_tool:
        result = await tool.execute(query="不存在的内容")

    assert result["success"] is True
    assert result["count"] == 0
    assert result["results"] == []
    assert result["documents"] == {}
    # 无结果时不应查 DB（共享范围与标题回查两路都不触发）
    assert not db_shared.called
    assert not db_tool.called


async def test_kb_search_top_k_string_coerced_to_int():
    """LLM 以字符串传入 top_k（如 "10"）时强转为 int，避免 top_k*3 字符串拼接 + slice 报错"""
    tool = _make_tool()
    mock_retriever = MagicMock()
    mock_retriever.retrieve = AsyncMock(return_value=[
        {"doc_id": 1, "text": "x", "score": 0.9, "metadata": {}},
    ])
    tool._retriever = mock_retriever

    mock_cm, _ = _mock_db([{"id": 1, "title": "t.txt", "file_path": None}])

    with patch("src.tools.knowledge.knowledge_base_tool.get_db_connection", return_value=mock_cm):
        result = await tool.execute(query="test", top_k="10")

    assert result["success"] is True
    # retrieve 收到的是 int 10（而非字符串，top_k*3 才不会变成 "101010"）
    call_kwargs = mock_retriever.retrieve.call_args.kwargs
    assert call_kwargs["top_k"] == 10


async def test_kb_search_top_k_invalid_falls_back_to_default():
    """top_k 传无法转换的值时回退到默认 10"""
    tool = _make_tool()
    mock_retriever = MagicMock()
    mock_retriever.retrieve = AsyncMock(return_value=[])
    tool._retriever = mock_retriever

    with patch("src.db.database.get_db_connection"):
        result = await tool.execute(query="test", top_k="abc")

    assert result["success"] is True
    assert mock_retriever.retrieve.call_args.kwargs["top_k"] == 10


async def test_kb_search_returns_no_truncate_flag():
    """有结果时返回 _no_truncate=True，确保检索结果不被 agent 保头保尾截断"""
    tool = _make_tool()
    mock_retriever = MagicMock()
    mock_retriever.retrieve = AsyncMock(return_value=[
        {"doc_id": 1, "text": "x", "score": 0.9, "metadata": {}},
    ])
    tool._retriever = mock_retriever

    mock_cm, _ = _mock_db([{"id": 1, "title": "t.txt", "file_path": None}])

    with patch("src.tools.knowledge.knowledge_base_tool.get_db_connection", return_value=mock_cm):
        result = await tool.execute(query="test")

    assert result["success"] is True
    assert result["_no_truncate"] is True


# ============== _load_shared_ranges（共享检索范围构造） ==============


def _mock_db_single_row(row):
    """构造 fetchone 返回单个 dict 行的 DB 连接 mock"""
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = row
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_cm = MagicMock()
    mock_cm.__enter__ = MagicMock(return_value=mock_conn)
    mock_cm.__exit__ = MagicMock(return_value=None)
    return mock_cm


def test_load_shared_ranges_intersection():
    """启用清单 ∩ 租户级授权 -> 精确 (owner, source_type) 对；未授权项与无 owner 项被过滤"""
    tool = KnowledgeBaseTool()
    row = {
        "sources": [
            {"source_type": "industry", "display_name": "行业库", "owner_tenant_id": "A"},
            {"source_type": "tech", "display_name": "技术库", "owner_tenant_id": "A"},
            {"source_type": "product", "display_name": "商品库", "owner_tenant_id": "NOPE"},
            {"source_type": "self", "display_name": "本租户"},
        ],
        "share_owners": ["A"],
    }
    with patch("src.db.database.get_db_connection",
               return_value=_mock_db_single_row(row)):
        ranges = tool._load_shared_ranges("B", "subagent", None)

    assert ("A", "industry") in ranges
    assert ("A", "tech") in ranges
    # 未授权来源被交集过滤
    assert all(r[0] != "NOPE" for r in ranges)
    # 无 owner 的本租户项不算共享范围
    assert all(r[0] != "self" for r in ranges)


def test_load_shared_ranges_empty_when_no_auth():
    """启用清单有共享项但租户级授权为空 -> 空交集，共享项自动失效（无快照）"""
    tool = KnowledgeBaseTool()
    row = {
        "sources": [{"source_type": "industry", "display_name": "行业库", "owner_tenant_id": "A"}],
        "share_owners": [],
    }
    with patch("src.db.database.get_db_connection",
               return_value=_mock_db_single_row(row)):
        ranges = tool._load_shared_ranges("B", "subagent", None)
    assert ranges == []


def test_load_shared_ranges_source_type_filter():
    """传 source_type 时只保留该分类的共享项；不匹配则返回空"""
    tool = KnowledgeBaseTool()
    row = {
        "sources": [{"source_type": "industry", "display_name": "行业库", "owner_tenant_id": "A"}],
        "share_owners": ["A"],
    }
    with patch("src.db.database.get_db_connection",
               return_value=_mock_db_single_row(row)):
        assert tool._load_shared_ranges("B", "subagent", "industry") == [("A", "industry")]
        assert tool._load_shared_ranges("B", "subagent", "tech") == []


def test_load_shared_ranges_no_row_returns_empty():
    """查询无记录（未配置关联）时返回空"""
    tool = KnowledgeBaseTool()
    with patch("src.db.database.get_db_connection",
               return_value=_mock_db_single_row(None)):
        assert tool._load_shared_ranges("B", "subagent", None) == []


async def test_execute_master_agent_passes_empty_shared_ranges():
    """主智能体（无 subagent_id）不构造共享范围，retrieve 收到空 shared_ranges（行为与现状一致）"""
    tool = _make_tool()
    mock_retriever = MagicMock()
    mock_retriever.retrieve = AsyncMock(return_value=[])
    tool._retriever = mock_retriever

    with patch("src.db.database.get_db_connection") as db_mock:
        result = await tool.execute(query="test")

    assert result["success"] is True
    # 主智能体上下文无 tenant_id/subagent_id，不查共享范围表
    assert not db_mock.called
    assert mock_retriever.retrieve.call_args.kwargs.get("shared_ranges") == []


async def test_title_lookup_wraps_range_sql_in_parens():
    """标题回查 SQL：tenant_range 的 OR 组合必须整体加括号（WP2 P2 修复）。
    AND 优先级高于 OR，不加括号时 `id IN (...) AND a OR b` 中 id IN 只约束
    本租户分支，共享分支会回查到不在结果集内的文档标题（口径漂移）。"""
    from src.tools.context import ToolExecutionContext, tool_execution_scope

    tool = _make_tool()
    mock_retriever = MagicMock()
    mock_retriever.retrieve = AsyncMock(return_value=[
        {"doc_id": 1, "text": "片段", "score": 0.9, "metadata": {}},
    ])
    tool._retriever = mock_retriever

    mock_cm, mock_cursor = _mock_db([
        {"id": 1, "title": "文档", "file_path": "/x.txt", "tenant_id": "tenant_test1"},
    ])

    ctx = ToolExecutionContext(tenant_id="tenant_test1", subagent_id=None)
    with tool_execution_scope(ctx), \
            patch("src.tools.knowledge.knowledge_base_tool.get_db_connection", return_value=mock_cm):
        result = await tool.execute(query="报价模板", top_k=5)

    assert result["success"] is True
    sql = " ".join(mock_cursor.execute.call_args[0][0].split())
    assert "AND ((documents.tenant_id = %s))" in sql


# ---------------------------------------------------------------
# 栏目授权收口（resolve_category_scope 接入，2026-09-20）
# ---------------------------------------------------------------

def _auth_db_mock(sources):
    """构造返回给定 sources 配置行的 DB 连接 mock（fetchall 返回空 = 各栏目 0 文档）"""
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = {"sources": sources, "share_owners": []}
    mock_cursor.fetchall.return_value = []
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_cm = MagicMock()
    mock_cm.__enter__ = MagicMock(return_value=mock_conn)
    mock_cm.__exit__ = MagicMock(return_value=None)
    return mock_cm


async def test_kb_search_unauthorized_source_type_rejected():
    """栏目授权非空 + 传未授权栏目：拒绝并返回授权栏目清单（含文档数）"""
    from src.tools.context import ToolExecutionContext, tool_execution_scope

    tool = KnowledgeBaseTool()
    mock_retriever = MagicMock()
    mock_retriever.retrieve = AsyncMock()
    tool._retriever = mock_retriever

    mock_cm = _auth_db_mock([{"source_type": "数据分析", "owner_tenant_id": None}])
    ctx = ToolExecutionContext(tenant_id="tenant_test1", subagent_id="data-analysis")
    with tool_execution_scope(ctx), \
            patch("src.tools.knowledge.knowledge_base_tool.get_db_connection", return_value=mock_cm), \
            patch("src.db.database.get_db_connection", return_value=mock_cm):
        result = await tool.execute(query="x", source_type="hotel_resource")

    assert result["success"] is False
    assert result["authorized_categories"] == [
        {"source_type": "数据分析", "doc_count": 0},
    ]
    assert "hotel_resource" in result["note"]
    assert not mock_retriever.retrieve.called


async def test_kb_search_no_source_type_narrows_to_authorized():
    """栏目授权非空 + 未传 source_type：收窄为授权栏目集合（list）传给 retriever"""
    from src.tools.context import ToolExecutionContext, tool_execution_scope

    tool = KnowledgeBaseTool()
    mock_retriever = MagicMock()
    mock_retriever.retrieve = AsyncMock(return_value=[])
    tool._retriever = mock_retriever

    mock_cm = _auth_db_mock([{"source_type": "数据分析", "owner_tenant_id": None}])
    ctx = ToolExecutionContext(tenant_id="tenant_test1", subagent_id="data-analysis")
    with tool_execution_scope(ctx), \
            patch("src.tools.knowledge.knowledge_base_tool.get_db_connection", return_value=mock_cm), \
            patch("src.db.database.get_db_connection", return_value=mock_cm):
        result = await tool.execute(query="x")

    assert result["success"] is True
    call_kwargs = mock_retriever.retrieve.call_args.kwargs
    assert call_kwargs["source_type"] == ["数据分析"]
    # 收窄时共享侧不过滤栏目：load_shared_ranges 传 None
    assert call_kwargs["shared_ranges"] == []
