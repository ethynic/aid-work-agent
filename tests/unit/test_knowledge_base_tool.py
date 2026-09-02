"""
知识库检索工具单元测试

验证 execute() 返回结构包含 doc_id 和 file_path（源文件来源）。
"""
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
    assert item["doc_title"] == "报价模板.xlsx"
    assert item["file_path"] == "storage/tenants/tenant_test1/knowledge/kb_xxx.xlsx"
    assert item["text"] == "报价模板内容"

    # 关键：SQL 必须查询了 file_path 列
    sql_called = mock_cursor.execute.call_args[0][0]
    assert "file_path" in sql_called


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
    assert result["results"][0]["file_path"] == ""


async def test_kb_search_empty_results():
    """无检索结果时正常返回空列表，不触发 DB 查询"""
    tool = _make_tool()
    mock_retriever = MagicMock()
    mock_retriever.retrieve = AsyncMock(return_value=[])
    tool._retriever = mock_retriever

    with patch("src.tools.knowledge.knowledge_base_tool.get_db_connection") as db_mock:
        result = await tool.execute(query="不存在的内容")

    assert result["success"] is True
    assert result["count"] == 0
    assert result["results"] == []
    # 无结果时不应查 DB
    assert not db_mock.called


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

    with patch("src.tools.knowledge.knowledge_base_tool.get_db_connection"):
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
    with patch("src.tools.knowledge.knowledge_base_tool.get_db_connection",
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
    with patch("src.tools.knowledge.knowledge_base_tool.get_db_connection",
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
    with patch("src.tools.knowledge.knowledge_base_tool.get_db_connection",
               return_value=_mock_db_single_row(row)):
        assert tool._load_shared_ranges("B", "subagent", "industry") == [("A", "industry")]
        assert tool._load_shared_ranges("B", "subagent", "tech") == []


def test_load_shared_ranges_no_row_returns_empty():
    """查询无记录（未配置关联）时返回空"""
    tool = KnowledgeBaseTool()
    with patch("src.tools.knowledge.knowledge_base_tool.get_db_connection",
               return_value=_mock_db_single_row(None)):
        assert tool._load_shared_ranges("B", "subagent", None) == []


async def test_execute_master_agent_passes_empty_shared_ranges():
    """主智能体（无 subagent_id）不构造共享范围，retrieve 收到空 shared_ranges（行为与现状一致）"""
    tool = _make_tool()
    mock_retriever = MagicMock()
    mock_retriever.retrieve = AsyncMock(return_value=[])
    tool._retriever = mock_retriever

    with patch("src.tools.knowledge.knowledge_base_tool.get_db_connection") as db_mock:
        result = await tool.execute(query="test")

    assert result["success"] is True
    # 主智能体上下文无 tenant_id/subagent_id，不查共享范围表
    assert not db_mock.called
    assert mock_retriever.retrieve.call_args.kwargs.get("shared_ranges") == []
