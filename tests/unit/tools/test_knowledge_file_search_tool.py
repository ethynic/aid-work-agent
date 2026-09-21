"""
知识库文件搜索工具单元测试

覆盖：工具薄壳（上下文读取、共享范围、传参容错、read_hint、0 命中引导）
与 service 层 search_documents_by_title（匹配模式、可见性 SQL、owner 标注）。
"""
from unittest.mock import MagicMock, patch

from src.tools.knowledge.knowledge_file_search_tool import KnowledgeFileSearchTool


def _make_service_rows(doc_id=1, title="数据分析助手·融合知识库.md", tenant_id="tenant_test1"):
    """构造 service 返回的 result dict"""
    return {
        "success": True,
        "results": [{
            "doc_id": doc_id,
            "title": title,
            "file_path": f"storage/tenants/{tenant_id}/knowledge/kb_xxx.md",
            "file_size": 25895,
            "file_type": "md",
            "source_type": "file",
            "summary": "测试摘要",
            "created_at": "2026-09-20 11:45:46",
            "metadata": {},
        }],
        "count": 1,
    }


# ============== 工具薄壳 ==============


async def test_file_search_returns_read_hint_and_no_truncate():
    """命中时每条结果附 read_hint，返回 _no_truncate=True"""
    tool = KnowledgeFileSearchTool()
    canned = _make_service_rows()
    with patch("src.knowledge.service.knowledge_service") as svc_mock:
        svc_mock.search_documents_by_title.return_value = canned
        result = await tool.execute(file_name="融合知识库")

    assert result["success"] is True
    assert result["count"] == 1
    assert result["_no_truncate"] is True
    assert "read(file_path=" in result["results"][0]["read_hint"]
    assert "kb_xxx.md" in result["results"][0]["read_hint"]


async def test_file_search_empty_name_returns_error():
    """file_name 为空/纯空白时直接报错，不调 service"""
    tool = KnowledgeFileSearchTool()
    with patch("src.knowledge.service.knowledge_service") as svc_mock:
        result = await tool.execute(file_name="   ")
    assert result["success"] is False
    assert not svc_mock.search_documents_by_title.called


async def test_file_search_zero_hits_has_redirect_note():
    """0 命中时返回引导文案，指路 knowledge_base_search（误用自愈闭环）"""
    tool = KnowledgeFileSearchTool()
    canned = {"success": True, "results": [], "count": 0}
    with patch("src.knowledge.service.knowledge_service") as svc_mock:
        svc_mock.search_documents_by_title.return_value = canned
        result = await tool.execute(file_name="不存在的文件")

    assert result["success"] is True
    assert result["count"] == 0
    assert "knowledge_base_search" in result["note"]


async def test_file_search_no_context_degrades_to_own_tenant():
    """无工具上下文（主智能体外调用）时共享范围为空、tenant_id 为空直接空结果"""
    tool = KnowledgeFileSearchTool()
    with patch("src.knowledge.service.knowledge_service") as svc_mock, \
            patch("src.db.database.get_db_connection") as db_mock:
        svc_mock.search_documents_by_title.return_value = {
            "success": True, "results": [], "count": 0,
        }
        result = await tool.execute(file_name="x")

    assert result["success"] is True
    call_kwargs = svc_mock.search_documents_by_title.call_args.kwargs
    assert call_kwargs["tenant_id"] is None
    assert call_kwargs["shared_ranges"] == []
    # 无租户上下文不查共享范围表
    assert not db_mock.called


async def test_file_search_subagent_loads_shared_ranges():
    """子智能体上下文：从 DB 读取共享范围并传给 service（模式 A）"""
    from src.tools.context import ToolExecutionContext, tool_execution_scope

    tool = KnowledgeFileSearchTool()
    row = {
        "sources": [
            {"source_type": "file", "display_name": "文件", "owner_tenant_id": "tenant_A"},
        ],
        "share_owners": ["tenant_A"],
    }
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = row
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_cm = MagicMock()
    mock_cm.__enter__ = MagicMock(return_value=mock_conn)
    mock_cm.__exit__ = MagicMock(return_value=None)

    ctx = ToolExecutionContext(
        tenant_id="tenant_test1", subagent_id="data-analysis",
    )
    with tool_execution_scope(ctx), \
            patch("src.db.database.get_db_connection", return_value=mock_cm), \
            patch("src.knowledge.service.knowledge_service") as svc_mock:
        svc_mock.search_documents_by_title.return_value = {
            "success": True, "results": [], "count": 0,
        }
        await tool.execute(file_name="融合知识库", source_type="file")

    call_kwargs = svc_mock.search_documents_by_title.call_args.kwargs
    assert call_kwargs["tenant_id"] == "tenant_test1"
    assert call_kwargs["shared_ranges"] == [("tenant_A", "file")]
    assert call_kwargs["source_type"] == "file"


async def test_file_search_master_agent_empty_shared_ranges():
    """主智能体（subagent_id 为空）退化为仅本租户，共享范围为空"""
    from src.tools.context import ToolExecutionContext, tool_execution_scope

    tool = KnowledgeFileSearchTool()
    ctx = ToolExecutionContext(tenant_id="tenant_test1", subagent_id=None)
    with tool_execution_scope(ctx), \
            patch("src.db.database.get_db_connection") as db_mock, \
            patch("src.knowledge.service.knowledge_service") as svc_mock:
        svc_mock.search_documents_by_title.return_value = {
            "success": True, "results": [], "count": 0,
        }
        await tool.execute(file_name="x")

    call_kwargs = svc_mock.search_documents_by_title.call_args.kwargs
    assert call_kwargs["shared_ranges"] == []
    assert not db_mock.called


async def test_file_search_exact_string_coerced_to_bool():
    """LLM 以字符串传 exact（"true"）时归一为 bool True"""
    from src.tools.context import ToolExecutionContext, tool_execution_scope

    tool = KnowledgeFileSearchTool()
    with patch("src.knowledge.service.knowledge_service") as svc_mock, \
            patch("src.db.database.get_db_connection"):
        svc_mock.search_documents_by_title.return_value = {
            "success": True, "results": [], "count": 0,
        }
        ctx = ToolExecutionContext(tenant_id="tenant_test1", subagent_id=None)
        with tool_execution_scope(ctx):
            await tool.execute(file_name="x", exact="true")

    assert svc_mock.search_documents_by_title.call_args.kwargs["exact"] is True


async def test_file_search_service_failure_passthrough():
    """service 失败结果原样透传（含 debug 字段），工具不再包一层"""
    tool = KnowledgeFileSearchTool()
    failed = {"success": False, "error": "搜索失败，请稍后重试", "debug": "x=***",
              "results": [], "count": 0}
    with patch("src.knowledge.service.knowledge_service") as svc_mock:
        svc_mock.search_documents_by_title.return_value = failed
        result = await tool.execute(file_name="x")

    assert result == failed


# ============== service 层 search_documents_by_title ==============


def _make_service():
    from src.knowledge.service import KnowledgeBaseService
    return KnowledgeBaseService()


def _mock_db_cursor(rows):
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = rows
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_cm = MagicMock()
    mock_cm.__enter__ = MagicMock(return_value=mock_conn)
    mock_cm.__exit__ = MagicMock(return_value=None)
    return mock_cm, mock_cursor


def _db_row(doc_id=1, tenant_id="tenant_test1", title="数据分析助手·融合知识库.md"):
    from datetime import datetime
    return {
        "id": doc_id,
        "title": title,
        "file_path": f"storage/tenants/{tenant_id}/knowledge/kb_xxx.md",
        "file_size": 25895,
        "file_type": "md",
        "source_type": "file",
        "summary": "测试摘要",
        "created_at": datetime(2026, 9, 20, 11, 45, 46),
        "tenant_id": tenant_id,
    }


def test_service_fuzzy_match_sql_shape():
    """模糊模式：ILIKE 包含匹配 + 分档排序 + 范围/active 条件成对出现"""
    from src.knowledge.retriever.tenant_range import (
        build_active_document_condition, build_tenant_range_conditions,
    )

    svc = _make_service()
    mock_cm, mock_cursor = _mock_db_cursor([_db_row()])
    with patch.object(svc, "_get_db_connection", return_value=mock_cm):
        result = svc.search_documents_by_title(
            tenant_id="tenant_test1", file_name="融合知识库",
            shared_ranges=[("tenant_A", "file")],
        )

    assert result["success"] is True
    sql = " ".join(mock_cursor.execute.call_args[0][0].split())
    assert "ILIKE %s" in sql
    assert "regexp_replace" not in sql
    assert "CASE WHEN d.title = %s" in sql
    # 租户范围 OR 组合必须整体加括号（AND 优先级高于 OR）
    assert f"({build_tenant_range_conditions('tenant_test1', None, [('tenant_A', 'file')], alias='d')[0]})" in sql
    assert build_active_document_condition(alias="d") in sql

    params = mock_cursor.execute.call_args[0][1]
    assert "%融合知识库%" in params
    assert "tenant_test1" in params and "tenant_A" in params


def test_service_exact_match_uses_stem_expr():
    """精确模式：title 全等 + 去扩展名（regexp_replace）比对"""
    svc = _make_service()
    mock_cm, mock_cursor = _mock_db_cursor([])
    with patch.object(svc, "_get_db_connection", return_value=mock_cm):
        result = svc.search_documents_by_title(
            tenant_id="tenant_test1", file_name="融合知识库", exact=True,
        )

    assert result["success"] is True and result["count"] == 0
    sql = " ".join(mock_cursor.execute.call_args[0][0].split())
    assert "regexp_replace(d.title" in sql
    assert "ILIKE" not in sql
    params = mock_cursor.execute.call_args[0][1]
    assert params.count("融合知识库") >= 2


def test_service_empty_inputs_skip_db():
    """file_name 为空或无租户上下文时返回空结果，不触发 DB 查询"""
    svc = _make_service()
    with patch.object(svc, "_get_db_connection") as conn_mock:
        r1 = svc.search_documents_by_title(tenant_id="t1", file_name="  ")
        r2 = svc.search_documents_by_title(tenant_id=None, file_name="x")
    assert r1 == {"success": True, "results": [], "count": 0}
    assert r2 == {"success": True, "results": [], "count": 0}
    assert not conn_mock.called


def test_service_formats_row_and_owner_metadata():
    """行格式化：created_at 转字符串；共享来源文档带 owner_tenant_id 标注"""
    svc = _make_service()
    mock_cm, mock_cursor = _mock_db_cursor([
        _db_row(doc_id=7, tenant_id="tenant_A"),
    ])
    with patch.object(svc, "_get_db_connection", return_value=mock_cm):
        result = svc.search_documents_by_title(
            tenant_id="tenant_test1", file_name="x",
            shared_ranges=[("tenant_A", "file")],
        )

    item = result["results"][0]
    assert item["doc_id"] == 7
    assert item["created_at"] == "2026-09-20 11:45:46"
    assert item["metadata"]["owner_tenant_id"] == "tenant_A"


def test_service_own_tenant_no_owner_tag():
    """本租户文档不带 owner 标注（行为与 knowledge_base_search 一致）"""
    svc = _make_service()
    mock_cm, mock_cursor = _mock_db_cursor([_db_row(tenant_id="tenant_test1")])
    with patch.object(svc, "_get_db_connection", return_value=mock_cm):
        result = svc.search_documents_by_title(
            tenant_id="tenant_test1", file_name="x",
        )
    assert "owner_tenant_id" not in result["results"][0]["metadata"]


def test_service_db_failure_returns_sanitized_error():
    """DB 异常时 success=False，debug 过滤敏感信息"""
    svc = _make_service()
    mock_cm = MagicMock()
    mock_cm.__enter__ = MagicMock(side_effect=Exception("password=secret123 boom"))
    mock_cm.__exit__ = MagicMock(return_value=None)
    with patch.object(svc, "_get_db_connection", return_value=mock_cm):
        result = svc.search_documents_by_title(tenant_id="t1", file_name="x")

    assert result["success"] is False
    assert "secret123" not in result["debug"]


def test_service_fuzzy_escapes_like_wildcards():
    """模糊模式：file_name 中的 % _ \\ 按字面量转义（防匹配放大与尾反斜杠报错）"""
    svc = _make_service()
    mock_cm, mock_cursor = _mock_db_cursor([])
    with patch.object(svc, "_get_db_connection", return_value=mock_cm):
        svc.search_documents_by_title(tenant_id="t1", file_name="100%_折扣\\")

    params = mock_cursor.execute.call_args[0][1]
    assert "%100\\%\\_折扣\\\\%" in params
    assert "100\\%\\_折扣\\\\%" in params
    # 精确分档的 = 比较仍用原始值（= 不做通配符展开）
    assert "100%_折扣\\" in params


def test_service_limit_clamped():
    """limit 越界收敛到 1~50；非法值回退 10"""
    svc = _make_service()
    mock_cm, mock_cursor = _mock_db_cursor([])
    with patch.object(svc, "_get_db_connection", return_value=mock_cm):
        for limit, expected in ((999, 50), (0, 1), ("abc", 10)):
            svc.search_documents_by_title(
                tenant_id="t1", file_name="x", limit=limit,
            )
            params = mock_cursor.execute.call_args[0][1]
            assert params[-1] == expected


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


async def test_file_search_unauthorized_source_type_rejected():
    """栏目授权非空 + 传未授权栏目：拒绝并返回授权栏目清单（含文档数）"""
    from src.tools.context import ToolExecutionContext, tool_execution_scope

    tool = KnowledgeFileSearchTool()
    mock_cm = _auth_db_mock([{"source_type": "数据分析", "owner_tenant_id": None}])
    ctx = ToolExecutionContext(tenant_id="tenant_test1", subagent_id="data-analysis")
    with tool_execution_scope(ctx), \
            patch("src.db.database.get_db_connection", return_value=mock_cm), \
            patch("src.knowledge.service.knowledge_service") as svc_mock:
        result = await tool.execute(file_name="x", source_type="hotel_resource")

    assert result["success"] is False
    assert result["authorized_categories"] == [
        {"source_type": "数据分析", "doc_count": 0},
    ]
    assert "hotel_resource" in result["note"]
    assert not svc_mock.search_documents_by_title.called


async def test_file_search_no_source_type_narrows_to_authorized():
    """栏目授权非空 + 未传 source_type：收窄为授权栏目集合（list）传给 service"""
    from src.tools.context import ToolExecutionContext, tool_execution_scope

    tool = KnowledgeFileSearchTool()
    mock_cm = _auth_db_mock([{"source_type": "数据分析", "owner_tenant_id": None}])
    ctx = ToolExecutionContext(tenant_id="tenant_test1", subagent_id="data-analysis")
    with tool_execution_scope(ctx), \
            patch("src.db.database.get_db_connection", return_value=mock_cm), \
            patch("src.knowledge.service.knowledge_service") as svc_mock:
        svc_mock.search_documents_by_title.return_value = {
            "success": True, "results": [], "count": 0,
        }
        await tool.execute(file_name="x")

    call_kwargs = svc_mock.search_documents_by_title.call_args.kwargs
    assert call_kwargs["source_type"] == ["数据分析"]
    # 收窄时共享侧不过滤栏目：load_shared_ranges 传 None
    assert call_kwargs["shared_ranges"] == []


async def test_file_search_authorized_source_type_passthrough():
    """栏目授权非空 + 传授权内栏目：正常透传"""
    from src.tools.context import ToolExecutionContext, tool_execution_scope

    tool = KnowledgeFileSearchTool()
    mock_cm = _auth_db_mock([{"source_type": "数据分析", "owner_tenant_id": None}])
    ctx = ToolExecutionContext(tenant_id="tenant_test1", subagent_id="data-analysis")
    with tool_execution_scope(ctx), \
            patch("src.db.database.get_db_connection", return_value=mock_cm), \
            patch("src.knowledge.service.knowledge_service") as svc_mock:
        svc_mock.search_documents_by_title.return_value = {
            "success": True, "results": [], "count": 0,
        }
        await tool.execute(file_name="x", source_type="数据分析")

    assert svc_mock.search_documents_by_title.call_args.kwargs["source_type"] == "数据分析"
